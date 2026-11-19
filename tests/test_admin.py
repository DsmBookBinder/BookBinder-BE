import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import patch
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


@contextmanager
def fixed_clock(utc_naive):
    """Freeze 'now' for status changes, summaries, budgets and download names (utc_naive is a naive UTC datetime)."""
    aware = utc_naive.replace(tzinfo=timezone.utc).astimezone(KST)
    with ExitStack() as stack:
        stack.enter_context(patch('app.admin.services.now', return_value=utc_naive))
        stack.enter_context(patch('app.admin.services.kst_now', return_value=aware))
        stack.enter_context(patch('app.admin.routes.kst_now', return_value=aware))
        yield


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
        self.assertEqual(self.message(self.call('s1', 'get', '/api/admin/books'), 403), '관리자 전용 기능이에요.')
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


@contextmanager
def locking_selects():
    """Collect the table of every SELECT ... FOR UPDATE built while active (SQLite ignores the lock itself)."""
    from sqlalchemy import event
    seen = []
    def record(_conn, clause, _multi, _params, _options):
        if getattr(clause, '_for_update_arg', None) is not None:
            seen.append(clause.get_final_froms()[0].name)
    event.listen(db.engine, 'before_execute', record)
    try:
        yield seen
    finally:
        event.remove(db.engine, 'before_execute', record)


class AdminConcurrencyTests(AdminTestBase):
    def status(self, ids, status, **extra):
        return self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': ids, 'status': status, **extra})

    def test_books_are_locked_in_isbn_order_whatever_the_request_order(self):
        # SOSEON (9788...) sorts before CONVENIENCE (9791...). [A,B] and [B,A] must lock in the same order.
        for action, url, body in (('status', '/api/admin/books/status', {'status': 'approved'}), ('revert', '/api/admin/books/revert', {})):
            if action == 'revert':
                self.status([SOSEON, CONVENIENCE], 'approved')
            for ids in ([SOSEON, CONVENIENCE], [CONVENIENCE, SOSEON]):
                if action == 'revert':
                    self.status(ids, 'approved')  # skipped when already approved; keeps state approved for the revert
                calls = []
                real = services.locked_row
                with patch('app.admin.services.locked_row', side_effect=lambda isbn: (calls.append(isbn), real(isbn))[1]):
                    response = self.call('lib', 'post', url, json={'bookIds': ids, **body})
                self.assertEqual(calls, [SOSEON, CONVENIENCE], (action, ids))
                if response.get_json()['changed'] == 2:
                    self.assertEqual([i['bookId'] for i in response.get_json()['items']], ids)  # response keeps the request order
                if action == 'status':
                    self.call('lib', 'post', '/api/admin/books/revert', json={'bookIds': ids})

    def test_stamp_is_taken_after_the_lock_and_only_for_changed_books(self):
        events = []
        real_lock = services.locked_row
        def lock(isbn):
            events.append(('lock', isbn))
            return real_lock(isbn)
        def clock():
            events.append(('now',))
            return datetime(2026, 11, 18)
        with patch('app.admin.services.locked_row', side_effect=lock), patch('app.admin.services.now', side_effect=clock):
            # MODEUN is pending too, but SOSEON is approved first so that it is skipped below.
            self.status([SOSEON], 'approved')
            events.clear()
            self.status([SOSEON, CONVENIENCE], 'approved')
        # SOSEON is no longer pending: locked, skipped, no stamp. CONVENIENCE: locked, then stamped.
        self.assertEqual(events, [('lock', SOSEON), ('lock', CONVENIENCE), ('now',)])

    def test_requester_reads_use_locking_selects(self):
        with locking_selects() as seen:
            self.status([SOSEON], 'approved')
        self.assertIn('requested_books', seen)
        self.assertGreaterEqual(seen.count('book_requests'), 2)  # "has requests" check and the notification fan-out
        self.assertEqual(db.session.query(Notification).count(), 2)

    def test_requester_added_before_approval_is_notified(self):
        # Ordering sanity check only: the request is committed BEFORE the approval call, so this passes even with a
        # plain SELECT. SQLite cannot reproduce MySQL's REPEATABLE READ snapshot; the guard against a stale snapshot is
        # the locking-read check above, and the real two-connection case needs a MySQL test database (not run here).
        self.add_request('s3', SOSEON, datetime(2026, 11, 19, 1))
        self.status([SOSEON], 'approved')
        recipients = {n.user_id for n in db.session.query(Notification)}
        self.assertEqual(recipients, {self.users['s1'], self.users['s2'], self.users['s3']})

    def test_failure_on_second_book_rolls_everything_back_and_retry_is_clean(self):
        from app.books.models import Notification as Real
        calls = []
        def flaky(**kwargs):
            calls.append(1)
            if len(calls) == 3:  # SOSEON has two requesters, so the 3rd notification belongs to CONVENIENCE
                raise RuntimeError('notification could not be saved')
            return Real(**kwargs)
        with patch('app.admin.services.Notification', side_effect=flaky):
            response = self.status([SOSEON, CONVENIENCE], 'approved')
        self.assertEqual(response.status_code, 500)
        db.session.expire_all()
        self.assertEqual({b.status for b in db.session.query(RequestedBook)}, {'pending'})
        self.assertEqual((db.session.query(Notification).count(), db.session.query(ReviewLog).count()), (0, 0))
        retry = self.status([SOSEON, CONVENIENCE], 'approved')
        self.assertEqual(retry.get_json()['changed'], 2)
        again = self.status([SOSEON, CONVENIENCE], 'approved')
        self.assertEqual(again.get_json()['changed'], 0)
        self.assertEqual((db.session.query(Notification).count(), db.session.query(ReviewLog).count()), (3, 2))  # no duplicates

    def test_list_reads_the_rows_once(self):
        calls = []
        real = services.load_rows
        with patch('app.admin.services.load_rows', side_effect=lambda: (calls.append(1), real())[1]):
            body = self.books('?status=pending')
        self.assertEqual(len(calls), 1)
        self.assertEqual((body['total'], body['summary']['total']), (4, 4))
        calls.clear()
        with patch('app.admin.services.load_rows', side_effect=lambda: (calls.append(1), real())[1]):
            self.call('lib', 'post', '/api/admin/books/export', json={})
        self.assertEqual(len(calls), 1)


class AdminInputTests(AdminTestBase):
    def test_status_must_be_a_string(self):
        for bad in ([], {}, ['approved'], 5, None):
            response = self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': bad})
            self.assertEqual((response.status_code, response.get_json()['error']['code']), (400, 'VALIDATION_ERROR'), bad)

    def test_exports_reject_broken_bodies_instead_of_exporting_everything(self):
        for url in ('/api/admin/books/export', '/api/admin/budget/export'):
            for raw in ('{broken', '[]', '"text"', '5', 'null'):
                response = self.call('lib', 'post', url, data=raw, content_type='application/json')
                self.assertEqual(response.status_code, 400, (url, raw))
                self.assertEqual(response.get_json()['error']['code'], 'VALIDATION_ERROR')
            self.assertEqual(self.call('lib', 'post', url, data='x', content_type='text/plain').status_code, 400)  # not JSON at all
            self.assertEqual(self.call('lib', 'post', url).status_code, 200, url)  # no body = defaults
            self.assertEqual(self.call('lib', 'post', url, json={}).status_code, 200, url)
        # Without a body the budget export uses the default semester, exactly like GET /api/admin/budget.
        self.assertEqual(self.call('lib', 'post', '/api/admin/books/export', data='{"classNo": ', content_type='application/json').status_code, 400)

    def test_huge_integer_quantity_is_clamped_without_float_conversion(self):
        for given in (2**31, 10**30, 10**400):
            body = self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': given}).get_json()
            self.assertEqual(body['quantity'], 2147483647, given)
        self.assertEqual(self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': 1e308}).get_json()['quantity'], 2147483647)
        self.assertEqual(self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': -(10**400)}).get_json()['quantity'], 0)
        self.assertEqual(self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': 2147483647}).get_json()['quantity'], 2147483647)

    def test_dates_at_the_calendar_edge_are_validation_errors_not_500(self):
        for query in ('?to=9999-12-31', '?from=0001-01-01', '?from=9999-12-31&to=9999-12-31'):
            response = self.call('lib', 'get', '/api/admin/books' + query)
            self.assertEqual((response.status_code, response.get_json()['error']['message']), (400, '검색 조건을 확인해 주세요.'), query)
        # An ordinary date keeps "next midnight" as the exclusive end.
        self.assertEqual(self.call('lib', 'get', '/api/admin/books?to=9999-12-30').status_code, 200)
        self.assertEqual(self.titles('?from=2026-11-18&to=2026-11-18'), ['소년이 온다'])


class AdminExportTests(AdminTestBase):
    def test_books_export_columns_formats_and_filename(self):
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': 'approved'})
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [MODEUN], 'status': 'rejected', 'reason': '이미 소장 중'})
        # 2026-11-18 15:30 UTC is already 2026-11-19 in KST: the file name uses the KST date.
        with fixed_clock(datetime(2026, 11, 18, 15, 30)):
            response = self.call('lib', 'post', '/api/admin/books/export', json={'sort': 'title'})
            budget = self.call('lib', 'post', '/api/admin/budget/export', json={'period': 'all'})
        self.assertEqual(response.status_code, 200)
        disposition = response.headers['Content-Disposition']
        self.assertIn(f"filename*=UTF-8''{quote('신청도서목록')}_20261119.xlsx", disposition)
        self.assertIn(f"filename*=UTF-8''{quote('예상예산')}_20261119.xlsx", budget.headers['Content-Disposition'])
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


class WorkbookPackageTests(AdminTestBase):
    def package(self, url, **kwargs):
        response = self.call('lib', 'post', url, **kwargs)
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(response.data)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def test_both_exports_have_valid_workbook_parts(self):
        main = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
        rel = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
        for url in ('/api/admin/books/export', '/api/admin/budget/export'):
            parts = self.package(url, json={})
            workbook = ET.fromstring(parts['xl/workbook.xml'])
            children = [child.tag for child in workbook]
            self.assertEqual(children, [main + 'bookViews', main + 'sheets'], url)  # bookViews comes before sheets
            self.assertIsNotNone(workbook.find(f'{main}bookViews/{main}workbookView'))
            sheet_ref = workbook.find(f'{main}sheets/{main}sheet').get(rel)
            relationships = ET.fromstring(parts['xl/_rels/workbook.xml.rels'])
            targets = {r.get('Id'): r.get('Target') for r in relationships}
            self.assertEqual(targets[sheet_ref], 'worksheets/sheet1.xml')
            self.assertIn('xl/worksheets/sheet1.xml', parts)
            self.assertEqual(targets['rId2'], 'styles.xml')
            self.assertIn('xl/styles.xml', parts)
            view = ET.fromstring(parts['xl/worksheets/sheet1.xml']).find(f'{main}sheetViews/{main}sheetView')
            self.assertEqual(view.get('workbookViewId'), '0')  # refers to the workbookView that now exists
            content_types = parts['[Content_Types].xml'].decode()
            for part in ('/xl/workbook.xml', '/xl/worksheets/sheet1.xml', '/xl/styles.xml'):
                self.assertIn(part, content_types)

    def test_literal_escape_lookalikes_are_preserved(self):
        db.session.query(RequestedBook).filter_by(isbn=SOSEON).one().title = 'a_x0041_b _x005F_x0041_ _x12_ plain_x'
        db.session.commit()
        parts = self.package('/api/admin/books/export', json={'sort': 'title'})
        root = ET.fromstring(parts['xl/worksheets/sheet1.xml'])
        stored = [''.join(c.itertext()) for c in root.iter(f"{{{NS['m']}}}c") if c.get('t') == 'inlineStr']
        self.assertIn('a_x005F_x0041_b _x005F_x005F_x005F_x0041_ _x12_ plain_x', stored)  # first underscore of each lookalike escaped
        # Readers decode _xHHHH_ back, which yields the original text.
        import re
        decoded = re.sub(r'_x([0-9A-Fa-f]{4})_', lambda m: chr(int(m.group(1), 16)), 'a_x005F_x0041_b _x005F_x005F_x005F_x0041_ _x12_ plain_x')
        self.assertEqual(decoded, 'a_x0041_b _x005F_x0041_ _x12_ plain_x')


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
        # Wednesday 2026-11-18 09:00 KST; this week started Monday 2026-11-16 00:00 KST (= 2026-11-15 15:00 UTC).
        with fixed_clock(datetime(2026, 11, 18, 0, 0)):
            self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': 'approved'})
            self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [CONVENIENCE], 'status': 'rejected', 'reason': 'x'})
            self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': 'purchased'})
            self.call('lib', 'post', '/api/admin/books/revert', json={'bookIds': [CONVENIENCE]})  # reverts leave no history
            # Old history relative to the same clock: Sunday 23:59:59.8 KST is last week, Monday 00:00:00 KST is this week.
            book = db.session.query(RequestedBook).filter_by(isbn=MODEUN).one()
            for when in (datetime(2026, 11, 15, 14, 59, 59, 800000), datetime(2026, 11, 15, 15, 0, 0)):
                db.session.add(ReviewLog(requested_book_id=book.id, status='approved', actor_id=self.users['lib'], created_at=when))
            db.session.commit()
            body = self.call('lib', 'get', '/api/admin/summary').get_json()
        self.assertEqual((body['pending'], body['weekApproved'], body['weekRejected'], body['purchasedTotal']), (3, 2, 1, 1))  # 1 new approval + Monday 00:00 log
        recent = [l['status'] for l in body['recentLogs']]
        self.assertEqual(recent[:3], ['purchased', 'rejected', 'approved'])
        self.assertEqual([(l['bookTitle'], l['status']) for l in body['recentLogs'][:3]], [('소년이 온다', 'purchased'), ('불편한 편의점', 'rejected'), ('소년이 온다', 'approved')])
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
        with fixed_clock(datetime(2026, 11, 18, 0, 0)):
            body = self.call('lib', 'get', '/api/admin/summary').get_json()
        self.assertEqual(len(body['recentLogs']), 8)
        self.assertEqual(body['weekApproved'], 0)  # all of those are before this (fixed) week


if __name__ == '__main__':
    unittest.main()
