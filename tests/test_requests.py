import unittest
from flask import Flask, g
from app.db import db
from app.auth.models import User
from app.books.models import BookRequest, Notification, RequestedBook
from app.requests.routes import requests_api


class Provider:
    def get(self, isbn):
        return {'title': '책', 'author': '저자', 'publisher': '출판사', 'price': 10000}


class Catalog:
    def ownership(self, isbn):
        return 'NOT_OWNED'


class RequestsTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', BOOK_PROVIDER=Provider(), LIBRARY_CATALOG=Catalog())
        db.init_app(self.app)
        self.app.register_blueprint(requests_api)
        self.uid = 1
        self.role = 'student'
        self.csrf = True
        @self.app.before_request
        def auth():
            g.current_user = {'id': self.uid, 'role': self.role} if self.uid else None
            g.csrf_valid = self.csrf
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        db.session.add_all([User(id=i, login_id=f"user{i}", password_hash="test", name="test", role="student") for i in (1, 2)])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def create(self):
        return self.client.post('/api/requests', json={'isbn': '0306406152'})

    def test_request_duplicate_and_private_listing(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.create().get_json()['error']['code'], 'DUPLICATE_REQUEST')
        self.assertEqual(self.client.get('/api/me/requests').get_json()['total'], 1)
        self.uid = 2
        self.assertEqual(self.client.get('/api/me/requests').get_json()['total'], 0)
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(db.session.query(RequestedBook).count(), 1)

    def test_access_csrf_and_unavailable_catalog(self):
        self.csrf = False
        self.assertEqual(self.create().status_code, 403)
        self.csrf = True
        self.role = 'librarian'
        self.assertEqual(self.create().status_code, 403)
        self.role = 'student'
        self.app.config['LIBRARY_CATALOG'] = None
        self.assertEqual(self.create().status_code, 503)
        self.uid = None
        self.assertEqual(self.client.get('/api/me/notifications').status_code, 401)

    def test_notifications_are_private_and_read_idempotent(self):
        rid = int(self.create().get_json()['id'])
        n = Notification(user_id=1, request_id=rid, type='approved', message='승인')
        db.session.add(n); db.session.commit(); nid = n.id
        self.uid = 2
        self.assertEqual(self.client.patch(f'/api/me/notifications/{nid}/read').status_code, 404)
        self.uid = 1
        self.assertEqual(self.client.get('/api/me/notifications').get_json()['unread_count'], 1)
        self.assertTrue(self.client.patch(f'/api/me/notifications/{nid}/read').get_json()['is_read'])
        self.assertTrue(self.client.patch(f'/api/me/notifications/{nid}/read').get_json()['is_read'])
        self.assertEqual(self.client.get('/api/me/notifications?unread_only=true').get_json()['total'], 0)

    def test_owned_and_rejected_are_blocked(self):
        class Owned:
            def ownership(self, isbn): return 'OWNED'
        self.app.config['LIBRARY_CATALOG'] = Owned()
        self.assertEqual(self.create().get_json()['error']['code'], 'OWNED_BOOK')
        self.app.config['LIBRARY_CATALOG'] = Catalog()
        self.create()
        db.session.query(RequestedBook).one().status = 'rejected'
        db.session.commit()
        self.uid = 2
        self.assertEqual(self.create().get_json()['error']['code'], 'STATE_CONFLICT')


if __name__ == '__main__':
    unittest.main()
