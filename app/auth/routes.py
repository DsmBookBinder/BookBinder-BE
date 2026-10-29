import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from flask import Blueprint, current_app, g, request, make_response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from app.auth.models import User, AuthSession, now
from app.books.routes import error
from app.db import db

api = Blueprint('auth', __name__, url_prefix='/api')
COOKIE = 'bookbinder_session'
DUMMY_HASH = generate_password_hash('bookbinder-invalid-password')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def resolve_session():
    g.current_user = None
    g.auth_session = None
    g.csrf_valid = False
    token = request.cookies.get(COOKIE, '')
    if not token or len(token) > 200:
        return
    session = db.session.get(AuthSession, digest(token))
    if session is None or session.expires_at <= now():
        return
    g.auth_session = session
    g.current_user = session.user
    csrf = request.headers.get('X-CSRF-Token', '')
    if csrf and len(csrf) <= 200:
        g.csrf_valid = hmac.compare_digest(session.csrf_hash, digest(csrf))


def new_session(user_id=None):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    row = AuthSession(token_hash=digest(token), csrf_hash=digest(csrf), user_id=user_id, expires_at=now() + timedelta(hours=12))
    db.session.add(row)
    return token, csrf


def attach_cookie(response, token):
    response.set_cookie(COOKIE, token, httponly=True, secure=current_app.config.get('SESSION_COOKIE_SECURE', False), samesite='Lax', max_age=12*60*60, path='/')
    response.headers['Cache-Control'] = 'no-store'
    return response


def csrf_required():
    return None if g.csrf_valid else error('CSRF_FAILED', '요청을 확인할 수 없습니다.', 403)


def private(user):
    return {'id': str(user.id), 'login_id': user.login_id, 'name': user.name, 'role': user.role.upper(),
            'grade_class': user.grade_class, 'student_no': user.student_no, 'created_at': user.created_at.isoformat(timespec='seconds') + 'Z'}


def valid_text(value, maximum):
    return isinstance(value, str) and 1 <= len(value.strip()) <= maximum


@api.get('/auth/csrf')
def csrf_token():
    if g.auth_session:
        # Rotate CSRF without replacing the existing session cookie.
        csrf = secrets.token_urlsafe(32)
        g.auth_session.csrf_hash = digest(csrf)
        db.session.commit()
        response = make_response({'csrf_token': csrf})
        response.headers['Cache-Control'] = 'no-store'
        return response
    token, csrf = new_session()
    db.session.commit()
    return attach_cookie(make_response({'csrf_token': csrf}), token)


@api.post('/auth/signup')
def signup():
    denied = csrf_required()
    if denied: return denied
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'login_id', 'password', 'name', 'grade_class', 'student_no'}:
        return error('VALIDATION_ERROR', '가입 정보를 확인해 주세요.', 400)
    login = data['login_id']
    password = data['password']
    if not isinstance(login, str) or not re.fullmatch(r'[A-Za-z0-9_]{4,50}', login) or not isinstance(password, str) or not 10 <= len(password) <= 128 or not valid_text(data['name'], 50) or not valid_text(data['grade_class'], 20) or not valid_text(data['student_no'], 20):
        return error('VALIDATION_ERROR', '가입 정보를 확인해 주세요.', 400)
    account = User(login_id=login.lower(), password_hash=generate_password_hash(password), name=data['name'].strip(), role='student', grade_class=data['grade_class'].strip(), student_no=data['student_no'].strip())
    try:
        db.session.add(account)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error('DUPLICATE_LOGIN_ID', '이미 사용 중인 아이디입니다.', 409)
    return {'id': str(account.id), 'login_id': account.login_id, 'name': account.name, 'role': 'STUDENT'}, 201


@api.post('/auth/login')
def login():
    denied = csrf_required()
    if denied: return denied
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'login_id', 'password'} or not isinstance(data['login_id'], str) or not isinstance(data['password'], str) or not 1 <= len(data['login_id']) <= 50 or not 1 <= len(data['password']) <= 128:
        return error('VALIDATION_ERROR', '로그인 정보를 확인해 주세요.', 400)
    account = db.session.execute(select(User).where(User.login_id == data['login_id'].lower())).scalar_one_or_none()
    valid = check_password_hash(account.password_hash if account else DUMMY_HASH, data['password'])
    if not valid or account is None:
        return error('INVALID_CREDENTIALS', '아이디 또는 비밀번호가 맞지 않습니다.', 401)
    if g.auth_session: db.session.delete(g.auth_session)
    token, unused_csrf = new_session(account.id)
    db.session.commit()
    response = make_response({'user': {'id': str(account.id), 'name': account.name, 'role': account.role.upper()}})
    return attach_cookie(response, token)


@api.post('/auth/logout')
def logout():
    if not g.current_user: return error('AUTH_REQUIRED', '로그인이 필요합니다.', 401)
    denied = csrf_required()
    if denied: return denied
    db.session.delete(g.auth_session)
    db.session.commit()
    response = make_response('', 204)
    response.delete_cookie(COOKIE, path='/', secure=current_app.config.get('SESSION_COOKIE_SECURE', False), httponly=True, samesite='Lax')
    response.headers['Cache-Control'] = 'no-store'
    return response


@api.route('/me', methods=['GET', 'PATCH'])
def me():
    if not g.current_user: return error('AUTH_REQUIRED', '로그인이 필요합니다.', 401)
    if request.method == 'PATCH':
        denied = csrf_required()
        if denied: return denied
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not data or not set(data) <= {'name', 'grade_class'} or ('name' in data and not valid_text(data['name'], 50)) or ('grade_class' in data and (g.current_user.role != 'student' or not valid_text(data['grade_class'], 20))):
            return error('VALIDATION_ERROR', '수정 정보를 확인해 주세요.', 400)
        for key, value in data.items(): setattr(g.current_user, key, value.strip())
        db.session.commit()
    return private(g.current_user)
