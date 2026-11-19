"""Optional Kakao book-search adapter (requires a server-side REST API key)."""
import json
import socket
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.books.isbn import normalize_isbn
from app.books.providers import ProviderError, ProviderTimeout, ProviderRateLimited

URL = 'https://dapi.kakao.com/v3/search/book'
MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class KakaoBookProvider:
    """Normalize external data; this adapter does not determine school ownership."""
    max_page_size = 50

    def __init__(self, api_key, *, opener=None, timeout=5):
        if not isinstance(api_key, str) or not api_key.strip() or '\n' in api_key or '\r' in api_key:
            raise ValueError('A valid Kakao REST API key is required')
        self._api_key = api_key.strip()
        self._opener = opener or build_opener(_NoRedirect())
        self._timeout = timeout

    def _query(self, query, page, page_size, target=None):
        if not 1 <= page <= 50 or not 1 <= page_size <= 50:
            raise ValueError('Kakao supports pages and page sizes from 1 to 50')
        params = {'query': query, 'page': page, 'size': page_size}
        if target:
            params['target'] = target
        request = Request(URL + '?' + urlencode(params), headers={'Authorization': 'KakaoAK ' + self._api_key, 'Accept': 'application/json'})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ProviderError('Response too large')
            payload = json.loads(body)
        except HTTPError as exc:
            if exc.code == 429:
                raise ProviderRateLimited() from None
            raise ProviderError() from None
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeout() from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeout() from exc
            raise ProviderError() from None
        except (ValueError, UnicodeError):
            raise ProviderError('Invalid provider response') from None
        if not isinstance(payload, dict) or not isinstance(payload.get('documents'), list) or not isinstance(payload.get('meta'), dict) or type(payload['meta'].get('is_end')) is not bool:
            raise ProviderError('Invalid provider response')
        return payload

    @staticmethod
    def _book(doc):
        if not isinstance(doc, dict) or not isinstance(doc.get('title'), str) or not doc['title'].strip():
            raise ProviderError('Invalid book data')
        raw_isbn = doc.get('isbn') or ''
        if not isinstance(raw_isbn, str):
            raise ProviderError('Invalid book data')
        isbn = None
        # Kakao can return an ISBN-10 and ISBN-13 that do not represent the
        # same edition. Prefer an explicitly supplied valid ISBN-13.
        candidates = sorted(raw_isbn.split(), key=lambda value: len(value.replace('-', '')) != 13)
        for candidate in candidates:
            try:
                isbn = normalize_isbn(candidate)
                break
            except ValueError:
                continue
        authors = doc.get('authors') or []
        if not isinstance(authors, list) or any(not isinstance(a, str) for a in authors):
            raise ProviderError('Invalid book data')
        price = doc.get('price')
        if type(price) is not int or price < 0:
            price = None
        def optional_string(key):
            value = doc.get(key)
            return value.strip() if isinstance(value, str) and value.strip() else None
        try:
            pub_date = datetime.fromisoformat(optional_string('datetime') or '').date()
        except ValueError:
            pub_date = None
        return {'isbn': isbn, 'title': doc['title'].strip(), 'author': ', '.join(authors),
                'publisher': optional_string('publisher'), 'price': price,
                'cover_url': optional_string('thumbnail'), 'description': optional_string('contents'),
                'pub_date': pub_date}

    def search(self, query, page, page_size):
        result = self._query(query, page, page_size)
        return {'items': [self._book(row) for row in result['documents']], 'page': page,
                'page_size': page_size, 'has_next': not result['meta']['is_end'] and page < 50,
                'catalog_updated_at': None}

    def get(self, isbn):
        isbn = normalize_isbn(isbn)
        result = self._query(isbn, 1, 50, target='isbn')
        for row in result['documents']:
            book = self._book(row)
            if book['isbn'] == isbn:
                return book
        return None
