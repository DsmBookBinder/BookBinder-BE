from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from app.books.dls import REQUIRED, SHEET
from app.books.models import RequestedBook
from app.books.sources import configure_sources
from tests.helpers import AppCase, SOSEON, CONVENIENCE
from app.db import db


class ClientDataTests(AppCase):
    def make_catalogue(self, rows, header):
        directory = self.enterContext(TemporaryDirectory())
        path = Path(directory) / 'catalogue.xlsx'
        workbook = Workbook()
        workbook.active.title = SHEET
        workbook.active.append(header)
        for row in rows:
            workbook.active.append(row)
        workbook.save(path)
        workbook.close()
        self.app.config['LIBRARY_CATALOG'] = None
        with patch.dict('os.environ', {'LIBRARY_CATALOG': 'dls', 'DLS_CATALOG_XLSX': str(path)}):
            configure_sources(self.app)

    def test_isbnless_entries_are_searchable_but_do_not_verify_external_ownership(self):
        row = ('SYNTHETIC1', '어린 왕자 테스트', None, '테스트 출판사', 2024, '대출중')
        self.make_catalogue([row, row], (*REQUIRED, '자료상태'))
        result = self.call('s1', 'get', '/api/books', query_string={'scope': 'owned', 'q': '어린왕자'}).get_json()
        self.assertEqual(result['total'], 1)
        book = result['items'][0]
        self.assertEqual((book['isbn'], book['requestState'], book['year'], book['libraryStatus']), (None, 'owned', 2024, '대출중'))
        self.assertNotIn('SYNTHETIC1', str(book))
        detail = self.call('s1', 'get', f"/api/books/{book['id']}").get_json()
        self.assertEqual(detail['ownership'], 'OWNED')
        external = self.call('s1', 'get', f'/api/books/{SOSEON}').get_json()
        self.assertEqual(external['ownership'], 'UNKNOWN')
        response = self.call('s1', 'post', '/api/requests', json={'bookId': SOSEON})
        self.assertEqual((response.status_code, response.get_json()['error']['code']), (503, 'LIBRARY_CATALOG_UNAVAILABLE'))

    def test_catalogue_with_isbn_blocks_owned_and_allows_verified_unowned(self):
        self.make_catalogue([('TEST', '도서', None, '출판사', 2024, SOSEON)], (*REQUIRED, 'ISBN'))
        self.assertEqual(self.call('s1', 'post', '/api/requests', json={'bookId': SOSEON}).status_code, 409)
        self.assertEqual(self.call('s1', 'post', '/api/requests', json={'bookId': CONVENIENCE}).status_code, 201)

    def test_demo_admits_unknown_isbn_and_sends_it_through_admin_review(self):
        rows = [('LOCAL', 'ISBN 없는 소장 도서', None, '출판사', 2024, None),
                ('KNOWN', '확인된 소장 도서', None, '출판사', 2024, CONVENIENCE)]
        self.make_catalogue(rows, (*REQUIRED, 'ISBN'))
        with patch.dict('os.environ', {'BOOKBINDER_DEMO_ASSUME_UNOWNED': 'true'}):
            with self.assertRaises(RuntimeError):
                configure_sources(self.app)
            self.app.debug = True
            configure_sources(self.app)
            self.app.debug = False
        self.assertEqual(self.call('s1', 'get', '/api/me').headers['X-BookBinder-Data'], 'demo')
        self.assertEqual(self.call('s1', 'get', f'/api/books/{SOSEON}').get_json()['ownership'], 'NOT_OWNED')
        self.assertEqual(self.call('s1', 'post', '/api/requests', json={'bookId': CONVENIENCE}).status_code, 409)
        self.assertEqual(self.call('s1', 'post', '/api/requests', json={'bookId': SOSEON}).status_code, 201)
        items = self.call('lib', 'get', '/api/admin/books').get_json()['items']
        self.assertEqual((items[0]['book']['id'], items[0]['review']['status']), (SOSEON, 'pending'))
        self.set_status(SOSEON, 'approved')
        self.assertEqual(self.call('s1', 'get', '/api/me/notifications').get_json()['items'][0]['status'], 'approved')

    def test_import_rejects_malformed_workbook_with_row_number(self):
        with self.assertRaisesRegex(RuntimeError, '2행.*ISBN'):
            self.make_catalogue([('TEST', '도서', None, '출판사', 2024, 'invalid')], (*REQUIRED, 'ISBN'))

    def test_school_order_uses_approved_books_quantity_and_formulas(self):
        for isbn in (SOSEON, CONVENIENCE):
            self.assertEqual(self.call('s1', 'post', '/api/requests', json={'bookId': isbn}).status_code, 201)
        book = RequestedBook.query.filter_by(isbn=SOSEON).one()
        book.status, book.quantity, book.price, book.title = 'approved', 3, 15000, '=1+1'
        db.session.commit()
        body = {'format': 'school'}
        response = self.call('lib', 'post', '/api/admin/books/export', json=body)
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.data), read_only=True)
        self.addCleanup(workbook.close)
        sheet = workbook.active
        self.assertEqual(sheet['E3'].value, '정가(할인가X)')
        self.assertEqual([sheet[c].value for c in ('E4', 'F4', 'G4', 'F5', 'G5')],
                         [15000, 3, '=E4*F4', '=SUM(F4:F4)', '=SUM(G4:G4)'])
        self.assertEqual(sheet['B4'].data_type, 's')
        self.assertEqual(sheet['B4'].value, '=1+1')
        self.assertEqual(sheet.max_row, 5)
        book.price = None
        db.session.commit()
        result = self.call('lib', 'post', '/api/admin/books/export', json=body)
        self.assertEqual((result.status_code, result.get_json()['error']['code']), (409, 'PRICE_REQUIRED'))
