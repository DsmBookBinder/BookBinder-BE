"""Shared book request tables. User authentication is owned by the auth domain."""
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, UniqueConstraint
from app.db import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RequestedBook(db.Model):
    __tablename__ = 'requested_books'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    isbn = db.Column(db.String(13), nullable=False, unique=True)
    title = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(100), nullable=False)
    publisher = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Integer, nullable=True)
    cover_url = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(16), nullable=False, default='pending')
    reject_reason = db.Column(db.String(255), nullable=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (CheckConstraint('quantity >= 0'), CheckConstraint('price IS NULL OR price >= 0'),
                      CheckConstraint("status IN ('pending','approved','rejected','purchased')"))


class BookRequest(db.Model):
    __tablename__ = 'book_requests'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    # A's user model owns the referenced table; migration adds its foreign key.
    user_id = db.Column(db.BigInteger, db.ForeignKey('users.id'), nullable=False, index=True)
    requested_book_id = db.Column(db.BigInteger, db.ForeignKey('requested_books.id'), nullable=False, index=True)
    requested_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    book = db.relationship(RequestedBook)
    __table_args__ = (UniqueConstraint('user_id', 'requested_book_id', name='uq_request_user_book'),)


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('users.id'), nullable=False, index=True)
    request_id = db.Column(db.BigInteger, db.ForeignKey('book_requests.id'), nullable=False, index=True)
    type = db.Column(db.String(16), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    __table_args__ = (CheckConstraint("type IN ('approved','rejected','purchased')"),)
