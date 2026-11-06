from flask import Blueprint, current_app, g, request
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.db import db
from app.books.isbn import normalize_isbn
from app.books.models import BookRequest, Notification, RequestedBook
from app.books.providers import ProviderError, ProviderTimeout, ProviderUnavailable
from app.books.routes import error

requests_api = Blueprint('student_requests', __name__, url_prefix='/api')


def user():
    return getattr(g, 'current_user', None)


def field(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def authorize(student=False, csrf=False):
    identity = user()
    if identity is None or field(identity, 'id') is None:
        return error('AUTH_REQUIRED', '로그인이 필요합니다.', 401)
    if student and field(identity, 'role') != 'student':
        return error('FORBIDDEN', '학생만 이용할 수 있습니다.', 403)
    # Auth middleware must explicitly validate CSRF. Never trust the request directly.
    if csrf and getattr(g, 'csrf_valid', False) is not True:
        return error('CSRF_FAILED', '요청을 확인할 수 없습니다.', 403)
    return None


def pages():
    vals = []
    for name, default in (('page', '1'), ('page_size', '20')):
        value = request.args.get(name, default)
        if not value.isascii() or not value.isdigit():
            raise ValueError(name)
        vals.append(int(value))
    if vals[0] < 1 or not 1 <= vals[1] <= 100:
        raise ValueError('pagination')
    return vals


def timestamp(value):
    return value.isoformat(timespec='seconds') + 'Z' if value else None


def request_item(item):
    book = item.book
    return {'id': str(item.id), 'isbn': book.isbn, 'title': book.title,
            'author': book.author, 'publisher': book.publisher, 'price': item.price,
            'status': item.status, 'reject_reason': item.reject_reason,
            'processed_at': timestamp(item.processed_at),
            'requested_at': timestamp(item.requested_at)}


def source_error(exc):
    if isinstance(exc, ProviderTimeout):
        return error('BOOK_PROVIDER_TIMEOUT', '도서 제공처 응답 시간이 초과되었습니다.', 504)
    if isinstance(exc, ProviderError):
        return error('BOOK_PROVIDER_ERROR', '도서 제공처에 문제가 발생했습니다.', 502)
    return error('BOOK_PROVIDER_UNAVAILABLE', '도서 제공처를 사용할 수 없습니다.', 503)


@requests_api.post('/requests')
def create_request():
    denied = authorize(student=True, csrf=True)
    if denied: return denied
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {'isbn'}:
        return error('VALIDATION_ERROR', 'ISBN을 확인해 주세요.', 400)
    try:
        isbn = normalize_isbn(body['isbn'])
    except ValueError:
        return error('VALIDATION_ERROR', 'ISBN을 확인해 주세요.', 400)
    provider = current_app.config.get('BOOK_PROVIDER')
    catalog = current_app.config.get('LIBRARY_CATALOG')
    if provider is None:
        return error('BOOK_PROVIDER_UNAVAILABLE', '도서 제공처를 사용할 수 없습니다.', 503)
    if catalog is None:
        return error('LIBRARY_CATALOG_UNAVAILABLE', '교내 목록을 사용할 수 없습니다.', 503)
    try:
        ownership = catalog.ownership(isbn)
        info = provider.get(isbn)
    except (ProviderUnavailable, ProviderError, ProviderTimeout) as exc:
        return source_error(exc)
    if ownership not in ('OWNED', 'NOT_OWNED'):
        return error('LIBRARY_CATALOG_UNAVAILABLE', '소장 여부를 확인할 수 없습니다.', 503)
    if ownership == 'OWNED':
        return error('OWNED_BOOK', '이미 소장 중인 도서입니다.', 409)
    if info is None:
        return error('NOT_FOUND', '도서를 찾을 수 없습니다.', 404)
    if not isinstance(info, dict) or any(not isinstance(info.get(k), str) or not info[k].strip() for k in ('title', 'author', 'publisher')):
        return error('BOOK_PROVIDER_ERROR', '도서 정보가 올바르지 않습니다.', 502)
    price = info.get('price')
    if price is not None and (type(price) is not int or price < 0):
        return error('BOOK_PROVIDER_ERROR', '도서 가격이 올바르지 않습니다.', 502)
    try:
        book = db.session.execute(select(RequestedBook).where(RequestedBook.isbn == isbn).with_for_update()).scalar_one_or_none()
        if book is None:
            book = RequestedBook(isbn=isbn, title=info['title'], author=info['author'], publisher=info['publisher'], price=price, cover_url=info.get('cover_url'))
            db.session.add(book)
            db.session.flush()
        if book.status in ('rejected', 'purchased'):
            db.session.rollback()
            return error('STATE_CONFLICT', '신청할 수 없는 도서입니다.', 409)
        item = BookRequest(user_id=field(user(), 'id'), book=book, price=price)
        db.session.add(item)
        db.session.flush()
        response = request_item(item)
        db.session.commit()
        return response, 201
    except IntegrityError:
        db.session.rollback()
        # The ISBN row may have been created concurrently by another user.
        # Retry once using the now committed row, preserving duplicate protection.
        try:
            book = db.session.execute(select(RequestedBook).where(RequestedBook.isbn == isbn).with_for_update()).scalar_one_or_none()
            if book is None:
                raise RuntimeError('Concurrent book row unavailable')
            if book.status in ('rejected', 'purchased'):
                db.session.rollback()
                return error('STATE_CONFLICT', '신청할 수 없는 도서입니다.', 409)
            item = BookRequest(user_id=field(user(), 'id'), book=book, price=price)
            db.session.add(item)
            db.session.flush()
            response = request_item(item)
            db.session.commit()
            return response, 201
        except IntegrityError:
            db.session.rollback()
            return error('DUPLICATE_REQUEST', '이미 신청한 도서입니다.', 409)
        except Exception:
            db.session.rollback()
            raise
    except Exception:
        db.session.rollback()
        raise


@requests_api.get('/me/requests')
def list_requests():
    denied = authorize(student=True)
    if denied: return denied
    try: page, size = pages()
    except ValueError: return error('VALIDATION_ERROR', '페이지를 확인해 주세요.', 400)
    status = request.args.get('status')
    if status and status not in ('PENDING', 'APPROVED', 'REJECTED', 'PURCHASED'):
        return error('VALIDATION_ERROR', '상태를 확인해 주세요.', 400)
    query = select(BookRequest).join(RequestedBook).where(BookRequest.user_id == field(user(), 'id'))
    if status: query = query.where(BookRequest.status == status)
    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    items = db.session.execute(query.order_by(BookRequest.requested_at.desc(), BookRequest.id.desc()).offset((page-1)*size).limit(size)).scalars().all()
    return {'items': [request_item(item) for item in items], 'page': page, 'page_size': size, 'total': total}


@requests_api.get('/me/notifications')
def list_notifications():
    denied = authorize()
    if denied: return denied
    try: page, size = pages()
    except ValueError: return error('VALIDATION_ERROR', '페이지를 확인해 주세요.', 400)
    unread = request.args.get('unread_only', 'false')
    if unread not in ('true', 'false'):
        return error('VALIDATION_ERROR', '읽음 조건을 확인해 주세요.', 400)
    uid = field(user(), 'id')
    query = select(Notification).where(Notification.user_id == uid)
    count = db.session.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == uid, Notification.is_read.is_(False)))
    if unread == 'true': query = query.where(Notification.is_read.is_(False))
    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    items = db.session.execute(query.order_by(Notification.created_at.desc(), Notification.id.desc()).offset((page-1)*size).limit(size)).scalars().all()
    return {'items': [{'id': str(n.id), 'request_id': str(n.request_id), 'message': n.message, 'type': n.type, 'is_read': n.is_read, 'created_at': timestamp(n.created_at)} for n in items], 'page': page, 'page_size': size, 'total': total, 'unread_count': count}


@requests_api.patch('/me/notifications/<int:notification_id>/read')
def read_notification(notification_id):
    denied = authorize(csrf=True)
    if denied: return denied
    item = db.session.execute(select(Notification).where(Notification.id == notification_id, Notification.user_id == field(user(), 'id'))).scalar_one_or_none()
    if item is None: return error('NOT_FOUND', '알림을 찾을 수 없습니다.', 404)
    item.is_read = True
    db.session.commit()
    return {'id': str(item.id), 'is_read': True}
