"""Student requests and notifications (FE db.ts requestBook / cancelRequest / notifications)."""
from flask import Blueprint, current_app, g, request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from app.db import db
from app.books import service
from app.books.isbn import normalize_isbn
from app.books.models import BookRequest, Notification, RequestedBook, utcnow
from app.books.providers import ProviderError, ProviderUnavailable
from app.books.routes import provider_error
from app.errors import (AUTH_REQUIRED, BOOK_NOT_FOUND, CATALOG_MESSAGE, CSRF_FAILED, FORBIDDEN_STUDENT, error, fail)
from app.serializers import book_out, ms, request_out, review_out

requests_api = Blueprint('student_requests', __name__, url_prefix='/api')
STATUSES = ('pending', 'approved', 'rejected', 'purchased')
MAX_ATTEMPTS = 3
MYSQL_DEADLOCK = 1213


def user():
    return getattr(g, 'current_user', None)


def field(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def authorize(student=False, csrf=False):
    identity = user()
    if identity is None or field(identity, 'id') is None:
        return fail(AUTH_REQUIRED)
    if student and field(identity, 'role') != 'student':
        return fail(FORBIDDEN_STUDENT)
    # Auth middleware must explicitly validate CSRF. Never trust the request directly.
    if csrf and getattr(g, 'csrf_valid', False) is not True:
        return fail(CSRF_FAILED)
    return None


def is_deadlock(exc):
    """MySQL picked this transaction as the deadlock victim (error 1213): nothing was committed, so it is safe to retry."""
    args = getattr(exc.orig, 'args', ())
    return bool(args) and args[0] == MYSQL_DEADLOCK


class Rejected(Exception):
    def __init__(self, response):
        self.response = response


def catalog_unavailable():
    return error('LIBRARY_CATALOG_UNAVAILABLE', CATALOG_MESSAGE, 503)


def create_once(isbn, uid):
    # FE requestBook order: owned -> already requested -> rejected. Catalogue "unknown" blocks last (fail closed).
    row = db.session.execute(select(RequestedBook).where(RequestedBook.isbn == isbn).with_for_update()).scalar_one_or_none()
    ownership = service.ownership(isbn, row.status if row is not None else None)
    if ownership == 'OWNED':
        raise Rejected(error('OWNED_BOOK', '이미 교내에 소장 중인 도서예요.', 409))
    if row is not None and db.session.scalar(select(func.count(BookRequest.id)).where(BookRequest.requested_book_id == row.id, BookRequest.user_id == uid)):
        raise Rejected(error('DUPLICATE_REQUEST', '이미 신청한 도서예요.', 409))
    if row is not None and row.status == 'rejected':
        raise Rejected(error('REJECTED_BOOK', '반려된 도서는 다시 신청할 수 없어요.', 409))
    if ownership != 'NOT_OWNED':
        raise Rejected(catalog_unavailable())
    if row is None:
        provider = current_app.config.get('BOOK_PROVIDER')
        if provider is None:
            raise ProviderUnavailable()
        raw = provider.get(isbn)
        if raw is None:
            raise Rejected(fail(BOOK_NOT_FOUND))
        meta = service.source_meta(raw)
        if (meta is None or meta['isbn'] != isbn
                or any(len(meta[field_name]) > limit for field_name, limit in
                       (('title', 200), ('author', 100), ('publisher', 100)))
                or len(meta['cover_url'] or '') > 500):
            raise ProviderError()
        # Only a newly created row takes the provider price; librarian-entered prices are never overwritten.
        row = RequestedBook(isbn=isbn, title=meta['title'], author=meta['author'], publisher=meta['publisher'], price=meta['price'],
                            cover_url=meta['cover_url'], pub_date=meta['pub_date'], description=meta['description'],
                            status='pending', quantity=1)
        db.session.add(row)
        db.session.flush()
    item = BookRequest(user_id=uid, book=row)
    db.session.add(item)
    db.session.flush()
    body = {'request': request_out(item), 'review': review_out(row)}
    db.session.commit()
    return body, 201


@requests_api.post('/requests')
def create_request():
    denied = authorize(student=True, csrf=True)
    if denied: return denied
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or len(body) != 1 or next(iter(body)) not in ('bookId', 'isbn'):
        return error('VALIDATION_ERROR', '도서 정보를 확인해 주세요.', 400)
    try:
        isbn = normalize_isbn(next(iter(body.values())))
    except ValueError:
        return error('VALIDATION_ERROR', '도서 정보를 확인해 주세요.', 400)
    uid = field(user(), 'id')
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return create_once(isbn, uid)
            except IntegrityError:
                # Another request created the same book/request concurrently: retry on committed state.
                db.session.rollback()
                if attempt == MAX_ATTEMPTS:
                    return error('DUPLICATE_REQUEST', '이미 신청한 도서예요.', 409)
            except OperationalError as exc:
                # Two first-time requests for the same ISBN can deadlock on the book row's gap lock.
                db.session.rollback()
                if not is_deadlock(exc) or attempt == MAX_ATTEMPTS:
                    raise
    except Rejected as exc:
        db.session.rollback()
        return exc.response
    except service.SOURCE_ERRORS as exc:
        db.session.rollback()
        return provider_error(exc)
    except Exception:
        db.session.rollback()
        raise


@requests_api.get('/me/requests')
def list_requests():
    denied = authorize(student=True)
    if denied: return denied
    status = request.args.get('status')
    if status and status not in STATUSES:
        return error('VALIDATION_ERROR', '상태를 확인해 주세요.', 400)
    query = select(BookRequest).join(RequestedBook).where(BookRequest.user_id == field(user(), 'id'))
    if status:
        query = query.where(RequestedBook.status == status)
    rows = db.session.scalars(query.order_by(BookRequest.requested_at.desc(), BookRequest.id.desc())).all()
    items = []
    for item in rows:
        entry = {'request': request_out(item), 'book': book_out(service.row_meta(item.book), service.is_owned(item.book.isbn, item.book.status)),
                 'status': item.book.status}
        if item.book.status == 'rejected' and item.book.reject_reason:
            entry['reason'] = item.book.reject_reason
        items.append(entry)
    return {'items': items}


@requests_api.delete('/me/requests/<int:request_id>')
def cancel_request(request_id):
    denied = authorize(student=True, csrf=True)
    if denied: return denied
    target = db.session.get(BookRequest, request_id)
    if target is None or target.user_id != field(user(), 'id'):
        return error('NOT_FOUND', '신청 내역을 찾을 수 없어요.', 404)
    book = db.session.execute(select(RequestedBook).where(RequestedBook.id == target.requested_book_id).with_for_update()
                              .execution_options(populate_existing=True)).scalar_one()
    # The same request may have been cancelled while we waited for the lock.
    if db.session.execute(select(BookRequest.id).where(BookRequest.id == request_id).with_for_update()).first() is None:
        db.session.rollback()
        return error('NOT_FOUND', '신청 내역을 찾을 수 없어요.', 404)
    if book.status != 'pending':
        db.session.rollback()
        return error('STATE_CONFLICT', '대기 중인 신청만 취소할 수 있어요.', 409)
    # Notifications outlive the request (ON DELETE SET NULL); do it explicitly so SQLite behaves the same.
    db.session.execute(update(Notification).where(Notification.request_id == target.id).values(request_id=None))
    db.session.delete(target)
    db.session.flush()
    # Locking read: a plain COUNT would use the older snapshot and miss a concurrent cancel.
    remaining = db.session.execute(select(BookRequest.id).where(BookRequest.requested_book_id == book.id).with_for_update()).all()
    if not remaining:
        # Last requester left: the book drops off the librarian list. Row and price are kept.
        book.status, book.reject_reason, book.quantity, book.updated_at = 'pending', None, 1, utcnow()
    db.session.commit()
    return '', 204


def notification_out(item):
    data = {'id': str(item.id), 'userId': str(item.user_id), 'bookId': item.book.isbn, 'bookTitle': item.book.title,
            'status': item.type, 'createdAt': ms(item.created_at), 'read': bool(item.is_read), 'message': item.message}
    if item.reason:
        data['reason'] = item.reason
    return data


@requests_api.get('/me/notifications')
def list_notifications():
    denied = authorize()
    if denied: return denied
    # unreadOnly is the contract key. The old snake_case key is still accepted during the transition and
    # will be removed in the release after the FE ships unreadOnly (documented in openapi.yaml).
    unread = request.args.get('unreadOnly', request.args.get('unread_only', 'false'))
    if unread not in ('true', 'false'):
        return error('VALIDATION_ERROR', '읽음 조건을 확인해 주세요.', 400)
    uid = field(user(), 'id')
    query = select(Notification).where(Notification.user_id == uid)
    count = db.session.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == uid, Notification.is_read.is_(False)))
    if unread == 'true':
        query = query.where(Notification.is_read.is_(False))
    items = db.session.scalars(query.order_by(Notification.created_at.desc(), Notification.id.desc())).all()
    return {'items': [notification_out(n) for n in items], 'unreadCount': count}


@requests_api.patch('/me/notifications/read-all')
def read_all_notifications():
    denied = authorize(csrf=True)
    if denied: return denied
    result = db.session.execute(update(Notification).where(Notification.user_id == field(user(), 'id'), Notification.is_read.is_(False)).values(is_read=True))
    db.session.commit()
    return {'updated': result.rowcount}


@requests_api.patch('/me/notifications/<int:notification_id>/read')
def read_notification(notification_id):
    denied = authorize(csrf=True)
    if denied: return denied
    item = db.session.execute(select(Notification).where(Notification.id == notification_id, Notification.user_id == field(user(), 'id'))).scalar_one_or_none()
    if item is None: return error('NOT_FOUND', '알림을 찾을 수 없어요.', 404)
    item.is_read = True
    db.session.commit()
    return {'id': str(item.id), 'read': True}
