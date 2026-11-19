import io
import json
import socket
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from app.books.kakao import KakaoBookProvider
from app.books.providers import ProviderError, ProviderRateLimited, ProviderTimeout


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size): return self.payload[:size]


class FakeOpener:
    def __init__(self, payload): self.payload, self.calls = payload, []
    def open(self, req, timeout):
        self.calls.append((req, timeout))
        return FakeResponse(json.dumps(self.payload, ensure_ascii=False).encode())


class KakaoTests(unittest.TestCase):
    def setUp(self):
        self.doc = {'title': '책', 'isbn': '0306406152 9780306406157', 'authors': ['저자'], 'publisher': '출판사', 'price': 12000, 'sale_price': 9000, 'thumbnail': '', 'contents': '소개', 'datetime': '2020-01-01T00:00:00.000+09:00'}
        self.opener = FakeOpener({'meta': {'is_end': True}, 'documents': [self.doc]})
        self.provider = KakaoBookProvider('test-key', opener=self.opener)

    def test_search_normalizes_and_uses_list_price(self):
        result = self.provider.search('책 제목', 1, 20)
        item = result['items'][0]
        self.assertEqual((item['isbn'], item['title'], item['author'], item['publisher'], item['price']), ('9780306406157', '책', '저자', '출판사', 12000))
        self.assertEqual(item['pub_date'].isoformat(), '2020-01-01')
        self.assertFalse(result['has_next'])
        req, timeout = self.opener.calls[0]
        self.assertEqual(req.get_header('Authorization'), 'KakaoAK test-key')
        self.assertEqual(parse_qs(urlparse(req.full_url).query)['query'], ['책 제목'])
        self.assertEqual(timeout, 5)

    def test_prefers_explicit_isbn13_over_different_isbn10(self):
        self.doc['isbn'] = '1190669315 9791190669313'
        self.assertEqual(self.provider.search('x', 1, 20)['items'][0]['isbn'], '9791190669313')

    def test_details_require_exact_isbn(self):
        self.assertEqual(self.provider.get('0306406152')['isbn'], '9780306406157')
        self.assertEqual(parse_qs(urlparse(self.opener.calls[-1][0].full_url).query)['target'], ['isbn'])
        self.assertIsNone(self.provider.get('9780804429573'))

    def test_invalid_isbn_and_unknown_price(self):
        self.doc['isbn'], self.doc['price'] = 'invalid', -1
        book = self.provider.search('x', 1, 20)['items'][0]
        self.assertIsNone(book['isbn'])
        self.assertIsNone(book['price'])

    def test_provider_limits_and_errors(self):
        with self.assertRaises(ValueError): self.provider.search('x', 1, 51)
        with patch.object(self.opener, 'open', side_effect=HTTPError('https://example.test', 429, '', {}, io.BytesIO())):
            with self.assertRaises(ProviderRateLimited): self.provider.search('x', 1, 20)
        with patch.object(self.opener, 'open', side_effect=URLError(socket.timeout())):
            with self.assertRaises(ProviderTimeout): self.provider.search('x', 1, 20)
        self.opener.payload = {'meta': {}, 'documents': []}
        with self.assertRaises(ProviderError): self.provider.search('x', 1, 20)


if __name__ == '__main__': unittest.main()
