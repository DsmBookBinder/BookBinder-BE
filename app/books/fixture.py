"""DEVELOPMENT-ONLY fixture provider/catalogue built from the FE sample books.

Never enabled outside debug mode (see app.books.sources). Not a real data source.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from app.books.catalog import CatalogSnapshot
from app.books.isbn import normalize_isbn

DATA = Path(__file__).with_name('fixture_data') / 'books.json'


def load_rows():
    with DATA.open(encoding='utf-8') as handle:
        return json.load(handle)


def _item(row):
    return {'isbn': row['isbn'], 'title': row['title'], 'author': row['author'], 'publisher': row['publisher'],
            'price': row['price'], 'cover_url': None, 'pub_date': row['pub_date'], 'description': row['description'],
            'source': 'fixture'}


class FixtureProvider:
    """Matches any search word; the service applies the real all-words scoring."""
    is_fixture = True

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else load_rows()

    def search(self, query, page, page_size):
        words = query.casefold().split()
        hits = [r for r in self.rows if not words or any(
            w in ' '.join((r['title'], r['author'], r['publisher'], r['isbn'], r['description'])).casefold() for w in words)]
        start = (page - 1) * page_size
        return {'items': [_item(r) for r in hits[start:start + page_size]], 'page': page, 'page_size': page_size,
                'total': len(hits), 'source': 'fixture'}

    def get(self, isbn):
        isbn = normalize_isbn(isbn)
        return next((_item(r) for r in self.rows if r['isbn'] == isbn), None)


def fixture_catalog(rows=None):
    rows = rows if rows is not None else load_rows()
    owned = [{'isbn': r['isbn'], 'title': r['title'], 'author': r['author'], 'publisher': r['publisher'],
              'price': r['price'], 'pub_date': r['pub_date'], 'description': r['description']} for r in rows if r['owned']]
    catalog = CatalogSnapshot(owned, datetime.now(timezone.utc))
    catalog.is_fixture = True
    return catalog
