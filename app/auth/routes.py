import click
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from flask import Blueprint, current_app, g, request, make_response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from app.auth.models import User, AuthSession, now
from app.errors import CSRF_FAILED, AUTH_REQUIRED, error, fail
from app.serializers import user_out
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
    return None if g.csrf_valid else fail(CSRF_FAILED)


def valid_text(value, maximum):
    return isinstance(value, str) and 1 <= len(value.strip()) <= maximum


LOGIN_ID = re.compile(r'[a-zA-Z0-9]{4,20}')
CLASS_NO = re.compile(r'[1-3]-[0-9]{1,2}')
STUDENT_NO = re.compile(r'[0-9]{5}')
FIELD_MESSAGES = {
    'name': '이름을 입력해 주세요.',
    'loginId': '영문·숫자 4~20자로 입력해 주세요.',
    'password': '8자 이상 입력해 주세요.',
    'password2': '비밀번호가 서로 달라요.',
    'classNo': '2-3처럼 학년-반으로 입력해 주세요.',
    'studentNo': '숫자 5자리 학번을 입력해 주세요.',
}


def text(data, key):
    value = data.get(key)
    return value if isinstance(value, str) else ''


def check_profile(data):
    """FE rules shared by signup and profile edit. Returns {field: message} for bad fields."""
    bad = {}
    if not valid_text(data.get('name'), 50):
        bad['name'] = FIELD_MESSAGES['name']
    if not CLASS_NO.fullmatch(text(data, 'classNo').strip()):
        bad['classNo'] = FIELD_MESSAGES['classNo']
    if not STUDENT_NO.fullmatch(text(data, 'studentNo')):
        bad['studentNo'] = FIELD_MESSAGES['studentNo']
    return bad


@api.get('/auth/csrf')
def csrf_token():
    if g.auth_session:
        # Rotate CSRF without replacing the existing session cookie.
        csrf = secrets.token_urlsafe(32)
        g.auth_session.csrf_hash = digest(csrf)
        db.session.commit()
        response = make_response({'csrfToken': csrf})
        response.headers['Cache-Control'] = 'no-store'
        return response
    token, csrf = new_session()
    db.session.commit()
    return attach_cookie(make_response({'csrfToken': csrf}), token)


@api.post('/auth/signup')
def signup():
    denied = csrf_required()
    if denied: return denied
    data = request.get_json(silent=True)
    allowed = {'loginId', 'password', 'password2', 'name', 'classNo', 'studentNo'}
    if not isinstance(data, dict) or not set(data) <= allowed:
        return error('VALIDATION_ERROR', '가입 정보를 확인해 주세요.', 400)
    fields = check_profile(data)
    login_id, password = text(data, 'loginId').strip(), text(data, 'password')
    if not LOGIN_ID.fullmatch(login_id):
        fields['loginId'] = FIELD_MESSAGES['loginId']
    if not 8 <= len(password) <= 128:
        fields['password'] = FIELD_MESSAGES['password']
    if 'password2' in data and data['password2'] != password:
        fields['password2'] = FIELD_MESSAGES['password2']
    if fields:
        return error('VALIDATION_ERROR', '가입 정보를 확인해 주세요.', 400, fields)
    account = User(login_id=login_id.lower(), password_hash=generate_password_hash(password), name=data['name'].strip(), role='student',
                   grade_class=data['classNo'].strip(), student_no=data['studentNo'])
    duplicate = error('DUPLICATE_LOGIN_ID', '이미 사용 중인 아이디예요.', 409, {'loginId': '이미 사용 중인 아이디예요.'})
    if db.session.scalar(select(User.id).where(User.login_id == account.login_id)) is not None:
        return duplicate
    try:
        db.session.add(account)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return duplicate
    return user_out(account), 201


@api.post('/auth/login')
def login():
    denied = csrf_required()
    if denied: return denied
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'loginId', 'password'} or not isinstance(data['loginId'], str) or not isinstance(data['password'], str) or not 1 <= len(data['loginId'].strip()) <= 50 or not 1 <= len(data['password']) <= 128:
        return error('VALIDATION_ERROR', '로그인 정보를 확인해 주세요.', 400)
    account = db.session.execute(select(User).where(User.login_id == data['loginId'].strip().lower())).scalar_one_or_none()
    valid = check_password_hash(account.password_hash if account else DUMMY_HASH, data['password'])
    if not valid or account is None:
        return error('INVALID_CREDENTIALS', '아이디 또는 비밀번호가 맞지 않아요.', 401)
    if g.auth_session: db.session.delete(g.auth_session)
    token, csrf = new_session(account.id)
    db.session.commit()
    response = make_response({'user': user_out(account), 'csrfToken': csrf})
    return attach_cookie(response, token)


@api.post('/auth/logout')
def logout():
    # FE logout() cannot fail: without a session there is nothing to end, so answer 204 as well.
    if g.current_user:
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
    if not g.current_user: return fail(AUTH_REQUIRED)
    if request.method == 'PATCH':
        denied = csrf_required()
        if denied: return denied
        data = request.get_json(silent=True)
        message = '이름, 학년/반(예: 2-3), 학번(숫자 5자리)을 확인해 주세요.'
        if not isinstance(data, dict):
            return error('VALIDATION_ERROR', message, 400)
        if g.current_user.role == 'student':
            # The FE profile modal always sends all three values together.
            if set(data) != {'name', 'classNo', 'studentNo'}:
                return error('VALIDATION_ERROR', message, 400)
            fields = check_profile(data)
            if fields:
                return error('VALIDATION_ERROR', message, 400, fields)
            g.current_user.name = data['name'].strip()
            g.current_user.grade_class = data['classNo'].strip()
            g.current_user.student_no = data['studentNo']
        else:
            if set(data) != {'name'} or not valid_text(data['name'], 50):
                return error('VALIDATION_ERROR', FIELD_MESSAGES['name'], 400, {'name': FIELD_MESSAGES['name']})
            g.current_user.name = data['name'].strip()
        db.session.commit()
    return user_out(g.current_user)


@api.patch('/me/password')
def change_password():
    if not g.current_user: return fail(AUTH_REQUIRED)
    denied = csrf_required()
    if denied: return denied
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not {'currentPassword', 'newPassword'} <= set(data) or not set(data) <= {'currentPassword', 'newPassword', 'newPasswordConfirm'} \
            or not isinstance(data['currentPassword'], str) or not isinstance(data['newPassword'], str):
        return error('VALIDATION_ERROR', '비밀번호 정보를 확인해 주세요.', 400)
    if not 8 <= len(data['newPassword']) <= 128:
        return error('VALIDATION_ERROR', '새 비밀번호는 8자 이상이어야 해요.', 400, {'newPassword': '새 비밀번호는 8자 이상이어야 해요.'})
    if 'newPasswordConfirm' in data and data['newPasswordConfirm'] != data['newPassword']:
        return error('VALIDATION_ERROR', '새 비밀번호가 서로 달라요.', 400, {'newPasswordConfirm': '새 비밀번호가 서로 달라요.'})
    if not check_password_hash(g.current_user.password_hash, data['currentPassword']):
        return error('INVALID_PASSWORD', '현재 비밀번호가 맞지 않아요.', 400)
    g.current_user.password_hash = generate_password_hash(data['newPassword'])
    # Sign out every other session of this user; the current one stays.
    db.session.execute(delete(AuthSession).where(AuthSession.user_id == g.current_user.id, AuthSession.token_hash != g.auth_session.token_hash))
    db.session.commit()
    response = make_response('', 204)
    response.headers['Cache-Control'] = 'no-store'
    return response


@api.cli.command('create-librarian')
@click.argument('login_id')
@click.option('--name', required=True)
def create_librarian(login_id, name):
    """Operator-only librarian issuing (the FE has no public librarian signup)."""
    if not LOGIN_ID.fullmatch(login_id) or not valid_text(name, 50):
        raise click.ClickException('아이디 또는 이름을 확인해 주세요.')
    password = click.prompt('비밀번호', hide_input=True, confirmation_prompt=True)
    if not 8 <= len(password) <= 128:
        raise click.ClickException('비밀번호는 8~128자로 입력해 주세요.')
    account = User(login_id=login_id.lower(), name=name.strip(), role='librarian', password_hash=generate_password_hash(password))
    db.session.add(account)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise click.ClickException('계정을 발급할 수 없습니다. 아이디 중복을 확인해 주세요.') from exc
    click.echo('사서 계정을 발급했습니다.')
