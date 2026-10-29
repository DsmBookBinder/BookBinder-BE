"""In-memory snapshot of a validated school catalogue.

This adapter does not load files or turn an absent catalogue into an empty one.
The importer is responsible for validating the original school data.
"""
from datetime import datetime, timezone
from app.books.isbn import normalize_isbn


class CatalogSnapshot:
    def __init__(self, rows, updated_at):
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None or updated_at.utcoffset() is None:
            raise ValueError('catalogue timestamp must include a timezone')
        self.updated_at = updated_at.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        self.rows = []
        self.isbns = set()
        self.has_unidentified = False
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('catalogue row must be a dict')
            if any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('title', 'author', 'publisher')):
                raise ValueError('catalogue row missing required fields')
            value = row.get('isbn')
            isbn = normalize_isbn(value) if value else None
            self.has_unidentified |= isbn is None
            if isbn:
                self.isbns.add(isbn)
            self.rows.append({'isbn': isbn, 'title': row['title'].strip(), 'author': row['author'].strip(), 'publisher': row['publisher'].strip(), 'ownership': 'OWNED'})

    def ownership(self, isbn):
        isbn = normalize_isbn(isbn)
        if isbn in self.isbns:
            return 'OWNED'
        # An unidentified entry might be the requested edition. Avoid asserting
        # a negative when the source cannot support it.
        return 'UNKNOWN' if self.has_unidentified else 'NOT_OWNED'

    def search(self, query, page, page_size):
        needle = query.casefold()
        rows = [r for r in self.rows if any(needle in (r[key] or '').casefold() for key in ('title', 'author', 'isbn'))]
        start = (page - 1) * page_size
        return {'items': rows[start:start + page_size], 'page': page, 'page_size': page_size, 'total': len(rows), 'catalog_updated_at': self.updated_at}
