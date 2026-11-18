"""Shared test app: real auth/session/CSRF, all blueprints, SQLite with foreign keys ON, fixture sources."""
import sqlite3
import unittest
from datetime import datetime
from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import Engine
from app.admin.routes import admin_api
from app.auth.models import User
from app.auth.routes import COOKIE, api as auth_api, new_session, resolve_session
from app.books.fixture import FixtureProvider, fixture_catalog
from app.books.models import BookRequest, RequestedBook
from app.books.routes import books_api
from app.cors import init_cors
from app.db import db
from app.requests.routes import requests_api

ORIGIN = 'http://localhost:5173'
SOSEON = '9788936434120'      # 소년이 온다, 15000, not owned
CONVENIENCE = '9791161571188'  # 불편한 편의점, 14000, not owned
PAINT = '9788936456894'       # 페인트, owned by the catalogue
MODEUN = '9788998441012'      # 모순, price null in the fixture


@event.listens_for(Engine, 'connect')
def enforce_sqlite_foreign_keys(dbapi, _record):
    if isinstance(dbapi, sqlite3.Connection):
        dbapi.cursor().execute('PRAGMA foreign_keys=ON')


class AppCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite://', CORS_ORIGINS_RAW=ORIGIN,
                               BOOK_PROVIDER=FixtureProvider(), LIBRARY_CATALOG=fixture_catalog())
        db.init_app(self.app)
        init_cors(self.app)
        self.app.before_request(resolve_session)
        for blueprint in (auth_api, books_api, requests_api, admin_api):
            self.app.register_blueprint(blueprint)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.users = {}
        for key, login, role, klass, no in (('s1', 'student1', 'student', '2-3', '20315'), ('s2', 'student2', 'student', '1-4', '10412'),
                                            ('s3', 'student3', 'student', '2-3', '20322'), ('lib', 'librarian1', 'librarian', None, None)):
            user = User(login_id=login, password_hash='unused', name={'s1': '김하늘', 's2': '이도윤', 's3': '강민재', 'lib': '박서연'}[key],
                        role=role, grade_class=klass, student_no=no)
            db.session.add(user)
            db.session.flush()
            self.users[key] = user.id
        db.session.commit()
        self.clients = {}

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def as_user(self, key):
        """Logged-in client with its own cookie; returns (client, csrf headers)."""
        if key not in self.clients:
            client = self.app.test_client()
            token, csrf = new_session(self.users[key])
            db.session.commit()
            client.set_cookie(COOKIE, token)
            self.clients[key] = (client, {'X-CSRF-Token': csrf})
        return self.clients[key]

    def call(self, key, method, url, **kwargs):
        client, headers = self.as_user(key)
        return getattr(client, method)(url, headers={**headers, **kwargs.pop('headers', {})}, **kwargs)

    def request_book(self, key, isbn=SOSEON):
        return self.call(key, 'post', '/api/requests', json={'bookId': isbn})

    def add_request(self, key, isbn, when, price='keep'):
        """Insert a request with a chosen time, creating the book from the fixture if needed."""
        book = db.session.query(RequestedBook).filter_by(isbn=isbn).one_or_none()
        if book is None:
            meta = self.app.config['BOOK_PROVIDER'].get(isbn)
            book = RequestedBook(isbn=isbn, title=meta['title'], author=meta['author'], publisher=meta['publisher'], price=meta['price'],
                                 pub_date=datetime.strptime(meta['pub_date'], '%Y-%m-%d').date(), description=meta['description'])
            db.session.add(book)
            db.session.flush()
        item = BookRequest(user_id=self.users[key], requested_book_id=book.id, requested_at=when)
        db.session.add(item)
        db.session.commit()
        return item

    def set_status(self, isbn, status, ids=None):
        return self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': ids or [isbn], 'status': status, 'reason': '사유'})
