import unittest
from flask import Flask
from app.db import db
from app.auth.models import User
from app.auth.routes import api, resolve_session


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
        db.init_app(self.app)
        self.app.before_request(resolve_session)
        self.app.register_blueprint(api)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def token(self):
        response = self.client.get('/api/auth/csrf')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.get_json()['csrf_token']

    def test_signup_login_csrf_rotation_profile_and_logout(self):
        body = {'login_id': 'Student01', 'password': 'testpassword123', 'name': '학생', 'grade_class': '2-3', 'student_no': '20301'}
        self.assertEqual(self.client.post('/api/auth/signup', json=body).status_code, 403)
        csrf = self.token()
        self.assertEqual(self.client.post('/api/auth/signup', json=body, headers={'X-CSRF-Token': csrf}).status_code, 201)
        self.assertNotIn('testpassword123', db.session.query(User).one().password_hash)
        self.assertEqual(self.client.post('/api/auth/signup', json=body, headers={'X-CSRF-Token': csrf}).status_code, 409)
        self.assertEqual(self.client.post('/api/auth/login', json={'login_id':'student01', 'password':'bad'}, headers={'X-CSRF-Token':csrf}).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={'login_id':'student01', 'password':'testpassword123'}, headers={'X-CSRF-Token':csrf}).status_code, 200)
        self.assertNotIn('password', self.client.get('/api/me').get_json())
        self.assertEqual(self.client.patch('/api/me', json={'name':'새 이름'}, headers={'X-CSRF-Token':csrf}).status_code, 403)
        csrf = self.token()
        self.assertEqual(self.client.patch('/api/me', json={'name':'새 이름'}, headers={'X-CSRF-Token':csrf}).get_json()['name'], '새 이름')
        self.assertEqual(self.client.patch('/api/me', json={'role':'librarian'}, headers={'X-CSRF-Token':csrf}).status_code, 400)
        self.assertEqual(self.client.post('/api/auth/logout', headers={'X-CSRF-Token':csrf}).status_code, 204)
        self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_signup_cannot_choose_role(self):
        csrf = self.token()
        body = {'login_id':'abcde','password':'1234567890','name':'학생','grade_class':'1-1','student_no':'1','role':'librarian'}
        self.assertEqual(self.client.post('/api/auth/signup', json=body, headers={'X-CSRF-Token':csrf}).status_code, 400)


if __name__ == '__main__': unittest.main()
