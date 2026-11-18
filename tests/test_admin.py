import unittest
from datetime import datetime
from io import BytesIO
from urllib.parse import quote
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from app.admin import services
from app.books.models import Notification, RequestedBook, ReviewLog
from app.db import db
from app.serializers import KST
from tests.helpers import AppCase, CONVENIENCE, MODEUN, SOSEON

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
CLEAN_CODE = '9788966260959'  # 클린 코드, 33000
SAPIENS = '9788934972464'     # 사피엔스, 22000


def sheet(content):
    """Parse our xlsx: returns (rows of [(value, style, type)], root)."""
    with ZipFile(BytesIO(content)) as archive:
        root = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        names = archive.namelist()
    rows = []
    for row in root.find('m:sheetData', NS):
        cells = []
        for c in row:
            text = ''.join(c.itertext()) if c.get('t') == 'inlineStr' else (c.find('m:v', NS).text if c.find('m:v', NS) is not None else None)
            cells.append((c.get('r'), text, c.get('s'), c.get('t')))
        rows.append(cells)
    return rows, root, names


class AdminTestBase(AppCase):
    def setUp(self):
        super().setUp()
        # A 소년이 온다: s1 (KST 11-17 10:00), s2 (KST 11-18 10:00) | B 불편한 편의점: s3 (KST 11-06 00:30)
        # C 모순(가격 없음): s1 (KST 09-02 09:00) | D 클린 코드: s2 (KST 2026-02-15)
        self.add_request('s1', SOSEON, datetime(2026, 11, 17, 1))
        self.add_request('s2', SOSEON, datetime(2026, 11, 18, 1))
        self.add_request('s3', CONVENIENCE, datetime(2026, 11, 5, 15, 30))
        self.add_request('s1', MODEUN, datetime(2026, 9, 2, 0))
        self.add_request('s2', CLEAN_CODE, datetime(2026, 2, 15, 3))

    def books(self, query=''):
        response = self.call('lib', 'get', '/api/admin/books' + query)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def titles(self, query=''):
        return [i['book']['title'] for i in self.books(query)['items']]

    def message(self, response, status=400):
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()['error']['message']


class AdminListTests(AdminTestBase):
    def test_roles_and_csrf(self):
        self.assertEqual(self.app.test_client().get('/api/admin/books').status_code, 401)
        self.assertEqual(self.message(self.call('s1', 'get', '/api/admin/books'), 403), '사서 선생님 전용 기능이에요.')
        client, _ = self.as_user('lib')
        self.assertEqual(self.message(client.post('/api/admin/books/status', json={}), 403), '요청을 확인할 수 없어요. 새로고침 후 다시 시도해 주세요.')
        self.assertEqual(self.call('lib', 'get', '/api/admin/books').headers['Cache-Control'], 'no-store')

    def test_row_shape_order_summary_and_class_options(self):
        body = self.books()
        self.assertEqual(self.titles(), ['소년이 온다', '불편한 편의점', '모순', '클린 코드'])  # latest first
        row = body['items'][0]
        self.assertEqual(set(row), {'book', 'review', 'requests', 'requesters', 'latestAt', 'firstAt'})
        self.assertEqual([r['userId'] for r in row['requests']], [str(self.users['s2']), str(self.users['s1'])])  # newest first
        self.assertEqual([u['name'] for u in row['requesters']], ['이도윤', '김하늘'])  # same order as requests
        self.assertEqual(row['requesters'][0], {'id': str(self.users['s2']), 'name': '이도윤', 'classNo': '1-4', 'studentNo': '10412'})
        self.assertEqual(row['latestAt'], 1794963600000)  # 2026-11-18T01:00Z, computed independently of the serializer
        self.assertGreater(row['latestAt'], row['firstAt'])
        self.assertEqual(row['review'], {'bookId': SOSEON, 'status': 'pending', 'quantity': 1, 'updatedAt': row['review']['updatedAt']})
        self.assertEqual((body['page'], body['pageSize'], body['total']), (1, 6, 4))
        self.assertEqual(body['summary'], {'total': 4, 'pending': 4, 'approved': 0, 'rejected': 0, 'purchased': 0})
        self.assertEqual(body['classOptions'], ['1-4', '2-3'])

    def test_filters_match_fe_semantics(self):
        self.assertEqual(self.titles('?status=pending'), ['소년이 온다', '불편한 편의점', '모순', '클린 코드'])
        self.assertEqual(self.titles('?status=all'), self.titles())
        self.assertEqual(self.titles('?status=approved'), [])
        # classNo: any requester of the book. A has s1(2-3) and s2(1-4); B has s3(2-3).
        self.assertEqual(self.titles('?classNo=1-4'), ['소년이 온다', '클린 코드'])
        self.assertEqual(self.titles('?classNo=2-3'), ['소년이 온다', '불편한 편의점', '모순'])
        # Dates are KST days; B's request is KST 11-06 00:30 although its UTC date is 11-05.
        self.assertEqual(self.titles('?from=2026-11-06&to=2026-11-06'), ['불편한 편의점'])
        self.assertEqual(self.titles('?to=2026-11-05'), ['모순', '클린 코드'])
        self.assertEqual(self.titles('?from=2026-11-18'), ['소년이 온다'])  # only one request needs to be in range
        self.assertEqual(self.titles('?from=2026-11-17&to=2026-11-17'), ['소년이 온다'])
        # Filters are independent: the 1-4 student (s2) is on A, the date matches only s1's request on A.
        self.assertEqual(self.titles('?classNo=1-4&from=2026-11-17&to=2026-11-17'), ['소년이 온다'])
        # q: substring of "title author names studentNos" (joined with spaces), case-insensitive, trimmed.
        self.assertEqual(self.titles('?q=%20%EC%86%8C%EB%85%84%EC%9D%B4%20'), ['소년이 온다'])
        self.assertEqual(self.titles('?q=한강'), ['소년이 온다'])
        self.assertEqual(self.titles('?q=' + quote('이도윤')), ['소년이 온다', '클린 코드'])
        self.assertEqual(self.titles('?q=20315'), ['소년이 온다', '모순'])
        self.assertEqual(self.titles('?q=' + quote('한강 이도윤')), ['소년이 온다'])  # spans author + requester because of the join
        self.assertEqual(self.titles('?q=' + quote('소년이 한강')), [])  # words are not split; "소년이 한강" is not a substring
        self.assertEqual(self.titles('?q=INSIGHT'), [])
        self.assertEqual(self.titles('?q=' + quote('Clean')), [])

    def test_sort_pagination_and_validation(self):
        self.assertEqual(self.titles('?sort=count'), ['소년이 온다', '불편한 편의점', '모순', '클린 코드'])  # ties keep latest-first
        self.assertEqual(self.titles('?sort=title'), ['모순', '불편한 편의점', '소년이 온다', '클린 코드'])
        self.assertEqual(self.titles('?pageSize=2&page=2'), ['모순', '클린 코드'])
        body = self.books('?pageSize=3&page=9')  # past the end -> last page
        self.assertEqual((body['page'], len(body['items']), body['total']), (2, 1, 4))
        filtered = self.books('?status=approved')
        self.assertEqual((filtered['total'], filtered['summary']['total'], filtered['summary']['pending']), (0, 4, 4))  # summary ignores filters
        for query in ('?sort=x', '?status=done', '?from=2026-13-01', '?from=2026-11-19&to=2026-11-18', '?page=0', '?pageSize=101', '?pageSize=abc', '?from=11/18'):
            self.assertEqual(self.message(self.call('lib', 'get', '/api/admin/books' + query)), '검색 조건을 확인해 주세요.', query)

    def test_hangul_title_order_pinned(self):
        # Mixed Latin/Hangul/digit titles: Python orders digits < Latin(case-insensitive) < Hangul like ICU "ko".
        # Differences from the browser's localeCompare('ko') for exotic punctuation are possible (documented in the PR).
        for index, title in enumerate(('가방', 'Zebra', 'apple', '7번방', '나비')):
            book = RequestedBook(isbn=f'97800000000{index:02d}', title=title, author='저자', publisher='출판사', price=1)
            db.session.add(book)
        db.session.flush()
        from app.books.models import BookRequest
        for book in db.session.query(RequestedBook).filter(RequestedBook.isbn.like('9780000000%')):
            db.session.add(BookRequest(user_id=self.users['s3'], requested_book_id=book.id, requested_at=datetime(2026, 11, 20)))
        db.session.commit()
        ordered = [i['book']['title'] for i in self.books('?sort=title&pageSize=100')['items']]
        self.assertEqual(ordered, ['7번방', 'apple', 'Zebra', '가방', '나비', '모순', '불편한 편의점', '소년이 온다', '클린 코드'])


class AdminActionTests(AdminTestBase):
    def status(self, ids, status, **extra):
        return self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': ids, 'status': status, **extra})

    def test_bulk_skips_ineligible_and_counts_changes(self):
        self.assertEqual(self.status([SOSEON], 'approved').get_json()['changed'], 1)
        # SOSEON is approved now: approve again is skipped; CONVENIENCE (pending) changes; unknown ISBN skipped.
        body = self.status([SOSEON, CONVENIENCE, '9780306406157'], 'approved').get_json()
        self.assertEqual((body['changed'], [i['bookId'] for i in body['items']]), (1, [CONVENIENCE]))
        self.assertEqual(self.status([MODEUN, CLEAN_CODE], 'purchased').get_json()['changed'], 0)  # pending cannot jump to purchased
        self.assertEqual(self.status([SOSEON, CONVENIENCE, MODEUN], 'purchased').get_json()['changed'], 2)
        self.assertEqual(self.status([SOSEON], 'purchased').get_json(), {'changed': 0, 'items': []})
        self.assertEqual(self.status([MODEUN], 'rejected', reason='  이미 있음 ').get_json()['items'][0]['reason'], '이미 있음')

    def test_double_request_creates_no_duplicate_notifications_or_logs(self):
        self.status([SOSEON], 'approved')
        self.status([SOSEON], 'approved')
        self.assertEqual(db.session.query(Notification).count(), 2)
        self.assertEqual(db.session.query(ReviewLog).count(), 1)

    def test_reject_reason_rules(self):
        message = '신청한 학생에게 전달할 반려 사유를 입력해 주세요.'
        for extra in ({}, {'reason': '   '}, {'reason': 5}, {'reason': 'x' * 501}):
            self.assertEqual(self.message(self.status([SOSEON], 'rejected', **extra)), message, extra)
        self.assertEqual(db.session.query(Notification).count(), 0)
        self.assertEqual(self.status([SOSEON], 'rejected', reason='x' * 500).status_code, 200)
        # Other statuses drop any reason.
        self.status([CONVENIENCE], 'approved', reason='ignored')
        self.assertNotIn('reason', self.books('?q=' + quote('편의점'))['items'][0]['review'])
        self.assertIsNone(db.session.query(RequestedBook).filter_by(isbn=CONVENIENCE).one().reject_reason)

    def test_body_validation(self):
        for body in ({}, {'bookIds': [], 'status': 'approved'}, {'bookIds': 'x', 'status': 'approved'}, {'bookIds': [SOSEON], 'status': 'pending'},
                     {'bookIds': ['bad'], 'status': 'approved'}, {'bookIds': [SOSEON] * 1001, 'status': 'approved'}):
            self.assertEqual(self.call('lib', 'post', '/api/admin/books/status', json=body).status_code, 400, body)
        self.assertEqual(self.status([SOSEON, SOSEON], 'approved').get_json()['changed'], 1)  # duplicates collapse

    def test_notifications_logs_and_ownership_effects(self):
        self.status([SOSEON], 'rejected', reason='중복')
        notes = db.session.query(Notification).order_by(Notification.id).all()
        self.assertEqual({(n.user_id, n.type, n.reason) for n in notes}, {(self.users['s1'], 'rejected', '중복'), (self.users['s2'], 'rejected', '중복')})
        self.assertTrue(all(n.request_id and n.requested_book_id for n in notes))
        log = db.session.query(ReviewLog).one()
        self.assertEqual((log.status, log.actor_id), ('rejected', self.users['lib']))

    def test_revert_rules(self):
        self.status([SOSEON], 'approved')
        self.status([CONVENIENCE], 'rejected', reason='사유')
        self.status([MODEUN], 'approved')
        self.status([MODEUN], 'purchased')
        notes, logs = db.session.query(Notification).count(), db.session.query(ReviewLog).count()
        body = self.call('lib', 'post', '/api/admin/books/revert', json={'bookIds': [SOSEON, CONVENIENCE, MODEUN, CLEAN_CODE]}).get_json()
        self.assertEqual(body['changed'], 3)  # pending CLEAN_CODE skipped
        states = {i['bookId']: i for i in body['items']}
        self.assertEqual((states[SOSEON]['status'], states[CONVENIENCE]['status'], states[MODEUN]['status']), ('pending', 'pending', 'approved'))
        self.assertNotIn('reason', states[CONVENIENCE])
        self.assertEqual((db.session.query(Notification).count(), db.session.query(ReviewLog).count()), (notes, logs))
        self.assertEqual(self.call('lib', 'post', '/api/admin/books/revert', json={'bookIds': [SOSEON]}).get_json()['changed'], 0)
        self.assertEqual(self.call('lib', 'post', '/api/admin/books/revert', json={}).status_code, 400)

    def test_price_and_quantity(self):
        ok = self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/price', json={'price': 12000})
        self.assertEqual((ok.status_code, ok.get_json()['price'], ok.get_json()['id']), (200, 12000, MODEUN))
        self.assertIsNone(self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/price', json={'price': None}).get_json()['price'])
        self.assertEqual(self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/price', json={'price': 0}).get_json()['price'], 0)  # 0 != null
        for bad in (-1, 1.5, '100', True, 2**31):
            self.assertEqual(self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/price', json={'price': bad}).status_code, 400, bad)
        self.assertEqual(self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/price', json={}).status_code, 400)
        self.assertEqual(self.call('lib', 'patch', '/api/admin/books/9780306406157/price', json={'price': 1}).status_code, 404)
        self.assertEqual(self.message(self.call('lib', 'patch', '/api/admin/books/zzz/price', json={'price': 1}), 404), '도서를 찾을 수 없어요.')
        for given, expected in ((3, 3), (0, 0), (2.9, 2), (-4, 0), (-0.5, 0)):
            body = self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': given}).get_json()
            self.assertEqual(body['quantity'], expected, given)
        self.assertEqual(self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': 'a'}).status_code, 400)
        self.assertEqual(self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': True}).status_code, 400)
        self.assertEqual(self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': float('inf')}).status_code, 400)
        self.assertEqual(self.call('lib', 'patch', '/api/admin/books/9780306406157/quantity', json={'quantity': 1}).status_code, 404)


class AdminExportTests(AdminTestBase):
    def test_books_export_columns_formats_and_filename(self):
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': 'approved'})
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [MODEUN], 'status': 'rejected', 'reason': '이미 소장 중'})
        response = self.call('lib', 'post', '/api/admin/books/export', json={'sort': 'title'})
        self.assertEqual(response.status_code, 200)
        disposition = response.headers['Content-Disposition']
        today = datetime.now(KST).strftime('%Y%m%d')
        self.assertIn(f"filename*=UTF-8''{quote('신청도서목록')}_{today}.xlsx", disposition)
        rows, root, names = sheet(response.data)
        self.assertIn('xl/styles.xml', names)
        text = lambda row: [c[1] for c in row]
        self.assertEqual(text(rows[0]), ['도서명', '저자', '출판사', 'ISBN', '가격', '신청 수', '신청 학생', '최근 신청일', '검토 결과', '구매 여부', '반려 사유'])
        self.assertTrue(all(c[2] == '2' for c in rows[0]))  # bold header
        self.assertEqual([r[0][1] for r in rows[1:]], ['모순', '불편한 편의점', '소년이 온다', '클린 코드'])  # sort carried over
        moson = rows[1]
        self.assertEqual([c[0] for c in moson], ['A2', 'B2', 'C2', 'D2', 'F2', 'G2', 'H2', 'I2', 'J2', 'K2'])  # price is a blank cell
        self.assertEqual(text(moson)[5:], ['김하늘(2-3)', '2026.09.02', '반려', '-', '이미 소장 중'])
        sosyeon = rows[3]
        self.assertEqual((sosyeon[4][1], sosyeon[4][2], sosyeon[4][3], sosyeon[5][1]), ('15000', '1', None, '2'))  # number cell with #,##0 style
        self.assertEqual(text(sosyeon)[6:], ['이도윤(1-4), 김하늘(2-3)', '2026.11.18', '승인', '미구매', ''])
        self.assertEqual(sosyeon[3][1], SOSEON)
        self.assertEqual(sosyeon[3][3], 'inlineStr')  # ISBN stays text
        pane = root.find('m:sheetViews/m:sheetView/m:pane', NS)
        self.assertEqual((pane.get('ySplit'), pane.get('state')), ('1', 'frozen'))
        widths = [float(c.get('width')) for c in root.find('m:cols', NS)]
        self.assertEqual(widths, [32, 22, 16, 16, 10, 8, 40, 12, 10, 10, 40])

    def test_purchased_row_and_filters_in_export(self):
        for status in ('approved', 'purchased'):
            self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': status})
        rows = sheet(self.call('lib', 'post', '/api/admin/books/export', json={'status': 'purchased'}).data)[0]
        self.assertEqual(len(rows), 2)
        self.assertEqual([c[1] for c in rows[1]][8:], ['승인', '구매완료', ''])  # purchased is "승인" in review, "구매완료" in purchase
        filtered = sheet(self.call('lib', 'post', '/api/admin/books/export', json={'classNo': '1-4', 'q': ' 이도윤 ', 'from': '2026-11-01', 'to': '', 'status': 'all'}).data)[0]
        self.assertEqual([r[0][1] for r in filtered[1:]], ['소년이 온다'])
        self.assertEqual(self.call('lib', 'post', '/api/admin/books/export', json={'from': '2026-12-01', 'to': '2026-11-01'}).status_code, 400)

    def test_formula_like_text_is_never_a_formula(self):
        db.session.query(RequestedBook).filter_by(isbn=SOSEON).one().title = '=1+1'
        db.session.commit()
        rows, root, _ = sheet(self.call('lib', 'post', '/api/admin/books/export', json={}).data)
        self.assertIsNone(root.find('.//m:f', NS))
        cell = next(r[0] for r in rows if r[0][1] == '=1+1')
        self.assertEqual(cell[3], 'inlineStr')

    def test_budget_export(self):
        self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/price', json={'price': 10000})
        self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': 2})
        for status in ('approved', 'purchased'):
            self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [CONVENIENCE], 'status': status})
        response = self.call('lib', 'post', '/api/admin/budget/export', json={'period': 'all', 'include': ['pending', 'approved']})
        self.assertIn(f"filename*=UTF-8''{quote('예상예산')}_", response.headers['Content-Disposition'])
        rows, root, _ = sheet(response.data)
        values = [[c[1] for c in r] for r in rows]
        self.assertEqual(values[0], ['구분', '도서명', '상태', '단가', '구매 수량', '소계'])
        self.assertEqual(values[1], ['예상', '소년이 온다', '대기', '15000', '2', '30000'])
        self.assertEqual(values[2], ['예상', '모순', '대기', '10000', '1', '10000'])
        self.assertEqual(values[3], ['예상', '클린 코드', '대기', '33000', '1', '33000'])
        self.assertEqual(values[4], ['구매완료', '불편한 편의점', '구매완료', '14000', '1', '14000'])
        self.assertEqual(values[5], ['예상 예산 합계', '73000'])  # blanks are empty cells; only label and total exist
        self.assertEqual(values[6], ['구매완료 합계', '14000'])
        self.assertEqual([c[0] for c in rows[5]], ['A6', 'F6'])
        self.assertEqual(rows[5][1][2], '1')  # total is a number cell with the #,##0 style
        self.assertEqual([float(c.get('width')) for c in root.find('m:cols', NS)], [16, 32, 10, 12, 10, 14])
        self.assertEqual(self.call('lib', 'post', '/api/admin/budget/export', json={'period': 'week'}).status_code, 400)


class BudgetTests(AdminTestBase):
    def budget(self, query=''):
        response = self.call('lib', 'get', '/api/admin/budget' + query)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_defaults_totals_and_missing_price(self):
        # Test "now" is real; use period=all so fixed 2026 dates always count.
        body = self.budget('?period=all')
        self.assertEqual(body['include'], ['pending', 'approved'])
        titles = [e['book']['title'] for e in body['expected']]
        self.assertEqual(titles, ['소년이 온다', '불편한 편의점', '모순', '클린 코드'])
        self.assertEqual(body['expectedTotal'], 15000 + 14000 + 0 + 33000)  # missing price counts as 0
        self.assertEqual(body['missingPriceCount'], 1)
        entry = {e['book']['title']: e for e in body['expected']}
        self.assertIsNone(entry['모순']['subtotal'])
        self.assertEqual((entry['소년이 온다']['subtotal'], entry['소년이 온다']['latestAt'] > entry['소년이 온다']['firstAt']), (15000, True))
        self.assertEqual(set(entry['소년이 온다']), {'book', 'review', 'subtotal', 'latestAt', 'firstAt'})

    def test_price_zero_vs_null_and_quantity_zero(self):
        self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/price', json={'price': 0})
        self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': 0})
        body = self.budget('?period=all')
        entry = {e['book']['title']: e for e in body['expected']}
        self.assertEqual((entry['모순']['subtotal'], entry['소년이 온다']['subtotal']), (0, 0))  # 0 won is a price; qty 0 gives 0
        self.assertEqual(body['missingPriceCount'], 0)

    def test_include_combinations_and_status_totals(self):
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': 'approved'})
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [CONVENIENCE], 'status': 'rejected', 'reason': 'x'})
        for status in ('approved', 'purchased'):
            self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [CLEAN_CODE], 'status': status})
        body = self.budget('?period=all&include=approved')
        self.assertEqual([e['book']['title'] for e in body['expected']], ['소년이 온다'])
        self.assertEqual([e['book']['title'] for e in body['purchased']], ['클린 코드'])
        self.assertEqual(body['statusTotals'], {'pending': 0, 'approved': 15000, 'rejected': 14000, 'purchased': 33000})  # ignores include
        both = self.budget('?period=all&include=approved,rejected,pending')
        self.assertEqual((both['include'], both['expectedTotal']), (['pending', 'approved', 'rejected'], 15000 + 14000))
        none = self.budget('?period=all&include=')
        self.assertEqual((none['include'], none['expected'], none['expectedTotal']), ([], [], 0))
        self.assertEqual(none['purchasedTotal'], 33000)
        for query in ('?include=purchased', '?include=pending,bogus', '?period=month'):
            self.assertEqual(self.call('lib', 'get', '/api/admin/budget' + query).status_code, 400, query)

    def test_semester_and_year_boundaries(self):
        kst = lambda *a: datetime(*a, tzinfo=KST)
        start = lambda period, today: services.period_start(period, today)
        self.assertEqual(start('semester', kst(2026, 2, 15)), datetime(2025, 8, 31, 15))  # Jan-Feb -> last year's Sep 1 (KST)
        self.assertEqual(start('semester', kst(2026, 1, 1)), datetime(2025, 8, 31, 15))
        self.assertEqual(start('semester', kst(2026, 3, 1)), datetime(2026, 2, 28, 15))   # March 1
        self.assertEqual(start('semester', kst(2026, 8, 31)), datetime(2026, 2, 28, 15))
        self.assertEqual(start('semester', kst(2026, 9, 1)), datetime(2026, 8, 31, 15))   # September 1
        self.assertEqual(start('semester', kst(2026, 12, 31)), datetime(2026, 8, 31, 15))
        self.assertEqual(start('year', kst(2026, 6, 1)), datetime(2025, 12, 31, 15))
        self.assertIsNone(start('all', kst(2026, 6, 1)))
        # Applied to data: on 2026-04-10 only requests at/after KST 03-01 00:00 count.
        self.add_request('s3', SAPIENS, datetime(2026, 2, 28, 15))      # KST 03-01 00:00 exactly: included
        self.add_request('s3', '9788934942467', datetime(2026, 2, 28, 14, 59, 59))  # 총 균 쇠: KST 02-28 23:59:59, one second early
        data = services.budget({'period': 'semester', 'include': 'pending'}, today=kst(2026, 4, 10))
        titles = [e['book']['title'] for e in data['expected']]
        self.assertIn('사피엔스', titles)
        self.assertNotIn('총 균 쇠', titles)
        self.assertNotIn('클린 코드', titles)  # KST 02-15 is last semester
        self.assertIn('소년이 온다', titles)
        data = services.budget({'period': 'semester', 'include': 'pending'}, today=kst(2026, 2, 20))
        self.assertIn('클린 코드', [e['book']['title'] for e in data['expected']])  # Feb belongs to last autumn's semester

    def test_latest_request_decides_period(self):
        # Book with an old first request and a new one is in period (latestAt >= start).
        self.add_request('s3', CLEAN_CODE, datetime(2026, 11, 10))
        data = services.budget({'period': 'semester', 'include': 'pending'}, today=datetime(2026, 11, 18, tzinfo=KST))
        self.assertIn('클린 코드', [e['book']['title'] for e in data['expected']])

    def test_monthly_buckets_use_first_request_and_ignore_period(self):
        today = datetime(2026, 11, 18, 12, tzinfo=KST)
        self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': 2})
        self.add_request('s3', CLEAN_CODE, datetime(2026, 11, 10))  # latest in Nov but first request in Feb
        monthly = services.budget({'period': 'semester', 'include': 'pending,approved'}, today=today)['monthly']
        self.assertEqual([m['label'] for m in monthly], ['6월', '7월', '8월', '9월', '10월', '11월'])
        self.assertEqual([m['books'] for m in monthly], [0, 0, 0, 1, 0, 2])
        self.assertEqual([m['amount'] for m in monthly], [0, 0, 0, 0, 0, 30000 + 14000])  # 모순 has no price (0), September
        # Rejected books only count when included; purchased always count.
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [CONVENIENCE], 'status': 'rejected', 'reason': 'x'})
        monthly = services.budget({'period': 'all', 'include': 'pending'}, today=today)['monthly']
        self.assertEqual(monthly[5]['books'], 1)
        # A window that crosses a year boundary.
        january = services.budget({'period': 'all', 'include': 'pending'}, today=datetime(2027, 1, 5, tzinfo=KST))['monthly']
        self.assertEqual([m['label'] for m in january], ['8월', '9월', '10월', '11월', '12월', '1월'])
        self.assertEqual(january[1]['books'], 1)


class SummaryTests(AdminTestBase):
    def test_summary_counts_weeks_and_recent_logs(self):
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': 'approved'})
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [CONVENIENCE], 'status': 'rejected', 'reason': 'x'})
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': 'purchased'})
        self.call('lib', 'post', '/api/admin/books/revert', json={'bookIds': [CONVENIENCE]})  # reverts leave no history
        body = self.call('lib', 'get', '/api/admin/summary').get_json()
        self.assertEqual((body['pending'], body['weekApproved'], body['weekRejected'], body['purchasedTotal']), (3, 1, 1, 1))
        self.assertEqual([(l['bookTitle'], l['status']) for l in body['recentLogs']], [('소년이 온다', 'purchased'), ('불편한 편의점', 'rejected'), ('소년이 온다', 'approved')])
        self.assertEqual(set(body['recentLogs'][0]), {'id', 'at', 'bookId', 'bookTitle', 'status'})
        self.assertIsInstance(body['recentLogs'][0]['at'], int)

    def test_week_starts_monday_midnight_kst_and_recent_limit(self):
        # Wednesday 2026-11-18 -> Monday 2026-11-16 00:00 KST = 2026-11-15 15:00 UTC
        self.assertEqual(services.week_start(datetime(2026, 11, 18, 9, tzinfo=KST)), datetime(2026, 11, 15, 15))
        self.assertEqual(services.week_start(datetime(2026, 11, 16, 0, 0, tzinfo=KST)), datetime(2026, 11, 15, 15))
        self.assertEqual(services.week_start(datetime(2026, 11, 22, 23, 59, tzinfo=KST)), datetime(2026, 11, 15, 15))  # Sunday
        book = db.session.query(RequestedBook).first()
        for i in range(10):
            db.session.add(ReviewLog(requested_book_id=book.id, status='approved', actor_id=self.users['lib'], created_at=datetime(2026, 1, 1, i)))
        db.session.commit()
        body = self.call('lib', 'get', '/api/admin/summary').get_json()
        self.assertEqual(len(body['recentLogs']), 8)
        self.assertEqual(body['weekApproved'], 0)  # all of those are old


if __name__ == '__main__':
    unittest.main()
