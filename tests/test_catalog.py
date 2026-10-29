import unittest
from datetime import datetime, timezone
from app.books.catalog import CatalogSnapshot


class CatalogTests(unittest.TestCase):
    def test_search_paging_and_ownership(self):
        catalog = CatalogSnapshot([
            {'isbn': '0-306-40615-2', 'title': 'Book One', 'author': 'Author', 'publisher': 'P'},
            {'isbn': '9780804429573', 'title': 'Book Two', 'author': 'Author', 'publisher': 'P'},
        ], datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(catalog.ownership('9780306406157'), 'OWNED')
        result = catalog.search('BOOK', 2, 1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['items'][0]['title'], 'Book Two')
        self.assertEqual(result['catalog_updated_at'], '2026-01-01T00:00:00Z')

    def test_unknown_isbn_blocks_negative_ownership(self):
        catalog = CatalogSnapshot([{'isbn': None, 'title': '도서', 'author': '저자', 'publisher': '출판사'}], datetime.now(timezone.utc))
        self.assertEqual(catalog.ownership('9780306406157'), 'UNKNOWN')

    def test_rejects_invalid_source(self):
        with self.assertRaises(ValueError):
            CatalogSnapshot([], datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            CatalogSnapshot([{'isbn': 'bad', 'title': 'a', 'author': 'b', 'publisher': 'c'}], datetime.now(timezone.utc))


if __name__ == '__main__':
    unittest.main()
