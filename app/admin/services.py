"""Librarian features on BOOK-level state (FE librarian/Requests.tsx, Budget.tsx, MyPage.tsx).

A "row" is one book with at least one request. Filtering/sorting mirror the FE exactly and run in
Python on the loaded rows (school-sized data); the DB only holds the state.
"""
import math
import re
from datetime import date, datetime, timedelta
from sqlalchemy import func, select, update
from sqlalchemy.orm import joinedload
from app.auth.models import User, now
from app.books import service as book_service
from app.books.isbn import normalize_isbn
from app.books.models import BookRequest, Notification, RequestedBook, ReviewLog
from app.db import db
from app.serializers import KST, book_out, kst, kst_now, ms, request_out, requester_out, review_out, to_utc_naive

STATUSES = ('pending', 'approved', 'rejected', 'purchased')
EXPECTED_STATUSES = ('pending', 'approved', 'rejected')
LABEL = {'pending': '대기', 'approved': '승인', 'rejected': '반려', 'purchased': '구매완료'}
ALLOWED_FROM = {'approved': 'pending', 'rejected': 'pending', 'purchased': 'approved'}
REVERT_TO = {'approved': 'pending', 'rejected': 'pending', 'purchased': 'approved'}
PAGE_SIZE = 6
INT_MAX = 2147483647
FILTER_MESSAGE = '검색 조건을 확인해 주세요.'
REASON_MESSAGE = '신청한 학생에게 전달할 반려 사유를 입력해 주세요.'
NOTIFY_TEXT = {'approved': '‘{t}’ 신청이 승인되었어요.', 'purchased': '‘{t}’ 구매가 완료되어 대출할 수 있어요.',
               'rejected': '‘{t}’ 신청이 반려되었어요.'}


class APIError(Exception):
    def __init__(self, code='VALIDATION_ERROR', status=400, message=FILTER_MESSAGE, fields=None):
        self.code, self.status, self.message, self.fields = code, status, message, fields


def not_found():
    return APIError('NOT_FOUND', 404, '도서를 찾을 수 없어요.')


# ── rows ──────────────────────────────────────────────────────────────

def load_rows():
    requests = db.session.scalars(select(BookRequest).options(joinedload(BookRequest.book), joinedload(BookRequest.user))
                                  .order_by(BookRequest.requested_at.desc(), BookRequest.id.desc())).all()
    grouped = {}
    for item in requests:
        grouped.setdefault(item.requested_book_id, []).append(item)
    rows = []
    for items in grouped.values():
        rows.append({'book': items[0].book, 'requests': items, 'requesters': [i.user for i in items],
                     'latest': items[0].requested_at, 'first': items[-1].requested_at})
    rows.sort(key=lambda r: (-ms(r['latest']), r['book'].id))
    return rows


def row_out(row):
    book = row['book']
    return {'book': book_out(book_service.row_meta(book), book_service.is_owned(book.isbn, book.status)), 'review': review_out(book),
            'requests': [request_out(r) for r in row['requests']], 'requesters': [requester_out(u) for u in row['requesters']],
            'latestAt': ms(row['latest']), 'firstAt': ms(row['first'])}


# ── filters (FE matches()) ────────────────────────────────────────────

def blank(value):
    return value is None or value == ''


def day_bound(value, end=False):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise APIError()
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise APIError() from None
    try:
        # End bound = next midnight (exclusive). Dates at the edge of the calendar overflow: treat as a bad filter.
        start = datetime(day.year, day.month, day.day, tzinfo=KST) + (timedelta(days=1) if end else timedelta())
        return to_utc_naive(start)
    except (OverflowError, ValueError):
        raise APIError() from None


def parse_filters(source):
    """source: request.args or a JSON body. Blank or 'all' means no filter."""
    def get(key):
        value = source.get(key)
        return None if blank(value) else value
    f = {}
    status = get('status')
    f['status'] = None if status in (None, 'all') else status
    if f['status'] is not None and f['status'] not in STATUSES:
        raise APIError()
    class_no = get('classNo')
    f['class_no'] = None if class_no in (None, 'all') else class_no
    if f['class_no'] is not None and not isinstance(f['class_no'], str):
        raise APIError()
    start, end = get('from'), get('to')
    f['start'] = day_bound(start) if start is not None else None
    f['end'] = day_bound(end, True) if end is not None else None
    if f['start'] is not None and f['end'] is not None and f['start'] >= f['end']:
        raise APIError()
    q = get('q')
    if q is not None and not isinstance(q, str):
        raise APIError()
    f['q'] = (q or '').strip().lower()
    sort = get('sort') or 'latest'
    if sort not in ('latest', 'count', 'title'):
        raise APIError()
    f['sort'] = sort
    return f


def matches(row, f):
    book = row['book']
    if f['status'] and book.status != f['status']:
        return False
    if f['class_no'] and not any(u.grade_class == f['class_no'] for u in row['requesters']):
        return False
    if f['start'] is not None or f['end'] is not None:
        if not any((f['start'] is None or r.requested_at >= f['start']) and (f['end'] is None or r.requested_at < f['end']) for r in row['requests']):
            return False
    if not f['q']:
        return True
    parts = [book.title, book.author]
    for u in row['requesters']:
        parts += [u.name, u.student_no or '']
    return f['q'] in ' '.join(parts).lower()


def title_key(row):
    title = row['book'].title
    return (title.casefold(), title.swapcase())


def filtered(f, loaded=None):
    """loaded: rows already read by the caller, so one request reads the DB once."""
    rows = [r for r in (load_rows() if loaded is None else loaded) if matches(r, f)]
    if f['sort'] == 'count':
        rows.sort(key=lambda r: -len(r['requests']))  # stable: ties keep latest-first
    elif f['sort'] == 'title':
        rows.sort(key=title_key)
    return rows


def paging(args):
    values = []
    for key, default, maximum in (('page', '1', 10**9), ('pageSize', str(PAGE_SIZE), 100)):
        raw = args.get(key, default)
        if not re.fullmatch(r'[0-9]{1,10}', raw) or not 1 <= int(raw) <= maximum:
            raise APIError()
        values.append(int(raw))
    return values


def list_books(args):
    f = parse_filters(args)
    page, size = paging(args)
    everything = load_rows()
    rows = filtered(f, everything)
    total = len(rows)
    page = min(page, max(1, math.ceil(total / size)))
    summary = {'total': len(everything), **{s: sum(1 for r in everything if r['book'].status == s) for s in STATUSES}}
    classes = sorted({c for (c,) in db.session.execute(select(User.grade_class).where(User.grade_class.is_not(None)).distinct())})
    return {'items': [row_out(r) for r in rows[(page - 1) * size:page * size]], 'page': page, 'pageSize': size,
            'total': total, 'summary': summary, 'classOptions': classes}


def export_books(body):
    if not isinstance(body, dict):
        raise APIError()
    f = parse_filters(body)
    rows = filtered(f)
    out = []
    for r in rows:
        book = r['book']
        review = 'approved' if book.status == 'purchased' else book.status
        purchase = LABEL['purchased'] if book.status == 'purchased' else '미구매' if book.status == 'approved' else '-'
        students = ', '.join(f'{u.name}({u.grade_class or "-"})' for u in r['requesters'])
        out.append([book.title, book.author, book.publisher, book.isbn, book.price, len(r['requests']), students,
                    kst(r['latest']).strftime('%Y.%m.%d'), LABEL[review], purchase, book.reject_reason or ''])
    return out


def school_order_rows(body):
    # Only approved, unpurchased books with a positive purchase quantity are orderable.
    filters = parse_filters({**body, 'status': 'approved'})
    books = [row['book'] for row in filtered(filters) if row['book'].quantity > 0]
    if any(book.price is None for book in books):
        raise APIError('PRICE_REQUIRED', 409, '발주할 도서의 정가를 입력해 주세요.')
    return [(book.title, book.author, book.publisher, book.price, book.quantity) for book in books]


# ── status changes ────────────────────────────────────────────────────

def isbn_list(body, minimum=1):
    ids = body.get('bookIds') if isinstance(body, dict) else None
    if not isinstance(ids, list) or not minimum <= len(ids) <= 1000:
        raise APIError()
    try:
        return list(dict.fromkeys(normalize_isbn(i) for i in ids))
    except ValueError:
        raise APIError() from None


def locked_row(isbn):
    # populate_existing: after waiting for the lock, use the latest committed values, not an earlier snapshot.
    row = db.session.execute(select(RequestedBook).where(RequestedBook.isbn == isbn).with_for_update()
                             .execution_options(populate_existing=True)).scalar_one_or_none()
    if row is None:
        return None
    # A plain COUNT(*) would read the transaction's snapshot (REPEATABLE READ); a locking read sees the latest commits.
    has_requests = db.session.execute(select(BookRequest.id).where(BookRequest.requested_book_id == row.id).limit(1).with_for_update()).first()
    return row if has_requests else None


def notification_message(status, title, reason):
    text = NOTIFY_TEXT[status].format(t=title)
    if status == 'rejected' and reason:
        text += f' (사유: {reason})'
    return text[:255]


def change_status(body, actor_id):
    ids = isbn_list(body)
    status = body.get('status')
    if not isinstance(status, str) or status not in ALLOWED_FROM:
        raise APIError()
    reason = body.get('reason')
    if status == 'rejected':
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise APIError(message=REASON_MESSAGE)
        reason = reason.strip()
    else:
        reason = None
    changed = {}
    # Lock books in ISBN order whatever order the client sent, so [A,B] and [B,A] cannot deadlock.
    for isbn in sorted(ids):
        row = locked_row(isbn)
        # Books that cannot make this move are skipped, not errors (FE setStatus).
        if row is None or row.status != ALLOWED_FROM[status]:
            continue
        stamp = now()  # taken after the lock and the transition check
        result = db.session.execute(update(RequestedBook).where(RequestedBook.id == row.id, RequestedBook.status == ALLOWED_FROM[status])
                                    .values(status=status, reject_reason=reason, updated_at=stamp).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            continue
        db.session.expire(row)
        db.session.add(ReviewLog(requested_book_id=row.id, status=status, actor_id=actor_id, created_at=stamp))
        # Locking read: requesters added after this transaction's snapshot must be notified too.
        for request in db.session.scalars(select(BookRequest).where(BookRequest.requested_book_id == row.id).order_by(BookRequest.id).with_for_update()):
            db.session.add(Notification(user_id=request.user_id, request_id=request.id, requested_book_id=row.id, type=status, reason=reason,
                                        message=notification_message(status, row.title, reason), created_at=stamp))
        changed[isbn] = row
    db.session.flush()
    items = [review_out(changed[i]) for i in ids if i in changed]  # response keeps the request order
    db.session.commit()
    return {'changed': len(items), 'items': items}


def revert_status(body):
    ids = isbn_list(body)
    changed = {}
    for isbn in sorted(ids):  # same lock order as change_status
        row = locked_row(isbn)
        if row is None or row.status == 'pending':
            continue
        stamp = now()
        previous = row.status
        result = db.session.execute(update(RequestedBook).where(RequestedBook.id == row.id, RequestedBook.status == previous)
                                    .values(status=REVERT_TO[previous], reject_reason=None, updated_at=stamp).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            continue
        db.session.expire(row)
        changed[isbn] = row  # no notification and no log: reverts are silent in the FE
    db.session.flush()
    items = [review_out(changed[i]) for i in ids if i in changed]
    db.session.commit()
    return {'changed': len(items), 'items': items}


def set_price(book_id, body):
    if not isinstance(body, dict) or set(body) != {'price'}:
        raise APIError()
    price = body['price']
    if price is not None and (type(price) is not int or not 0 <= price <= INT_MAX):
        raise APIError(message='가격을 확인해 주세요.')
    row = target_row(book_id)
    row.price = price
    row.updated_at = now()
    db.session.flush()
    data = book_out(book_service.row_meta(row), book_service.is_owned(row.isbn, row.status))
    db.session.commit()
    return data


def set_quantity(book_id, body):
    if not isinstance(body, dict) or set(body) != {'quantity'}:
        raise APIError()
    quantity = body['quantity']
    # Integers are clamped directly (no float conversion); finiteness and floor only apply to floats.
    if type(quantity) is int:
        whole = quantity
    elif type(quantity) is float and math.isfinite(quantity):
        whole = math.floor(quantity)
    else:
        raise APIError(message='수량을 확인해 주세요.')
    row = target_row(book_id)
    row.quantity = min(max(0, whole), INT_MAX)
    row.updated_at = now()
    db.session.flush()
    data = review_out(row)
    db.session.commit()
    return data


def target_row(book_id):
    try:
        isbn = normalize_isbn(book_id)
    except ValueError:
        raise not_found() from None
    row = locked_row(isbn)
    if row is None:
        raise not_found()
    return row


# ── budget ────────────────────────────────────────────────────────────

def parse_budget(source):
    period = source.get('period') or 'semester'
    if period not in ('semester', 'year', 'all'):
        raise APIError()
    include = source.get('include')
    if include is None:
        include = ['pending', 'approved']
    elif isinstance(include, str):
        include = [i for i in include.split(',') if i]
    if not isinstance(include, list) or any(i not in EXPECTED_STATUSES for i in include):
        raise APIError()
    return period, [s for s in EXPECTED_STATUSES if s in include]


def period_start(period, today):
    """FE periodStart(): semesters start in March and September (Jan-Feb belong to last year's autumn)."""
    if period == 'all':
        return None
    if period == 'year':
        return to_utc_naive(datetime(today.year, 1, 1, tzinfo=KST))
    month = today.month
    if month >= 9:
        start = datetime(today.year, 9, 1, tzinfo=KST)
    elif month >= 3:
        start = datetime(today.year, 3, 1, tzinfo=KST)
    else:
        start = datetime(today.year - 1, 9, 1, tzinfo=KST)
    return to_utc_naive(start)


def subtotal(book):
    return None if book.price is None else book.price * book.quantity


def amount(rows):
    return sum((r['book'].price or 0) * r['book'].quantity for r in rows)


def budget_row(r):
    book = r['book']
    return {'book': book_out(book_service.row_meta(book), book_service.is_owned(book.isbn, book.status)), 'review': review_out(book),
            'subtotal': subtotal(book), 'latestAt': ms(r['latest']), 'firstAt': ms(r['first'])}


def budget(source, today=None):
    period, include = parse_budget(source)
    today = today or kst_now()
    everything = load_rows()
    start = period_start(period, today)
    in_period = [r for r in everything if start is None or r['latest'] >= start]
    expected = [r for r in in_period if r['book'].status in include]
    purchased = [r for r in in_period if r['book'].status == 'purchased']
    monthly_rows = [r for r in everything if r['book'].status in include or r['book'].status == 'purchased']
    monthly = []
    for i in range(6):
        index = today.year * 12 + today.month - 1 - (5 - i)
        year, month = divmod(index, 12)
        begin = datetime(year, month + 1, 1, tzinfo=KST)
        following = datetime(year + (month + 1) // 12, (month + 1) % 12 + 1, 1, tzinfo=KST)
        inside = [r for r in monthly_rows if to_utc_naive(begin) <= r['first'] < to_utc_naive(following)]
        monthly.append({'label': f'{month + 1}월', 'amount': amount(inside), 'books': len(inside)})
    return {'period': period, 'include': include, 'expected': [budget_row(r) for r in expected], 'purchased': [budget_row(r) for r in purchased],
            'expectedTotal': amount(expected), 'purchasedTotal': amount(purchased),
            'statusTotals': {s: amount([r for r in in_period if r['book'].status == s]) for s in STATUSES},
            'missingPriceCount': sum(1 for r in expected if r['book'].price is None), 'monthly': monthly}


def budget_export(body):
    if not isinstance(body, dict):
        raise APIError()
    data = budget(body)
    def cells(kind, entry):
        book, review = entry['book'], entry['review']
        return [kind, book['title'], LABEL[review['status']], book['price'], review['quantity'], entry['subtotal']]
    rows = [cells('예상', e) for e in data['expected']] + [cells('구매완료', e) for e in data['purchased']]
    rows.append(['예상 예산 합계', None, None, None, None, data['expectedTotal']])
    rows.append(['구매완료 합계', None, None, None, None, data['purchasedTotal']])
    return rows


# ── summary ───────────────────────────────────────────────────────────

def week_start(today):
    monday = today.date() - timedelta(days=today.weekday())
    return to_utc_naive(datetime(monday.year, monday.month, monday.day, tzinfo=KST))


def summary(today=None):
    today = today or kst_now()
    rows = load_rows()
    since = week_start(today)
    def week(status):
        return db.session.scalar(select(func.count(ReviewLog.id)).where(ReviewLog.status == status, ReviewLog.created_at >= since))
    logs = db.session.scalars(select(ReviewLog).options(joinedload(ReviewLog.book)).order_by(ReviewLog.created_at.desc(), ReviewLog.id.desc()).limit(8)).all()
    return {'pending': sum(1 for r in rows if r['book'].status == 'pending'), 'weekApproved': week('approved'), 'weekRejected': week('rejected'),
            'purchasedTotal': sum(1 for r in rows if r['book'].status == 'purchased'),
            'recentLogs': [{'id': str(log.id), 'at': ms(log.created_at), 'bookId': log.book.isbn if log.book else None,
                            'bookTitle': log.book.title if log.book else '삭제된 도서', 'status': log.status} for log in logs]}
