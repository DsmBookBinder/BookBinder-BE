"""Read-only book search endpoints; integrations are deliberately injected."""
from flask import Blueprint, current_app, g, request

from app.books.isbn import normalize_isbn
from app.books.providers import ProviderError, ProviderTimeout, ProviderUnavailable

books_api = Blueprint("books", __name__, url_prefix="/api")


def error(code, message, status):
    return {"error": {"code": code, "message": message}}, status


def authenticated():
    # A's authentication middleware must set g.current_user after verifying a session.
    # Missing integration fails closed; request headers are never trusted as identity.
    identity = getattr(g, "current_user", None)
    return identity is not None and (identity.get("id") if isinstance(identity, dict) else getattr(identity, "id", None)) is not None


def pagination():
    query = request.args.get("query", "").strip()
    if not 1 <= len(query) <= 100:
        raise ValueError("query")
    values = []
    for key, default in (("page", "1"), ("page_size", "20")):
        raw = request.args.get(key, default)
        if not raw.isascii() or not raw.isdigit():
            raise ValueError(key)
        values.append(int(raw))
    page, size = values
    if page < 1 or not 1 <= size <= 100:
        raise ValueError("pagination")
    return query, page, size


def provider_call(config_key, operation, unavailable_code):
    provider = current_app.config.get(config_key)
    if provider is None:
        return error(unavailable_code, "데이터 제공처를 사용할 수 없습니다.", 503)
    try:
        result = operation(provider)
    except ProviderUnavailable:
        return error(unavailable_code, "데이터 제공처를 사용할 수 없습니다.", 503)
    except ProviderTimeout:
        return error("BOOK_PROVIDER_TIMEOUT", "도서 제공처 응답 시간이 초과되었습니다.", 504)
    except ProviderError:
        return error("BOOK_PROVIDER_ERROR", "도서 제공처에 문제가 발생했습니다.", 502)
    if result is None:
        return error("NOT_FOUND", "도서를 찾을 수 없습니다.", 404)
    return result, 200


@books_api.get("/books")
def search_books():
    if not authenticated():
        return error("AUTH_REQUIRED", "로그인이 필요합니다.", 401)
    try:
        query, page, size = pagination()
    except ValueError:
        return error("VALIDATION_ERROR", "검색 조건을 확인해 주세요.", 400)
    return provider_call("BOOK_PROVIDER", lambda p: p.search(query, page, size), "BOOK_PROVIDER_UNAVAILABLE")


@books_api.get("/books/<isbn>")
def book_detail(isbn):
    if not authenticated():
        return error("AUTH_REQUIRED", "로그인이 필요합니다.", 401)
    try:
        isbn = normalize_isbn(isbn)
    except ValueError:
        return error("VALIDATION_ERROR", "ISBN을 확인해 주세요.", 400)
    return provider_call("BOOK_PROVIDER", lambda p: p.get(isbn), "BOOK_PROVIDER_UNAVAILABLE")


@books_api.get("/library/books")
def search_library():
    if not authenticated():
        return error("AUTH_REQUIRED", "로그인이 필요합니다.", 401)
    try:
        query, page, size = pagination()
    except ValueError:
        return error("VALIDATION_ERROR", "검색 조건을 확인해 주세요.", 400)
    return provider_call("LIBRARY_CATALOG", lambda p: p.search(query, page, size), "LIBRARY_CATALOG_UNAVAILABLE")
