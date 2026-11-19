"""FE-shaped JSON: camelCase keys, lowercase states, epoch-millisecond times, string ids.

DB and Python stay snake_case; only these functions rename.
"""
import calendar
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def ms(value):
    """Naive UTC datetime -> epoch milliseconds (int)."""
    if value is None:
        return None
    return calendar.timegm(value.timetuple()) * 1000 + value.microsecond // 1000


def to_utc_naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def kst(value):
    """Naive UTC datetime -> aware KST datetime."""
    return value.replace(tzinfo=timezone.utc).astimezone(KST)


def kst_now():
    return datetime.now(KST)


def user_out(user):
    data = {'id': str(user.id), 'loginId': user.login_id, 'name': user.name, 'role': user.role.lower(),
            'joinedAt': ms(user.created_at)}
    if user.grade_class:
        data['classNo'] = user.grade_class
    if user.student_no:
        data['studentNo'] = user.student_no
    return data


def requester_out(user):
    return {k: v for k, v in {'id': str(user.id), 'name': user.name, 'classNo': user.grade_class,
                               'studentNo': user.student_no}.items() if v is not None}


def book_out(meta, owned):
    """meta: dict with isbn,title,author,publisher,price,cover_url,pub_date(date|None),description."""
    pub = meta.get('pub_date')
    data = {'id': meta.get('id', meta['isbn']), 'isbn': meta['isbn'], 'title': meta['title'], 'author': meta['author'],
            'publisher': meta['publisher'], 'year': pub.year if pub else meta.get('year'),
            'pubDate': pub.isoformat() if pub else None, 'price': meta.get('price'),
            'description': meta.get('description') or '', 'owned': bool(owned)}
    if meta.get('cover_url'):
        data['cover'] = meta['cover_url']
    if meta.get('material_type'):
        data['materialType'] = meta['material_type']
    if meta.get('library_status'):
        data['libraryStatus'] = meta['library_status']
    return data


def review_out(book):
    data = {'bookId': book.isbn, 'status': book.status, 'quantity': book.quantity, 'updatedAt': ms(book.updated_at)}
    if book.status == 'rejected' and book.reject_reason:
        data['reason'] = book.reject_reason
    return data


def request_out(item):
    return {'id': str(item.id), 'bookId': item.book.isbn, 'userId': str(item.user_id), 'createdAt': ms(item.requested_at)}
