import unittest
from datetime import datetime
from app.books.providers import ProviderError, ProviderTimeout
from tests.helpers import AppCase, CONVENIENCE, MODEUN, PAINT, SOSEON


class BookRouteTests(AppCase):
    def search(self, key='s1', **params):
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        response = self.call(key, 'get', f'/api/books?{query}')
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_requires_login(self):
        client = self.app.test_client()
        for url in ('/api/books', f'/api/books/{SOSEON}', '/api/books/popular'):
            self.assertEqual(client.get(url).status_code, 401, url)

    def test_search_without_query_lists_known_books_with_total(self):
        body = self.search()
        # No query: only owned catalogue + requested books are known (no provider call).
        self.assertEqual((body['page'], body['pageSize'], body['total']), (1, 5, 8))
        self.assertTrue(all(item['owned'] for item in body['items']))
        self.assertEqual(set(body['items'][0]) - {'cover'}, {'id', 'isbn', 'title', 'author', 'publisher', 'year', 'pubDate', 'price', 'description',
                                                             'owned', 'requestCount', 'requestState'})

    def test_search_words_scoring_and_all_words_rule(self):
        # 한강: 소년이 온다 + 채식주의자 by author (+2). Title hit scores higher than author hit.
        body = self.search(q='한강')
        self.assertEqual({i['title'] for i in body['items']}, {'소년이 온다', '채식주의자'})
        body = self.search(q='한강 창비 소년이')  # every word must hit
        self.assertEqual([i['title'] for i in body['items']], ['소년이 온다'])
        self.assertEqual(self.search(q='한강 없는단어')['total'], 0)
        self.assertEqual(self.search(q='CLEAN')['total'], 0)  # 클린 코드 is Korean; no latin hit
        self.assertEqual([i['title'] for i in self.search(q='클린')['items']], ['클린 코드'])
        # Title (+3) beats description-only (+1): '코드' is in 클린 코드's title.
        titles = [i['title'] for i in self.search(q='코드', pageSize=100)['items']]
        self.assertEqual(titles[0], '클린 코드')

    def test_scope_owned_and_pagination(self):
        body = self.search(scope='owned', pageSize=3)
        self.assertEqual((body['total'], len(body['items'])), (8, 3))
        last = self.search(scope='owned', pageSize=3, page=99)  # clamped to the last page
        self.assertEqual((last['page'], len(last['items'])), (3, 2))
        self.assertEqual(self.call('s1', 'get', '/api/books?pageSize=101').status_code, 400)
        self.assertEqual(self.call('s1', 'get', '/api/books?scope=x').status_code, 400)
        self.assertEqual(self.call('s1', 'get', '/api/books?sort=x').status_code, 400)

    def test_request_count_state_and_popular_sort(self):
        self.request_book('s1', SOSEON)
        self.request_book('s2', SOSEON)
        self.request_book('s1', CONVENIENCE)
        self.assertEqual(self.search(q='소년이 편의점')['total'], 0)  # words must all hit: nothing matches both
        found = {i['title']: i for i in self.search(q='편의점', pageSize=100)['items']}
        self.assertEqual((found['불편한 편의점']['requestCount'], found['불편한 편의점']['requestState']), (1, 'requested'))
        popular = self.search(q='소년이', sort='popular')['items']
        self.assertEqual((popular[0]['title'], popular[0]['requestCount']), ('소년이 온다', 2))
        self.assertEqual(popular[0]['requestState'], 'requested')
        self.assertEqual(self.search('s3', q='소년이')['items'][0]['requestState'], 'available')
        # Ties keep relevance order; more requests first.
        both = self.search(q='편의점', sort='popular')['items'] + self.search(q='소년이', sort='popular')['items']
        self.assertEqual([i['requestCount'] for i in both], [1, 2])

    def test_state_owned_rejected_and_purchased_ownership(self):
        owned = self.search(q='페인트')['items'][0]
        self.assertEqual((owned['owned'], owned['requestState']), (True, 'owned'))
        self.request_book('s1', SOSEON)
        self.set_status(SOSEON, 'rejected')
        self.assertEqual(self.search('s2', q='소년이')['items'][0]['requestState'], 'rejected')
        self.request_book('s2', CONVENIENCE)
        self.set_status(CONVENIENCE, 'approved')
        self.set_status(CONVENIENCE, 'purchased')
        item = self.search('s2', q='편의점')['items'][0]
        self.assertEqual((item['owned'], item['requestState']), (True, 'owned'))
        self.assertIn('불편한 편의점', [i['title'] for i in self.search(scope='owned', pageSize=100)['items']])
        self.call('lib', 'post', '/api/admin/books/revert', json={'bookIds': [CONVENIENCE]})
        self.assertNotIn('불편한 편의점', [i['title'] for i in self.search(scope='owned', pageSize=100)['items']])

    def test_detail(self):
        body = self.call('s1', 'get', f'/api/books/{SOSEON[:3]}-{SOSEON[3:]}').get_json()
        self.assertEqual((body['id'], body['pubDate'], body['year'], body['price'], body['requestCount'], body['review']), (SOSEON, '2014-05-19', 2014, 15000, 0, None))
        self.assertTrue(body['description'])
        self.request_book('s1', SOSEON)
        self.set_status(SOSEON, 'rejected')
        body = self.call('s2', 'get', f'/api/books/{SOSEON}').get_json()
        self.assertEqual(body['review'], {'status': 'rejected', 'reason': '사유'})
        self.assertEqual((body['requestState'], body['requestCount']), ('rejected', 1))
        response = self.call('s1', 'get', '/api/books/9780306406157')
        self.assertEqual((response.status_code, response.get_json()['error']['message']), (404, '도서를 찾을 수 없어요.'))
        self.assertEqual(self.call('s1', 'get', '/api/books/not-an-isbn').status_code, 404)
        paint = self.call('s1', 'get', f'/api/books/{PAINT}').get_json()
        self.assertTrue(paint['owned'])

    def test_missing_price_is_null(self):
        self.request_book('s1', MODEUN)
        self.assertIsNone(self.call('s1', 'get', f'/api/books/{MODEUN}').get_json()['price'])

    def test_popular_books(self):
        self.assertEqual(self.call('s1', 'get', '/api/books/popular').get_json(), {'items': []})
        for key in ('s1', 's2', 's3'):
            self.request_book(key, SOSEON)
        self.request_book('s1', CONVENIENCE)
        items = self.call('s1', 'get', '/api/books/popular?limit=5').get_json()['items']
        self.assertEqual([(i['book']['title'], i['count']) for i in items], [('소년이 온다', 3), ('불편한 편의점', 1)])
        self.assertEqual(len(self.call('s1', 'get', '/api/books/popular?limit=1').get_json()['items']), 1)
        self.set_status(SOSEON, 'approved')
        self.set_status(SOSEON, 'purchased')  # purchased = owned: leaves the popular list
        self.assertEqual([i['book']['title'] for i in self.call('s1', 'get', '/api/books/popular').get_json()['items']], ['불편한 편의점'])

    def test_provider_failures_and_absence(self):
        class Broken:
            def __init__(self, exc): self.exc = exc
            def search(self, *a): raise self.exc()
            def get(self, *a): raise self.exc()
        for exc, status, code in ((ProviderError, 502, 'BOOK_PROVIDER_ERROR'), (ProviderTimeout, 504, 'BOOK_PROVIDER_TIMEOUT')):
            self.app.config['BOOK_PROVIDER'] = Broken(exc)
            response = self.call('s1', 'get', '/api/books?q=책')
            self.assertEqual((response.status_code, response.get_json()['error']['code']), (status, code))
        self.app.config['BOOK_PROVIDER'] = None
        response = self.call('s1', 'get', '/api/books?q=책')
        self.assertEqual((response.status_code, response.get_json()['error']['code']), (503, 'BOOK_PROVIDER_UNAVAILABLE'))
        self.assertEqual(response.get_json()['error']['message'], '도서 정보를 불러올 수 없어요. 잠시 후 다시 시도해 주세요.')
        self.assertEqual(self.call('s1', 'get', '/api/books').status_code, 200)  # no query needs no provider
        self.assertEqual(self.call('s1', 'get', f'/api/books/{SOSEON}').status_code, 503)
        self.assertEqual(self.call('s1', 'get', f'/api/books/{PAINT}').status_code, 200)  # catalogue still answers

    def test_fixture_refused_outside_debug(self):
        from flask import Flask
        from app.books.sources import configure_sources
        import os
        app = Flask(__name__)
        app.debug = False
        os.environ['BOOK_PROVIDER'] = os.environ['LIBRARY_CATALOG'] = 'fixture'
        try:
            configure_sources(app)
            self.assertIsNone(app.config['BOOK_PROVIDER'])
            self.assertIsNone(app.config['LIBRARY_CATALOG'])
            app.debug = True
            app.config.update(BOOK_PROVIDER=None, LIBRARY_CATALOG=None)
            configure_sources(app)
            self.assertIsNotNone(app.config['BOOK_PROVIDER'])
            os.environ['BOOK_PROVIDER'] = 'dls'
            app.config['BOOK_PROVIDER'] = None
            with self.assertRaises(RuntimeError):
                configure_sources(app)
        finally:
            del os.environ['BOOK_PROVIDER'], os.environ['LIBRARY_CATALOG']


if __name__ == '__main__':
    unittest.main()
