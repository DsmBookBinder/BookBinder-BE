"""In-memory snapshot of a validated school catalogue.

This adapter does not load files or turn an absent catalogue into an empty one.
The importer is responsible for validating the original school data.
"""
from datetime import datetime, timezone
import unicodedata
from app.books.isbn import normalize_isbn


def search_key(value):
    return ''.join(unicodedata.normalize('NFKC', value).casefold().split())


class CatalogSnapshot:
    def __init__(self, rows, updated_at):
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None or updated_at.utcoffset() is None:
            raise ValueError('catalogue timestamp must include a timezone')
        self.updated_at = updated_at.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        self.rows = []
        self.isbns = set()
        self.by_id = {}
        self.has_unidentified = False
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('catalogue row must be a dict')
            if (any(not isinstance(row.get(k), str) for k in ('title', 'author', 'publisher'))
                    or not row['title'].strip()):
                raise ValueError('catalogue row missing required fields')
            value = row.get('isbn')
            isbn = normalize_isbn(value) if value else None
            self.has_unidentified |= isbn is None
            if isbn:
                self.isbns.add(isbn)
            item = {'isbn': isbn, 'title': row['title'].strip(), 'author': row['author'].strip(), 'publisher': row['publisher'].strip(), 'ownership': 'OWNED'}
            # Optional metadata some sources can supply (never required).
            for key in ('price', 'cover_url', 'pub_date', 'description', 'year', 'material_type', 'library_status'):
                if row.get(key) is not None:
                    item[key] = row[key]
            if isbn or row.get('id'):
                item['id'] = isbn or row['id']
                self.by_id.setdefault(item['id'], item)
            self.rows.append(item)

    def ownership(self, isbn):
        isbn = normalize_isbn(isbn)
        if isbn in self.isbns:
            return 'OWNED'
        # An unidentified entry might be the requested edition. Avoid asserting
        # a negative when the source cannot support it.
        return 'UNKNOWN' if self.has_unidentified else 'NOT_OWNED'

    def books(self):
        """Every addressable book. Imports supply an opaque id for entries without ISBN."""
        return [dict(item) for item in self.by_id.values()]

    def get(self, book_id):
        item = self.by_id.get(book_id)
        return dict(item) if item is not None else None

    def search(self, query, page, page_size):
        needle = search_key(query)
        rows = [r for r in self.rows if any(needle in search_key(r[key] or '') for key in ('title', 'author', 'isbn'))]
        start = (page - 1) * page_size
        return {'items': rows[start:start + page_size], 'page': page, 'page_size': page_size, 'total': len(rows), 'catalog_updated_at': self.updated_at}
