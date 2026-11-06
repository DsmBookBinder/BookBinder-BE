"""Validation and transactions for the supplied request-level admin contract."""
import re
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, update
from sqlalchemy.orm import joinedload
from app.db import db
from app.auth.models import User, now
from app.books.models import BookRequest, Notification

KST = timezone(timedelta(hours=9))
STATES = {'PENDING', 'APPROVED', 'REJECTED', 'PURCHASED'}


class APIError(Exception):
    def __init__(self, code='VALIDATION_ERROR', status=400):
        self.code, self.status = code, status


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,19}', value):
        raise APIError()
    number = int(value)
    if not 1 <= number <= 9223372036854775807:
        raise APIError()
    return number


def timestamp(value):
    return value.isoformat(timespec='seconds') + 'Z' if value else None


def state_item(item):
    return dict(id=str(item.id), status=item.status, reject_reason=item.reject_reason,
                processed_by=str(item.processed_by) if item.processed_by else None,
                processed_at=timestamp(item.processed_at))


def date_bound(value, end=False):
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise APIError()
    try:
        date = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=KST)
        if end:
            date += timedelta(days=1)
        return date.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        raise APIError() from None


def list_requests(args):
    values = []
    for key, default in [('page', '1'), ('page_size', '20')]:
        raw = args.get(key, default)
        if not re.fullmatch(r'[0-9]{1,10}', raw):
            raise APIError()
        values.append(int(raw))
    page, size = values
    if page < 1 or not 1 <= size <= 100:
        raise APIError()
    query = select(BookRequest).join(User, BookRequest.user_id == User.id)
    if 'status' in args:
        if args['status'] not in STATES:
            raise APIError()
        query = query.where(BookRequest.status == args['status'])
    start = date_bound(args['from']) if 'from' in args else None
    end = date_bound(args['to'], True) if 'to' in args else None
    if start is not None and end is not None and start >= end:
        raise APIError()
    if start is not None:
        query = query.where(BookRequest.requested_at >= start)
    if end is not None:
        query = query.where(BookRequest.requested_at < end)
    if 'student_no' in args:
        query = query.where(User.student_no == args['student_no'])
    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.session.scalars(query.options(joinedload(BookRequest.book), joinedload(BookRequest.user))
        .order_by(BookRequest.requested_at.desc(), BookRequest.id.desc())
        .offset((page - 1) * size).limit(size)).all()
    items = []
    for row in rows:
        item = state_item(row)
        item.update(user={key: str(row.user.id) if key == 'id' else getattr(row.user, key)
                          for key in ('id', 'name', 'grade_class', 'student_no')},
                    **{key: getattr(row.book, key) for key in ('isbn', 'title', 'author', 'publisher')},
                    price=row.price, requested_at=timestamp(row.requested_at))
        items.append(item)
    return dict(items=items, page=page, page_size=size, total=total)


def change_status(request_id, body, actor):
    rid = identifier(request_id)
    if not isinstance(body, dict) or not {'status', 'expected_status'} <= body.keys():
        raise APIError()
    target, expected = body['status'], body['expected_status']
    if not isinstance(target, str) or not isinstance(expected, str) or target not in STATES or expected not in STATES:
        raise APIError()
    reason = body.get('reason')
    if target == 'REJECTED':
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise APIError()
        reason = reason.strip()
    else:
        if reason is not None and not isinstance(reason, str):
            raise APIError()
        reason = None
    row = db.session.get(BookRequest, rid)
    if row is None:
        raise APIError('NOT_FOUND', 404)
    if (expected, target) not in {('PENDING', 'APPROVED'), ('PENDING', 'REJECTED'), ('APPROVED', 'PURCHASED')}:
        raise APIError('STATE_CONFLICT', 409)
    processed = now()
    result = db.session.execute(update(BookRequest).where(BookRequest.id == rid, BookRequest.status == expected)
        .values(status=target, reject_reason=reason, processed_by=actor, processed_at=processed)
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise APIError('STATE_CONFLICT', 409)
    db.session.add(Notification(user_id=row.user_id, request_id=rid, type=target.lower(),
                                message={'APPROVED': '도서 신청이 승인되었습니다.', 'REJECTED': '도서 신청이 반려되었습니다.',
                                         'PURCHASED': '신청 도서의 구매가 완료되었습니다.'}[target]))
    db.session.flush()
    db.session.refresh(row)
    response = state_item(row)
    db.session.commit()
    return response


def export_rows(body):
    if not isinstance(body, dict) or not isinstance(body.get('request_ids'), list) or type(body.get('group_by_isbn')) is not bool:
        raise APIError()
    ids = list(dict.fromkeys(identifier(value) for value in body['request_ids']))
    if not ids:
        raise APIError()
    if len(ids) > 1000:
        raise APIError('PAYLOAD_TOO_LARGE', 413)
    # Lock selected requests so every check and exported value uses one coherent snapshot.
    rows = db.session.scalars(select(BookRequest).where(BookRequest.id.in_(ids))
        .order_by(BookRequest.id).with_for_update()).all()
    if len(rows) != len(ids):
        raise APIError('NOT_FOUND', 404)
    prices, output, seen = {}, [], set()
    for row in rows:
        isbn = row.book.isbn
        if row.status != 'APPROVED' or row.price is None or (isbn is not None and isbn in prices and prices[isbn] != row.price):
            raise APIError('EXPORT_REVIEW_REQUIRED', 409)
        if isbn is not None:
            prices[isbn] = row.price
        if body['group_by_isbn'] and isbn is not None and isbn in seen:
            continue
        output.append([row.book.title, row.book.author, row.book.publisher, isbn or '', row.price, 1])
        if isbn is not None:
            seen.add(isbn)
    db.session.commit()
    return output
