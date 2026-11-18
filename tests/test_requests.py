import unittest
from datetime import datetime
from app.auth.models import AuthSession
from app.books.models import BookRequest, Notification, RequestedBook, ReviewLog
from app.db import db
from tests.helpers import AppCase, CONVENIENCE, MODEUN, PAINT, SOSEON


class RequestTests(AppCase):
    def error(self, response, status, code):
        self.assertEqual((response.status_code, response.get_json()['error']['code']), (status, code), response.get_json())
        return response.get_json()['error']['message']

    def test_create_duplicate_and_my_list_shape(self):
        response = self.request_book('s1')
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body['request']['bookId'], SOSEON)
        self.assertEqual(body['review']['status'], 'pending')
        self.assertEqual((body['review']['quantity'], body['request']['userId']), (1, str(self.users['s1'])))
        self.assertIsInstance(body['request']['createdAt'], int)
        self.assertEqual(self.error(self.request_book('s1'), 409, 'DUPLICATE_REQUEST'), '이미 신청한 도서예요.')
        mine = self.call('s1', 'get', '/api/me/requests').get_json()['items']
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]['status'], mine[0]['book']['id'], mine[0]['book']['price']), ('pending', SOSEON, 15000))
        self.assertEqual(self.call('s2', 'get', '/api/me/requests').get_json(), {'items': []})
        self.assertEqual(self.request_book('s2').status_code, 201)
        self.assertEqual(db.session.query(RequestedBook).count(), 1)

    def test_legacy_isbn_key_and_bad_bodies(self):
        self.assertEqual(self.call('s1', 'post', '/api/requests', json={'isbn': SOSEON}).status_code, 201)
        for body in ({}, {'bookId': 'x'}, {'bookId': SOSEON, 'isbn': SOSEON}, {'bookId': 5}, {'other': SOSEON}):
            self.assertEqual(self.call('s2', 'post', '/api/requests', json=body).status_code, 400, body)
        self.assertEqual(self.call('s2', 'post', '/api/requests', json={'bookId': '9780306406157'}).status_code, 404)

    def test_roles_csrf_and_messages(self):
        self.assertEqual(self.error(self.request_book('lib'), 403, 'FORBIDDEN'), '학생 계정으로 로그인해야 신청할 수 있어요.')
        self.assertEqual(self.app.test_client().post('/api/requests', json={'bookId': SOSEON}).status_code, 401)
        client, _ = self.as_user('s1')
        self.assertEqual(self.error(client.post('/api/requests', json={'bookId': SOSEON}), 403, 'CSRF_FAILED'), '요청을 확인할 수 없어요. 새로고침 후 다시 시도해 주세요.')
        self.assertEqual(self.error(self.call('lib', 'get', '/api/me/requests'), 403, 'FORBIDDEN'), '학생 계정으로 로그인해야 신청할 수 있어요.')

    def test_owned_by_catalogue_and_unknown_catalogue_fail_closed(self):
        self.assertEqual(self.error(self.request_book('s1', PAINT), 409, 'OWNED_BOOK'), '이미 교내에 소장 중인 도서예요.')
        self.assertEqual(db.session.query(RequestedBook).count(), 0)
        class Unknown:
            def ownership(self, isbn): return 'UNKNOWN'
        self.app.config['LIBRARY_CATALOG'] = Unknown()
        self.assertEqual(self.error(self.request_book('s1'), 503, 'LIBRARY_CATALOG_UNAVAILABLE'), '소장 여부를 확인할 수 없어 지금은 신청할 수 없어요.')
        self.app.config['LIBRARY_CATALOG'] = None
        self.assertEqual(self.request_book('s1').status_code, 503)
        self.assertEqual(db.session.query(RequestedBook).count(), 0)
        self.app.config['BOOK_PROVIDER'] = None  # a missing provider is also 503, never a made-up success
        self.app.config['LIBRARY_CATALOG'] = type('C', (), {'ownership': lambda s, i: 'NOT_OWNED'})()
        self.assertEqual(self.error(self.request_book('s1'), 503, 'BOOK_PROVIDER_UNAVAILABLE'), '도서 정보를 불러올 수 없어요. 잠시 후 다시 시도해 주세요.')

    def test_book_level_approval_notifies_every_requester_and_logs_once(self):
        self.request_book('s1')
        self.request_book('s2')
        self.assertEqual(self.set_status(SOSEON, 'approved').get_json()['changed'], 1)
        for key in ('s1', 's2'):
            self.assertEqual(self.call(key, 'get', '/api/me/requests').get_json()['items'][0]['status'], 'approved')
        # One notification per requester (checked by user, not just by a total).
        for key in ('s1', 's2'):
            mine = db.session.query(Notification).filter_by(user_id=self.users[key]).all()
            self.assertEqual([(n.type, n.requested_book_id is not None) for n in mine], [('approved', True)], key)
        self.assertEqual(db.session.query(ReviewLog).count(), 1)
        # A new requester joins an already approved book and sees its current state (FE keeps the review).
        self.assertEqual(self.request_book('s3').get_json()['review']['status'], 'approved')

    def test_rejected_book_blocks_everyone(self):
        self.request_book('s1')
        self.set_status(SOSEON, 'rejected')
        self.assertEqual(self.error(self.request_book('s2'), 409, 'REJECTED_BOOK'), '반려된 도서는 다시 신청할 수 없어요.')
        self.assertEqual(self.error(self.request_book('s1'), 409, 'DUPLICATE_REQUEST'), '이미 신청한 도서예요.')  # owned -> duplicate -> rejected
        mine = self.call('s1', 'get', '/api/me/requests').get_json()['items'][0]
        self.assertEqual((mine['status'], mine['reason']), ('rejected', '사유'))

    def test_purchased_counts_as_owned(self):
        self.request_book('s1')
        self.set_status(SOSEON, 'approved')
        self.set_status(SOSEON, 'purchased')
        self.assertEqual(self.error(self.request_book('s2'), 409, 'OWNED_BOOK'), '이미 교내에 소장 중인 도서예요.')
        self.assertTrue(self.call('s1', 'get', '/api/me/requests').get_json()['items'][0]['book']['owned'])
        before = (db.session.query(Notification).count(), db.session.query(ReviewLog).count())
        self.call('lib', 'post', '/api/admin/books/revert', json={'bookIds': [SOSEON]})
        self.assertEqual((db.session.query(Notification).count(), db.session.query(ReviewLog).count()), before)  # reverts are silent
        self.assertFalse(self.call('s1', 'get', '/api/me/requests').get_json()['items'][0]['book']['owned'])
        self.assertEqual(self.request_book('s2').status_code, 201)

    def test_existing_book_price_is_not_overwritten(self):
        self.request_book('s1', MODEUN)
        self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/price', json={'price': 12000})
        self.app.config['BOOK_PROVIDER'].rows[8]['price'] = 99999  # provider changes later
        second = self.request_book('s2', MODEUN)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(db.session.query(BookRequest).filter_by(user_id=self.users['s2']).count(), 1)  # the request really was saved
        self.assertEqual(db.session.query(RequestedBook).one().price, 12000)

    def test_cancel_rules(self):
        first = self.request_book('s1').get_json()['request']['id']
        second = self.request_book('s2').get_json()['request']['id']
        self.assertEqual(self.error(self.call('s1', 'delete', f'/api/me/requests/{second}'), 404, 'NOT_FOUND'), '신청 내역을 찾을 수 없어요.')
        self.assertEqual(self.call('lib', 'delete', f'/api/me/requests/{first}').status_code, 403)
        self.set_status(SOSEON, 'approved')
        self.assertEqual(self.error(self.call('s1', 'delete', f'/api/me/requests/{first}'), 409, 'STATE_CONFLICT'), '대기 중인 신청만 취소할 수 있어요.')
        self.call('lib', 'post', '/api/admin/books/revert', json={'bookIds': [SOSEON]})
        # Back to pending although notifications already reference the requests: deleting must not fail.
        self.assertEqual(self.call('s1', 'delete', f'/api/me/requests/{first}').status_code, 204)
        self.assertEqual(db.session.query(Notification).filter(Notification.request_id.is_(None)).count(), 1)
        self.assertEqual(db.session.query(Notification).count(), 2)
        self.assertEqual(self.call('s1', 'get', '/api/me/requests').get_json(), {'items': []})
        listed = self.call('lib', 'get', '/api/admin/books').get_json()
        self.assertEqual((listed['total'], listed['items'][0]['book']['id'], len(listed['items'][0]['requests'])), (1, SOSEON, 1))

    def test_last_requester_cancel_removes_book_from_lists_but_keeps_price(self):
        request_id = self.request_book('s1', MODEUN).get_json()['request']['id']
        self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/price', json={'price': 12000})
        self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/quantity', json={'quantity': 5})
        self.assertEqual(self.call('s1', 'delete', f'/api/me/requests/{request_id}').status_code, 204)
        self.assertEqual(self.call('lib', 'get', '/api/admin/books').get_json()['total'], 0)
        budget = self.call('lib', 'get', '/api/admin/budget?period=all').get_json()
        self.assertEqual(budget['expected'], [])
        row = db.session.query(RequestedBook).one()
        self.assertEqual((row.price, row.status, row.quantity), (12000, 'pending', 1))
        self.assertEqual(self.call('lib', 'patch', f'/api/admin/books/{MODEUN}/price', json={'price': 1}).status_code, 404)
        self.assertFalse(self.call('s1', 'get', f'/api/books/{MODEUN}').get_json()['review'])

    def test_notifications_shape_unread_and_read_all(self):
        self.request_book('s1')
        self.request_book('s1', CONVENIENCE)
        self.request_book('s2')
        self.set_status(SOSEON, 'rejected')
        self.set_status(CONVENIENCE, 'approved')
        data = self.call('s1', 'get', '/api/me/notifications').get_json()
        self.assertEqual((len(data['items']), data['unreadCount']), (2, 2))
        rejected = next(n for n in data['items'] if n['status'] == 'rejected')
        self.assertEqual((rejected['bookId'], rejected['bookTitle'], rejected['reason'], rejected['read']), (SOSEON, '소년이 온다', '사유', False))
        self.assertEqual(set(rejected) - {'message'}, {'id', 'userId', 'bookId', 'bookTitle', 'status', 'reason', 'createdAt', 'read'})
        approved = next(n for n in data['items'] if n['status'] == 'approved')
        self.assertNotIn('reason', approved)
        self.assertIsInstance(approved['createdAt'], int)
        # Own notifications only.
        self.assertEqual(len(self.call('s2', 'get', '/api/me/notifications').get_json()['items']), 1)
        self.assertEqual(self.call('s1', 'patch', f"/api/me/notifications/{approved['id']}/read").get_json(), {'id': approved['id'], 'read': True})
        self.assertEqual(self.call('s1', 'patch', f"/api/me/notifications/{approved['id']}/read").status_code, 200)  # idempotent
        self.assertEqual(self.call('s1', 'patch', '/api/me/notifications/9999/read').status_code, 404)
        only = self.call('s1', 'get', '/api/me/notifications?unreadOnly=true').get_json()
        self.assertEqual(([n['id'] for n in only['items']], only['unreadCount']), ([rejected['id']], 1))
        self.assertEqual(self.call('s1', 'get', '/api/me/notifications?unreadOnly=maybe').status_code, 400)
        self.assertEqual(self.call('s1', 'patch', '/api/me/notifications/read-all').get_json(), {'updated': 1})
        self.assertEqual(self.call('s1', 'patch', '/api/me/notifications/read-all').get_json(), {'updated': 0})
        self.assertEqual(self.call('s1', 'get', '/api/me/notifications').get_json()['unreadCount'], 0)
        self.assertEqual(self.call('s2', 'get', '/api/me/notifications').get_json()['unreadCount'], 1)  # others untouched
        client, _ = self.as_user('s1')
        self.assertEqual(client.patch('/api/me/notifications/read-all').status_code, 403)  # CSRF required

    def test_other_users_cannot_read_foreign_notification(self):
        self.request_book('s1')
        self.set_status(SOSEON, 'approved')
        nid = self.call('s1', 'get', '/api/me/notifications').get_json()['items'][0]['id']
        self.assertEqual(self.call('s2', 'patch', f'/api/me/notifications/{nid}/read').status_code, 404)

    def test_integrity_error_on_first_try_is_retried_for_real(self):
        from unittest.mock import patch
        from sqlalchemy.exc import IntegrityError
        from app.requests import routes
        real = routes.create_once
        calls = []
        def flaky(isbn, uid):
            calls.append(1)
            if len(calls) == 1:
                raise IntegrityError('x', {}, Exception())  # only the first call fails
            return real(isbn, uid)
        self.request_book('s1')
        with patch.object(routes, 'create_once', side_effect=flaky):
            response = self.request_book('s2')
        self.assertEqual((response.status_code, len(calls)), (201, 2))
        self.assertEqual(db.session.query(BookRequest).filter_by(user_id=self.users['s2']).count(), 1)

    def test_partial_work_of_a_failed_attempt_is_rolled_back_before_the_retry(self):
        from unittest.mock import patch
        from sqlalchemy.exc import IntegrityError
        from app.requests import routes
        real = routes.create_once
        for make_error in (lambda: IntegrityError('x', {}, Exception()), self.deadlock):
            db.session.query(BookRequest).delete()
            db.session.query(RequestedBook).delete()
            db.session.commit()
            calls = []
            def fails_after_flush(isbn, uid):
                calls.append(1)
                if len(calls) == 1:
                    # Change the DB and flush, then fail: only a rollback can remove this row before the retry.
                    db.session.add(RequestedBook(isbn=isbn, title='partial attempt', author='a', publisher='p', price=1))
                    db.session.flush()
                    raise make_error()
                return real(isbn, uid)
            with patch.object(routes, 'create_once', side_effect=fails_after_flush):
                response = self.request_book('s1')
            self.assertEqual((response.status_code, len(calls)), (201, 2))
            db.session.expire_all()
            book = db.session.query(RequestedBook).one()
            self.assertEqual(book.title, '소년이 온다')  # the half-written first attempt is gone
            self.assertEqual(db.session.query(BookRequest).count(), 1)

    def test_same_student_duplicate_after_integrity_error_is_409(self):
        from unittest.mock import patch
        from sqlalchemy.exc import IntegrityError
        from app.requests import routes
        real = routes.create_once
        calls = []
        def flaky(isbn, uid):
            calls.append(1)
            if len(calls) == 1:
                raise IntegrityError('x', {}, Exception())
            return real(isbn, uid)
        self.request_book('s1')
        with patch.object(routes, 'create_once', side_effect=flaky):
            self.assertEqual(self.error(self.request_book('s1'), 409, 'DUPLICATE_REQUEST'), '이미 신청한 도서예요.')
        self.assertEqual(db.session.query(BookRequest).filter_by(user_id=self.users['s1']).count(), 1)

    def test_persistent_integrity_error_ends_as_conflict_not_500(self):
        from unittest.mock import patch
        from sqlalchemy.exc import IntegrityError
        from app.requests import routes
        with patch.object(routes, 'create_once', side_effect=IntegrityError('x', {}, Exception())) as mocked:
            self.assertEqual(self.error(self.request_book('s2'), 409, 'DUPLICATE_REQUEST'), '이미 신청한 도서예요.')
        self.assertEqual(mocked.call_count, routes.MAX_ATTEMPTS)

    def deadlock(self, code=1213):
        from sqlalchemy.exc import OperationalError
        return OperationalError('stmt', {}, Exception(code, 'Deadlock found when trying to get lock'))

    def test_deadlock_is_rolled_back_and_retried(self):
        from unittest.mock import patch
        from app.requests import routes
        real = routes.create_once
        calls = []
        def deadlocked_once(isbn, uid):
            calls.append(1)
            if len(calls) == 1:
                raise self.deadlock()
            return real(isbn, uid)
        with patch.object(routes, 'create_once', side_effect=deadlocked_once):
            response = self.request_book('s1')
        self.assertEqual((response.status_code, len(calls)), (201, 2))
        self.assertEqual(db.session.query(BookRequest).count(), 1)

    def test_deadlock_gives_up_after_the_attempt_limit_and_other_db_errors_are_not_retried(self):
        from unittest.mock import patch
        from app.requests import routes
        self.app.config['PROPAGATE_EXCEPTIONS'] = False  # answer 500 like production instead of re-raising into the test client
        with patch.object(routes, 'create_once', side_effect=self.deadlock()) as mocked:
            self.assertEqual(self.request_book('s1').status_code, 500)
        self.assertEqual(mocked.call_count, routes.MAX_ATTEMPTS)
        with patch.object(routes, 'create_once', side_effect=self.deadlock(2003)) as mocked:
            self.assertEqual(self.request_book('s1').status_code, 500)
        self.assertEqual(mocked.call_count, 1)  # not a deadlock: no retry
        self.assertEqual(db.session.query(BookRequest).count(), 0)

    def test_cancel_counts_remaining_requests_with_a_locking_read(self):
        from sqlalchemy import event
        first = self.request_book('s1').get_json()['request']['id']
        self.request_book('s2')
        self.call('lib', 'patch', f'/api/admin/books/{SOSEON}/quantity', json={'quantity': 5})  # set BEFORE the first cancel
        seen = []
        def record(_conn, clause, _multi, _params, _options):
            if getattr(clause, '_for_update_arg', None) is not None:
                seen.append(clause.get_final_froms()[0].name)
        event.listen(db.engine, 'before_execute', record)
        try:
            self.assertEqual(self.call('s1', 'delete', f'/api/me/requests/{first}').status_code, 204)
        finally:
            event.remove(db.engine, 'before_execute', record)
        self.assertEqual(seen.count('book_requests'), 2)  # re-check of the request itself + the "remaining" read
        # Another requester remains, so the quantity must survive the first cancel (an unconditional reset to 1 would fail here).
        db.session.expire_all()
        self.assertEqual(db.session.query(RequestedBook).one().quantity, 5)
        last = db.session.query(BookRequest).one().id
        self.assertEqual(self.call('s2', 'delete', f'/api/me/requests/{last}').status_code, 204)
        self.assertEqual(db.session.query(RequestedBook).one().quantity, 1)  # last requester left: reset

    def test_unread_only_old_key_is_accepted_during_the_transition(self):
        self.request_book('s1')
        self.set_status(SOSEON, 'approved')
        self.assertEqual(len(self.call('s1', 'get', '/api/me/notifications?unread_only=true').get_json()['items']), 1)
        self.call('s1', 'patch', '/api/me/notifications/read-all')
        self.assertEqual(self.call('s1', 'get', '/api/me/notifications?unread_only=true').get_json()['items'], [])
        # The contract key wins when both are sent.
        self.assertEqual(len(self.call('s1', 'get', '/api/me/notifications?unreadOnly=false&unread_only=true').get_json()['items']), 1)


if __name__ == '__main__':
    unittest.main()
