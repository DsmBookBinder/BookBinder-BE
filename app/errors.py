"""Shared error response helper. Error messages are the FE's Korean wording."""


def error(code, message, status, fields=None):
    body = {'code': code, 'message': message}
    if fields:
        body['fields'] = fields
    return {'error': body}, status


AUTH_REQUIRED = ('AUTH_REQUIRED', '로그인이 필요해요.', 401)
FORBIDDEN_STUDENT = ('FORBIDDEN', '학생 계정으로 로그인해야 신청할 수 있어요.', 403)
FORBIDDEN_LIBRARIAN = ('FORBIDDEN', '관리자 전용 기능이에요.', 403)
CSRF_FAILED = ('CSRF_FAILED', '요청을 확인할 수 없어요. 새로고침 후 다시 시도해 주세요.', 403)
BOOK_NOT_FOUND = ('NOT_FOUND', '도서를 찾을 수 없어요.', 404)
FILTER_INVALID = ('VALIDATION_ERROR', '검색 조건을 확인해 주세요.', 400)
INTERNAL = ('INTERNAL_ERROR', '서버 오류가 발생했어요.', 500)
PROVIDER_MESSAGE = '도서 정보를 불러올 수 없어요. 잠시 후 다시 시도해 주세요.'
CATALOG_MESSAGE = '소장 여부를 확인할 수 없어 지금은 신청할 수 없어요.'


def fail(spec, fields=None):
    return error(spec[0], spec[1], spec[2], fields)
