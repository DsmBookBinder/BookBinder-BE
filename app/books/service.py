"""Book lookup, ownership and the FE search rules (selectors.ts / Search.tsx).

A book's id is its normalised ISBN-13. A "book" is known from the school's own table
(requested_books, authoritative for price/state), the external provider, or the owned-catalogue.
"""
from datetime import date
from flask import current_app
from sqlalchemy import func, select
from app.db import db
from app.books.isbn import normalize_isbn
from app.books.models import BookRequest, RequestedBook
from app.books.providers import ProviderError, ProviderTimeout, ProviderUnavailable

SOURCE_ERRORS = (ProviderUnavailable, ProviderError, ProviderTimeout)


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


def source_meta(item):
    """Validate one provider/catalogue item; None when unusable (never invent values)."""
    if not isinstance(item, dict):
        return None
    try:
        isbn = normalize_isbn(item.get('isbn'))
    except ValueError:
        return None
    if any(not isinstance(item.get(k), str) or not item[k].strip() for k in ('title', 'author', 'publisher')):
        return None
    price = item.get('price')
    if price is not None and (type(price) is not int or price < 0):
        price = None
    description = item.get('description')
    cover = item.get('cover_url')
    return {'isbn': isbn, 'title': item['title'].strip(), 'author': item['author'].strip(),
            'publisher': item['publisher'].strip(), 'price': price,
            'cover_url': cover if isinstance(cover, str) and cover else None,
            'pub_date': parse_date(item.get('pub_date')),
            'description': description if isinstance(description, str) else None}


def catalog_owned(isbn):
    """Display-only ownership: unknown/unavailable counts as not owned (requests re-check strictly)."""
    catalog = current_app.config.get('LIBRARY_CATALOG')
    if catalog is None:
        return False
    try:
        return catalog.ownership(isbn) == 'OWNED'
    except (*SOURCE_ERRORS, ValueError):
        return False


def is_owned(isbn, status=None):
    return status == 'purchased' or catalog_owned(isbn)


def request_counts():
    rows = db.session.execute(select(BookRequest.requested_book_id, func.count(BookRequest.id)).group_by(BookRequest.requested_book_id)).all()
    return {book_id: count for book_id, count in rows}


def relevance(meta, words):
    """FE relevance(): every word must hit; title +3, author +2, publisher/isbn/description +1."""
    score = 0
    other = f"{meta['publisher']} {meta['isbn']} {meta.get('description') or ''}".lower()
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
    result = provider.search(query, 1, 100)
    items = result.get('items') if isinstance(result, dict) else None
    if not isinstance(items, list):
        raise ProviderError()
    found = {}
    for item in items:
        meta = source_meta(item)
        if meta and meta['isbn'] not in found:
            found[meta['isbn']] = meta
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
            meta = source_meta(item)
            if meta:
                catalog_meta.setdefault(meta['isbn'], meta)
    order = {}
    for source in (provider_meta, db_meta, catalog_meta):
        order.update(dict.fromkeys(source))
    merged = []
    for isbn in order:
        meta = {}
        # The school table owns the price; other sources only fill blank descriptive fields.
        priority = [s[isbn] for s in (db_meta, provider_meta, catalog_meta) if isbn in s]
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
    counts = request_counts()
    mine = set(db.session.scalars(select(RequestedBook.isbn).join(BookRequest, BookRequest.requested_book_id == RequestedBook.id).where(BookRequest.user_id == user_id)))
    words = query.lower().split()
    entries = []
    for meta, row in candidates(query, scope):
        status = row.status if row else None
        owned = is_owned(meta['isbn'], status)
        if scope == 'owned' and not owned:
            continue
        score = relevance(meta, words) if words else 1
        if score <= 0:
            continue
        count = counts.get(row.id, 0) if row else 0
        entries.append((meta, row, owned, score, count))
    if sort == 'popular':
        entries.sort(key=lambda e: (-e[4], -e[3]))
    else:
        entries.sort(key=lambda e: -e[3])
    total = len(entries)
    page = min(page, max(1, -(-total // size)))
    items = []
    for meta, row, owned, _score, count in entries[(page - 1) * size:page * size]:
        item = book_out(meta, owned)
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
        if provider is not None:
            meta = source_meta(provider.get(isbn))
        if meta is None:
            catalog = current_app.config.get('LIBRARY_CATALOG')
            if catalog is not None and hasattr(catalog, 'books'):
                meta = next((m for m in (source_meta(i) for i in catalog.books()) if m and m['isbn'] == isbn), None)
        if meta is None and provider is None:
            raise ProviderUnavailable()
    if meta is None:
        return None
    count = db.session.scalar(select(func.count(BookRequest.id)).where(BookRequest.requested_book_id == row.id)) if row else 0
    mine = bool(row) and db.session.scalar(select(func.count(BookRequest.id)).where(BookRequest.requested_book_id == row.id, BookRequest.user_id == user_id)) > 0
    owned = is_owned(isbn, row.status if row else None)
    data = book_out(meta, owned)
    data['requestCount'] = count
    data['requestState'] = request_state(owned, mine, row.status if row else None)
    review = None
    if row is not None and count > 0:
        review = {'status': row.status}
        if row.status == 'rejected' and row.reject_reason:
            review['reason'] = row.reject_reason
    data['review'] = review
    return data


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
