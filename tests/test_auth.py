import unittest
from app.auth.models import AuthSession, User
from app.auth.routes import COOKIE, create_librarian
from app.db import db
from tests.helpers import AppCase, ORIGIN


class AuthTests(AppCase):
    def setUp(self):
        super().setUp()
        self.client = self.app.test_client()

    def csrf(self):
        response = self.client.get('/api/auth/csrf')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return {'X-CSRF-Token': response.get_json()['csrfToken']}

    def signup(self, headers, **overrides):
        body = {'loginId': 'Newbie01', 'password': 'password1', 'password2': 'password1', 'name': '새학생', 'classNo': '2-3', 'studentNo': '10410'}
        body.update(overrides)
        body = {k: v for k, v in body.items() if v is not None}
        return self.client.post('/api/auth/signup', json=body, headers=headers)

    def test_signup_login_profile_logout_flow(self):
        headers = self.csrf()
        response = self.signup(headers)
        self.assertEqual(response.status_code, 201)
        created = response.get_json()
        self.assertEqual((created['loginId'], created['role'], created['classNo'], created['studentNo']), ('newbie01', 'student', '2-3', '10410'))
        self.assertIsInstance(created['joinedAt'], int)
        self.assertNotIn('password', str(created).lower())
        self.assertEqual(self.client.get('/api/me').status_code, 401)  # signup does not log in
        self.assertNotIn('password', db.session.query(User).filter_by(login_id='newbie01').one().password_hash)
        bad = self.client.post('/api/auth/login', json={'loginId': 'newbie01', 'password': 'wrongwrong'}, headers=headers)
        self.assertEqual((bad.status_code, bad.get_json()['error']['code']), (401, 'INVALID_CREDENTIALS'))
        self.assertEqual(bad.get_json()['error']['message'], '아이디 또는 비밀번호가 맞지 않아요.')
        login = self.client.post('/api/auth/login', json={'loginId': '  NEWBIE01 ', 'password': 'password1'}, headers=headers)
        self.assertEqual(login.status_code, 200)
        data = login.get_json()
        self.assertEqual(data['user']['name'], '새학생')
        # The csrfToken returned by login works for a write straight away.
        patch = self.client.patch('/api/me', json={'name': ' 새 이름 ', 'classNo': '3-12', 'studentNo': '00410'}, headers={'X-CSRF-Token': data['csrfToken']})
        self.assertEqual((patch.status_code, patch.get_json()['name'], patch.get_json()['studentNo']), (200, '새 이름', '00410'))
        self.assertEqual(self.client.post('/api/auth/logout', headers={'X-CSRF-Token': data['csrfToken']}).status_code, 204)
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        self.assertEqual(self.client.post('/api/auth/logout').status_code, 204)  # already signed out: still 204

    def test_signup_rejects_role_and_unknown_keys(self):
        headers = self.csrf()
        self.assertEqual(self.signup(headers, role='librarian').status_code, 400)
        self.assertEqual(db.session.query(User).filter_by(login_id='newbie01').count(), 0)
        self.assertEqual(self.client.post('/api/auth/signup', json={}, headers={}).status_code, 403)  # CSRF

    def test_signup_validation_collects_every_field(self):
        headers = self.csrf()
        response = self.signup(headers, loginId='ab', password='short', password2='other', name='  ', classNo='4-1', studentNo='1041')
        body = response.get_json()['error']
        self.assertEqual((response.status_code, body['code'], body['message']), (400, 'VALIDATION_ERROR', '가입 정보를 확인해 주세요.'))
        self.assertEqual(body['fields'], {'name': '이름을 입력해 주세요.', 'loginId': '영문·숫자 4~20자로 입력해 주세요.', 'password': '8~128자로 입력해 주세요.',
                                         'password2': '비밀번호가 서로 달라요.', 'classNo': '2-3처럼 학년-반으로 입력해 주세요.', 'studentNo': '숫자 5자리 학번을 입력해 주세요.'})

    def test_signup_field_rules(self):
        headers = self.csrf()
        for value in ('2반', '4-1', '2-123', '0-1'):
            self.assertIn('classNo', self.signup(headers, classNo=value).get_json()['error']['fields'], value)
        for value in ('1041', '104100', ' 10410', '1041a', '１２３４５', '10410\n'):
            self.assertIn('studentNo', self.signup(headers, studentNo=value).get_json()['error']['fields'], repr(value))
        for value in ('abc', 'a' * 21, 'with_underscore', '한글아이디'):
            self.assertIn('loginId', self.signup(headers, loginId=value).get_json()['error']['fields'], value)
        self.assertEqual(self.signup(headers, classNo=' 1-4 ', studentNo='00410', loginId=' abcd ', password2=None).status_code, 201)
        self.assertEqual(db.session.query(User).filter_by(login_id='abcd').one().grade_class, '1-4')

    def test_duplicate_login_id(self):
        headers = self.csrf()
        self.assertEqual(self.signup(headers).status_code, 201)
        response = self.signup(headers, loginId='NEWBIE01')
        error = response.get_json()['error']
        self.assertEqual((response.status_code, error['code'], error['message']), (409, 'DUPLICATE_LOGIN_ID', '이미 사용 중인 아이디예요.'))
        self.assertEqual(error['fields']['loginId'], '이미 사용 중인 아이디예요.')

    def test_profile_validation_and_librarian_name_only(self):
        client, headers = self.as_user('s1')
        message = '이름, 학년/반(예: 2-3), 학번(숫자 5자리)을 확인해 주세요.'
        for body in ({'name': '이름'}, {'name': '이름', 'classNo': '2-3', 'studentNo': '1234'}, {'name': '', 'classNo': '2-3', 'studentNo': '12345'},
                     {'name': '이름', 'classNo': '2-3', 'studentNo': '12345', 'role': 'librarian'}):
            response = client.patch('/api/me', json=body, headers=headers)
            self.assertEqual((response.status_code, response.get_json()['error']['message']), (400, message), body)
        self.assertEqual(client.patch('/api/me', json={'name': '이름', 'classNo': '2-3', 'studentNo': '12345'}, headers=headers).status_code, 200)
        lib, lib_headers = self.as_user('lib')
        self.assertNotIn('classNo', lib.get('/api/me').get_json())
        self.assertEqual(lib.patch('/api/me', json={'name': '새 사서'}, headers=lib_headers).get_json()['role'], 'librarian')
        self.assertEqual(lib.patch('/api/me', json={'name': 'x', 'classNo': '1-1'}, headers=lib_headers).status_code, 400)

    def test_password_change_and_other_sessions_removed(self):
        user = db.session.get(User, self.users['s1'])
        from werkzeug.security import generate_password_hash
        user.password_hash = generate_password_hash('oldpassword')
        db.session.commit()
        client, headers = self.as_user('s1')
        other = self.app.test_client()
        other_headers = self.csrf_for(other)
        other.post('/api/auth/login', json={'loginId': 'student1', 'password': 'oldpassword'}, headers=other_headers)
        self.assertEqual(db.session.query(AuthSession).filter_by(user_id=self.users['s1']).count(), 2)
        def change(current, new, **extra):
            return client.patch('/api/me/password', json={'currentPassword': current, 'newPassword': new, **extra}, headers=headers)
        self.assertEqual(change('nope', 'newpassword1').get_json()['error']['code'], 'INVALID_PASSWORD')
        short = change('oldpassword', 'short')
        self.assertEqual((short.status_code, short.get_json()['error']['message']), (400, '새 비밀번호는 8~128자여야 해요.'))
        self.assertEqual(short.get_json()['error']['fields'], {'newPassword': '새 비밀번호는 8~128자여야 해요.'})
        self.assertEqual(change('oldpassword', 'newpassword1', newPasswordConfirm='different').status_code, 400)
        self.assertEqual(change('oldpassword', 'newpassword1', newPasswordConfirm='newpassword1').status_code, 204)
        self.assertEqual(client.get('/api/me').status_code, 200)  # current session survives
        self.assertEqual(other.get('/api/me').status_code, 401)  # the other one is gone
        fresh = self.app.test_client()
        fresh_headers = self.csrf_for(fresh)
        self.assertEqual(fresh.post('/api/auth/login', json={'loginId': 'student1', 'password': 'oldpassword'}, headers=fresh_headers).status_code, 401)
        self.assertEqual(fresh.post('/api/auth/login', json={'loginId': 'student1', 'password': 'newpassword1'}, headers=fresh_headers).status_code, 200)
        self.assertEqual(self.client.patch('/api/me/password', json={}).status_code, 401)

    def test_concurrent_password_change_second_request_is_rejected(self):
        # Both requests have ALREADY resolved their session (resolve_session passed) when the first change commits and
        # deletes the other sessions. The second must then be stopped by the re-check under the account lock.
        from types import SimpleNamespace
        from flask import g, request
        from sqlalchemy import delete
        from werkzeug.security import check_password_hash, generate_password_hash
        from app.auth.routes import digest
        racing = []  # token hashes of the sessions whose request "loses the race"

        def other_request_commits_after_resolve():
            # Runs right after resolve_session: the session is deleted by "the other request" now, while this
            # request keeps the stale, already-resolved view of it.
            if request.path == '/api/me/password' and g.auth_session is not None and g.auth_session.token_hash in racing:
                token_hash = g.auth_session.token_hash
                db.session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash))
                db.session.commit()
                g.auth_session = SimpleNamespace(token_hash=token_hash)
        self.app.before_request(other_request_commits_after_resolve)
        user = db.session.get(User, self.users['s1'])
        user.password_hash = generate_password_hash('oldpassword')
        db.session.commit()
        winner, winner_headers = self.as_user('s1')
        loser = self.app.test_client()
        loser_csrf = self.csrf_for(loser)
        loser_headers = {'X-CSRF-Token': loser.post('/api/auth/login', json={'loginId': 'student1', 'password': 'oldpassword'}, headers=loser_csrf).get_json()['csrfToken']}
        first = winner.patch('/api/me/password', json={'currentPassword': 'oldpassword', 'newPassword': 'newpassword1'}, headers=winner_headers)
        self.assertEqual(first.status_code, 204)
        # The first change removed the loser's session; give it a fresh one that resolves normally and then loses the race.
        relogin = loser.post('/api/auth/login', json={'loginId': 'student1', 'password': 'newpassword1'}, headers=self.csrf_for(loser))
        loser_headers = {'X-CSRF-Token': relogin.get_json()['csrfToken']}
        racing.append(digest(loser.get_cookie(COOKIE).value))
        second = loser.patch('/api/me/password', json={'currentPassword': 'newpassword1', 'newPassword': 'otherpassword2'}, headers=loser_headers)
        self.assertEqual(second.status_code, 401)  # resolved fine, then rejected by the re-check under the lock
        db.session.expire_all()
        self.assertTrue(check_password_hash(db.session.get(User, self.users['s1']).password_hash, 'newpassword1'))
        self.assertEqual(winner.get('/api/me').status_code, 200)  # the session of the request that finished first remains

    def test_password_change_locks_account_row_before_session_row(self):
        # SQLite ignores FOR UPDATE, so check the statements that were built: login and password change both
        # lock the users row, and the change locks the caller's session row after it.
        from sqlalchemy import event
        from werkzeug.security import generate_password_hash
        db.session.get(User, self.users['s1']).password_hash = generate_password_hash('oldpassword')
        db.session.commit()
        client, headers = self.as_user('s1')
        locked = []
        def record(_conn, clause, _multi, _params, _options):
            arg = getattr(clause, '_for_update_arg', None)
            if arg is not None:
                locked.append(clause.get_final_froms()[0].name)
        event.listen(db.engine, 'before_execute', record)
        try:
            self.assertEqual(client.patch('/api/me/password', json={'currentPassword': 'oldpassword', 'newPassword': 'newpassword1'}, headers=headers).status_code, 204)
            change = list(locked)
            del locked[:]
            fresh = self.app.test_client()
            fresh.post('/api/auth/login', json={'loginId': 'student1', 'password': 'newpassword1'}, headers=self.csrf_for(fresh))
        finally:
            event.remove(db.engine, 'before_execute', record)
        self.assertEqual(change, ['users', 'auth_sessions'])
        self.assertEqual(locked, ['users'])

    def csrf_for(self, client):
        return {'X-CSRF-Token': client.get('/api/auth/csrf').get_json()['csrfToken']}

    def test_cors_exact_origin_only(self):
        allowed = self.client.get('/api/health', headers={'Origin': ORIGIN})
        self.assertEqual(allowed.status_code, 200)  # the real /api/health route, not just a header check
        self.assertEqual(allowed.headers['Access-Control-Allow-Origin'], ORIGIN)
        self.assertEqual(allowed.headers['Access-Control-Allow-Credentials'], 'true')
        for origin in ('http://evil.example', 'http://localhost:5173.evil.example', 'http://localhost:5174'):
            self.assertNotIn('Access-Control-Allow-Origin', self.client.get('/api/health', headers={'Origin': origin}).headers)
        no_origin = self.client.get('/api/health')
        self.assertNotIn('Access-Control-Allow-Origin', no_origin.headers)
        # Vary: Origin is on every response so caches never mix origins.
        for response in (allowed, no_origin, self.client.get('/api/health', headers={'Origin': 'http://evil.example'})):
            self.assertIn('Origin', response.headers['Vary'])
        pre = self.client.options('/api/admin/books', headers={'Origin': ORIGIN, 'Access-Control-Request-Method': 'POST'})
        self.assertEqual(pre.status_code, 204)
        self.assertEqual(pre.headers['Access-Control-Allow-Headers'], 'Content-Type, X-CSRF-Token')
        self.assertEqual(pre.headers['Access-Control-Allow-Methods'], 'GET, POST, PATCH, DELETE, OPTIONS')
        bad = self.client.options('/api/admin/books', headers={'Origin': 'http://evil.example', 'Access-Control-Request-Method': 'POST'})
        self.assertNotIn('Access-Control-Allow-Origin', bad.headers)

    def test_cors_disabled_without_configuration(self):
        from flask import Flask
        from app.cors import init_cors
        app = Flask(__name__)
        app.config['CORS_ORIGINS_RAW'] = '*'
        init_cors(app)
        app.add_url_rule('/x', 'x', lambda: 'ok')
        self.assertNotIn('Access-Control-Allow-Origin', app.test_client().get('/x', headers={'Origin': ORIGIN}).headers)

    def test_operator_creates_librarian(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(create_librarian, ['Library01', '--name', '사서'], input='testpass123\ntestpass123\n')
        self.assertEqual(result.exit_code, 0, result.output)
        account = db.session.query(User).filter_by(login_id='library01').one()
        self.assertEqual(account.role, 'librarian')
        self.assertNotIn('testpass123', result.output)
        self.assertNotEqual(account.password_hash, 'testpass123')
        bad = runner.invoke(create_librarian, ['ab', '--name', '사서'], input='testpass123\ntestpass123\n')
        self.assertNotEqual(bad.exit_code, 0)


if __name__ == '__main__':
    unittest.main()
