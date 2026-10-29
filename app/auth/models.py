from datetime import datetime, timezone
from app.db import db


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(16), nullable=False, default='student')
    grade_class = db.Column(db.String(20))
    student_no = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    __table_args__ = (db.CheckConstraint("role IN ('student','librarian')"),)


class AuthSession(db.Model):
    __tablename__ = 'auth_sessions'
    token_hash = db.Column(db.String(64), primary_key=True)
    csrf_hash = db.Column(db.String(64), nullable=False)
    user_id = db.Column(db.BigInteger, db.ForeignKey('users.id'), nullable=True, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    user = db.relationship(User)
