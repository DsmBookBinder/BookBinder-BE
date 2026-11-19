"""Search the provider, requested books, and the school catalogue."""
from datetime import date
from flask import current_app
from sqlalchemy import func, select
from app.db import db
from app.books.catalog import search_key
from app.books.isbn import normalize_isbn
from app.books.models import BookRequest, RequestedBook
from app.books.providers import ProviderError, ProviderRateLimited, ProviderTimeout, ProviderUnavailable

SOURCE_ERRORS = (ProviderUnavailable, ProviderError, ProviderRateLimited, ProviderTimeout)
PROVIDER_PAGE_SIZE = 100
MAX_PROVIDER_PAGES = 50  # safety cap: 5,000 hits per search


def parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def row_meta(row):
    return {'isbn': row.isbn, 'title': row.title, 'author': row.author, 'publisher': row.publisher, 'price': row.price,
            'cover_url': row.cover_url, 'pub_date': row.pub_date, 'description': row.description}


def source_meta(item, *, catalogue=False):
    """Validate one provider/catalogue item; None when unusable (never invent values)."""
    if not isinstance(item, dict):
        return None
    try:
        isbn = normalize_isbn(item.get('isbn'))
    except ValueError:
        return None
    if any(not isinstance(item.get(k), str) for k in ('title', 'author', 'publisher')) or not item['title'].strip():
        return None
    if not catalogue and (not item['author'].strip() or not item['publisher'].strip()):
        return None
    price = item.get('price')
    if price is not None and (type(price) is not int or not 0 <= price <= 2147483647):
        price = None
    description = item.get('description')
    cover = item.get('cover_url')
    return {'isbn': isbn, 'title': item['title'].strip(), 'author': item['author'].strip(),
            'publisher': item['publisher'].strip(), 'price': price,
            'cover_url': cover if isinstance(cover, str) and cover else None,
            'pub_date': parse_date(item.get('pub_date')),
            'year': item.get('year'),
            'material_type': item.get('material_type'),
            'library_status': item.get('library_status'),
            'description': description if isinstance(description, str) else None}


def ownership(isbn, status=None):
    if status == 'purchased':
        return 'OWNED'
    catalog = current_app.config.get('LIBRARY_CATALOG')
    if catalog is None:
        return 'UNKNOWN'
    try:
        result = catalog.ownership(isbn)
    except (*SOURCE_ERRORS, ValueError):
        return 'UNKNOWN'
    if result == 'UNKNOWN' and current_app.config.get('DEMO_ASSUME_UNOWNED'):
        return 'NOT_OWNED'
    return result


def is_owned(isbn, status=None):
    return ownership(isbn, status) == 'OWNED'


def request_counts():
    rows = db.session.execute(select(BookRequest.requested_book_id, func.count(BookRequest.id)).group_by(BookRequest.requested_book_id)).all()
    return {book_id: count for book_id, count in rows}


def relevance(meta, words):
    """FE relevance(): every word must hit; title +3, author +2, publisher/isbn/description +1."""
    score = 0
    other = f"{meta['publisher']} {meta['isbn'] or ''} {meta.get('description') or ''}".lower()
    for word in words:
        if word in meta['title'].lower():
            score += 3
        elif word in meta['author'].lower():
            score += 2
        elif word in other:
            score += 1
        else:
            return 0
    return score


def _provider_items(query):
    provider = current_app.config.get('BOOK_PROVIDER')
    if provider is None:
        raise ProviderUnavailable()
    # Read every provider page first; de-duplication, the all-words filter, sorting and API paging happen afterwards.
    found = {}
    page_size = getattr(provider, 'max_page_size', PROVIDER_PAGE_SIZE)
    for page in range(1, MAX_PROVIDER_PAGES + 1):
        result = provider.search(query, page, page_size)
        items = result.get('items') if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise ProviderError()
        for item in items:
            meta = source_meta(item)
            if meta and meta['isbn'] not in found:
                found[meta['isbn']] = meta
        total = result.get('total')
        has_next = result.get('has_next')
        if not items or has_next is False:
            break
        if has_next is not True and (page * page_size >= total if type(total) is int else len(items) < page_size):
            break
    return found


def candidates(query, scope):
    """FE searches every book it knows. Here: provider hits (query only) + school table + catalogue."""
    provider_meta = _provider_items(query) if query and scope == 'all' else {}
    rows = db.session.scalars(select(RequestedBook).order_by(RequestedBook.id)).all()
    row_by_isbn = {row.isbn: row for row in rows}
    db_meta = {row.isbn: row_meta(row) for row in rows}
    catalog = current_app.config.get('LIBRARY_CATALOG')
    catalog_meta = {}
    if catalog is not None and hasattr(catalog, 'books'):
        for item in catalog.books():
            if item.get('id') and item.get('isbn') is None:
                catalog_meta[item['id']] = item
                continue
            meta = source_meta(item, catalogue=True)
            if meta:
                catalog_meta.setdefault(meta['isbn'], meta)
    order = {}
    for source in (provider_meta, db_meta, catalog_meta):
        order.update(dict.fromkeys(source))
    merged = []
    for isbn in order:
        # The school table owns the price; other sources only fill blank descriptive fields.
        priority = [s[isbn] for s in (db_meta, provider_meta, catalog_meta) if isbn in s]
        meta = {key: priority[0][key] for key in ('isbn', 'title', 'author', 'publisher')}
        for source in priority:
            for key, value in source.items():
                if key == 'price':
                    continue
                if meta.get(key) in (None, '') and value not in (None, ''):
                    meta[key] = value
        meta['price'] = priority[0].get('price')
        merged.append((meta, row_by_isbn.get(isbn)))
    return merged


def request_state(owned, mine, status):
    """FE requestStateFor order: owned, requested, rejected, available."""
    if owned:
        return 'owned'
    if mine:
        return 'requested'
    if status == 'rejected':
        return 'rejected'
    return 'available'


def search(user_id, query, scope, sort, page, size):
    from app.serializers import book_out
    if scope == 'all' and not query:
        return {'items': [], 'page': 1, 'pageSize': size, 'total': 0}
    counts = request_counts()
    mine = set(db.session.scalars(select(RequestedBook.isbn).join(BookRequest, BookRequest.requested_book_id == RequestedBook.id).where(BookRequest.user_id == user_id)))
    words = query.lower().split()
    entries = []
    for meta, row in candidates(query, scope):
        status = row.status if row else None
        book_ownership = 'OWNED' if meta.get('id', '').startswith('dls-') else ownership(meta['isbn'], status)
        owned = book_ownership == 'OWNED'
        if scope == 'owned' and not owned:
            continue
        score = relevance(meta, words) if words else 1
        if not score and owned and search_key(query) in search_key(meta['title']):
            score = 3
        if score <= 0:
            continue
        count = counts.get(row.id, 0) if row else 0
        entries.append((meta, row, book_ownership, score, count))
    if sort == 'popular':
        entries.sort(key=lambda e: (-e[4], -e[3]))
    else:
        entries.sort(key=lambda e: -e[3])
    total = len(entries)
    page = min(page, max(1, -(-total // size)))
    items = []
    for meta, row, book_ownership, _score, count in entries[(page - 1) * size:page * size]:
        owned = book_ownership == 'OWNED'
        item = book_out(meta, owned)
        item['ownership'] = book_ownership
        item['requestCount'] = count
        item['requestState'] = request_state(owned, meta['isbn'] in mine, row.status if row else None)
        items.append(item)
    return {'items': items, 'page': page, 'pageSize': size, 'total': total}


def detail(user_id, isbn):
    from app.serializers import book_out
    row = db.session.scalar(select(RequestedBook).where(RequestedBook.isbn == isbn))
    meta = None
    if row is not None:
        meta = row_meta(row)
    else:
        provider = current_app.config.get('BOOK_PROVIDER')
        provider_error = None
        if provider is not None:
            try:
                meta = source_meta(provider.get(isbn))
            except SOURCE_ERRORS as exc:
                provider_error = exc  # keep it; the school catalogue may still know the book
        if meta is None:
            catalog = current_app.config.get('LIBRARY_CATALOG')
            if hasattr(catalog, 'get'):
                meta = source_meta(catalog.get(isbn), catalogue=True)
            elif hasattr(catalog, 'books'):
                meta = next((source_meta(i, catalogue=True) for i in catalog.books() if i.get('isbn') == isbn), None)
        if meta is None and provider_error is not None:
            raise provider_error  # catalogue did not have it either: report the original provider failure
        if meta is None and provider is None:
            raise ProviderUnavailable()
    if meta is None:
        return None
    count = db.session.scalar(select(func.count(BookRequest.id)).where(BookRequest.requested_book_id == row.id)) if row else 0
    mine = bool(row) and db.session.scalar(select(func.count(BookRequest.id)).where(BookRequest.requested_book_id == row.id, BookRequest.user_id == user_id)) > 0
    book_ownership = ownership(isbn, row.status if row else None)
    owned = book_ownership == 'OWNED'
    data = book_out(meta, owned)
    data['ownership'] = book_ownership
    data['requestCount'] = count
    data['requestState'] = request_state(owned, mine, row.status if row else None)
    review = None
    if row is not None and count > 0:
        review = {'status': row.status}
        if row.status == 'rejected' and row.reject_reason:
            review['reason'] = row.reject_reason
    data['review'] = review
    return data


def catalogue_detail(book_id):
    from app.serializers import book_out
    catalog = current_app.config.get('LIBRARY_CATALOG')
    item = catalog.get(book_id) if hasattr(catalog, 'get') else None
    if item is None:
        return None
    return {**book_out(item, True), 'ownership': 'OWNED', 'requestCount': 0, 'requestState': 'owned', 'review': None}


def popular(limit):
    from app.serializers import book_out
    rows = db.session.execute(select(RequestedBook, func.count(BookRequest.id).label('n')).join(BookRequest, BookRequest.requested_book_id == RequestedBook.id)
                              .group_by(RequestedBook.id).order_by(func.count(BookRequest.id).desc(), RequestedBook.id)).all()
    items = []
    for row, count in rows:
        if is_owned(row.isbn, row.status):
            continue
        items.append({'book': book_out(row_meta(row), False), 'count': count})
        if len(items) == limit:
            break
    return {'items': items}
