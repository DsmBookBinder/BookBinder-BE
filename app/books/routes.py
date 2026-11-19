"""Book search, detail and popular books (FE Search.tsx / BookDetail.tsx)."""
from flask import Blueprint, g, request

from app.books.isbn import normalize_isbn
from app.books.providers import ProviderError, ProviderRateLimited, ProviderTimeout
from app.errors import AUTH_REQUIRED, BOOK_NOT_FOUND, FILTER_INVALID, PROVIDER_MESSAGE, error, fail

books_api = Blueprint("books", __name__, url_prefix="/api")


def current_id():
    # Authentication middleware sets g.current_user from a server-side session; headers are never trusted.
    identity = getattr(g, "current_user", None)
    if identity is None:
        return None
    return identity.get("id") if isinstance(identity, dict) else getattr(identity, "id", None)


def authenticated():
    return current_id() is not None


def provider_error(exc):
    if isinstance(exc, ProviderRateLimited):
        return error("BOOK_PROVIDER_RATE_LIMITED", PROVIDER_MESSAGE, 429)
    if isinstance(exc, ProviderTimeout):
        return error("BOOK_PROVIDER_TIMEOUT", PROVIDER_MESSAGE, 504)
    if isinstance(exc, ProviderError):
        return error("BOOK_PROVIDER_ERROR", PROVIDER_MESSAGE, 502)
    return error("BOOK_PROVIDER_UNAVAILABLE", PROVIDER_MESSAGE, 503)


def integer_arg(name, default, minimum, maximum):
    raw = request.args.get(name, str(default))
    if not raw.isascii() or not raw.isdigit():
        raise ValueError(name)
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(name)
    return value


@books_api.get("/books")
def search_books():
    from app.books import service
    if not authenticated():
        return fail(AUTH_REQUIRED)
    query = request.args.get("q", "").strip()
    scope, sort = request.args.get("scope", "all"), request.args.get("sort", "relevance")
    try:
        page = integer_arg("page", 1, 1, 10**9)
        size = integer_arg("pageSize", 5, 1, 100)
    except ValueError:
        return fail(FILTER_INVALID)
    if len(query) > 100 or scope not in ("all", "owned") or sort not in ("relevance", "popular"):
        return fail(FILTER_INVALID)
    try:
        return service.search(current_id(), query, scope, sort, page, size)
    except service.SOURCE_ERRORS as exc:
        return provider_error(exc)


@books_api.get("/books/popular")
def popular_books():
    from app.books import service
    if not authenticated():
        return fail(AUTH_REQUIRED)
    try:
        limit = integer_arg("limit", 5, 1, 50)
    except ValueError:
        return fail(FILTER_INVALID)
    return service.popular(limit)


@books_api.get("/books/<book_id>")
def book_detail(book_id):
    from app.books import service
    if not authenticated():
        return fail(AUTH_REQUIRED)
    if book_id.startswith('dls-'):
        data = service.catalogue_detail(book_id)
        return fail(BOOK_NOT_FOUND) if data is None else data
    try:
        isbn = normalize_isbn(book_id)
    except ValueError:
        return fail(BOOK_NOT_FOUND)
    try:
        data = service.detail(current_id(), isbn)
    except service.SOURCE_ERRORS as exc:
        return provider_error(exc)
    return fail(BOOK_NOT_FOUND) if data is None else data
