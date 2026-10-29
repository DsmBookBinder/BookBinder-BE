import unittest

from flask import Flask, g
from app.books.routes import books_api
from app.books.providers import ProviderError, ProviderTimeout


class Provider:
    def search(self, query, page, page_size):
        return {"items": [], "page": page, "page_size": page_size, "query": query}

    def get(self, isbn):
        return {"isbn": isbn} if isbn == "9780306406157" else None


class BookRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(books_api)
        self.client = self.app.test_client()

    def authenticate(self):
        @self.app.before_request
        def user():
            g.current_user = {"id": 1}

    def test_endpoints_fail_closed_without_authentication(self):
        for url in ("/api/books?query=a", "/api/books/9780306406157", "/api/library/books?query=a"):
            self.assertEqual(self.client.get(url).status_code, 401)

    def test_validation_and_unconfigured_providers(self):
        self.authenticate()
        for url in ("/api/books", "/api/books?query=a&page=0", "/api/books?query=a&page_size=101", "/api/books/9780306406158"):
            self.assertEqual(self.client.get(url).status_code, 400)
        self.assertEqual(self.client.get('/api/books?query=a').get_json()['error']['code'], 'BOOK_PROVIDER_UNAVAILABLE')
        self.assertEqual(self.client.get('/api/library/books?query=a').get_json()['error']['code'], 'LIBRARY_CATALOG_UNAVAILABLE')

    def test_search_and_detail(self):
        self.authenticate()
        self.app.config['BOOK_PROVIDER'] = Provider()
        self.app.config['LIBRARY_CATALOG'] = Provider()
        response = self.client.get('/api/books?query=%20book%20&page=2&page_size=5')
        self.assertEqual(response.get_json()['query'], 'book')
        self.assertEqual(response.get_json()['page'], 2)
        self.assertEqual(self.client.get('/api/books/0306406152').get_json()['isbn'], '9780306406157')
        self.assertEqual(self.client.get('/api/books/9780804429573').status_code, 404)
        self.assertEqual(self.client.get('/api/library/books?query=book').status_code, 200)

    def test_provider_error_and_timeout(self):
        self.authenticate()
        class Broken:
            def search(self, *args):
                raise ProviderError()
            def get(self, *args):
                raise ProviderTimeout()
        self.app.config['BOOK_PROVIDER'] = Broken()
        self.assertEqual(self.client.get('/api/books?query=a').status_code, 502)
        self.assertEqual(self.client.get('/api/books/9780306406157').status_code, 504)


if __name__ == '__main__':
    unittest.main()
