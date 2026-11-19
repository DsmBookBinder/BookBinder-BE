from flask import Blueprint, current_app, g, request, send_file
from app.db import db
from app.errors import AUTH_REQUIRED, CSRF_FAILED, FORBIDDEN_LIBRARIAN, INTERNAL, error, fail
from app.requests.routes import field
from app.serializers import kst_now
from app.admin import services
from app.admin.xlsx import workbook

admin_api = Blueprint('admin', __name__, url_prefix='/api/admin')
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
BOOK_HEADER = ['도서명', '저자', '출판사', 'ISBN', '가격', '신청 수', '신청 학생', '최근 신청일', '검토 결과', '구매 여부', '반려 사유']
BOOK_WIDTHS = [32, 22, 16, 16, 10, 8, 40, 12, 10, 10, 40]
BUDGET_HEADER = ['구분', '도서명', '상태', '단가', '구매 수량', '소계']
BUDGET_WIDTHS = [16, 32, 10, 12, 10, 14]


@admin_api.before_request
def authorize():
    user = getattr(g, 'current_user', None)
    if user is None or field(user, 'id') is None:
        return fail(AUTH_REQUIRED)
    if field(user, 'role') != 'librarian':
        return fail(FORBIDDEN_LIBRARIAN)
    if request.method in ('POST', 'PATCH', 'DELETE') and getattr(g, 'csrf_valid', False) is not True:
        return fail(CSRF_FAILED)


@admin_api.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@admin_api.errorhandler(services.APIError)
def expected_error(exc):
    db.session.rollback()
    return error(exc.code, exc.message, exc.status, exc.fields)


@admin_api.errorhandler(Exception)
def unexpected_error(exc):
    db.session.rollback()
    current_app.logger.error('Admin operation failed (%s)', type(exc).__name__)
    return fail(INTERNAL)


def optional_json_body():
    """No body -> {} (defaults). A body that is not a JSON object is rejected, never treated as 'no filter'."""
    if not request.get_data(cache=True).strip():
        return {}
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise services.APIError()
    return body


def download(rows, header, widths, prefix):
    filename = f'{prefix}_{kst_now().strftime("%Y%m%d")}.xlsx'
    # werkzeug adds filename*=UTF-8''... for the non-ASCII name.
    return send_file(workbook(header, rows, widths), mimetype=XLSX, as_attachment=True, download_name=filename)


@admin_api.get('/books')
def list_books():
    return services.list_books(request.args)


@admin_api.post('/books/status')
def change_status():
    return services.change_status(request.get_json(silent=True), field(g.current_user, 'id'))


@admin_api.post('/books/revert')
def revert():
    return services.revert_status(request.get_json(silent=True))


@admin_api.patch('/books/<book_id>/price')
def price(book_id):
    return services.set_price(book_id, request.get_json(silent=True))


@admin_api.patch('/books/<book_id>/quantity')
def quantity(book_id):
    return services.set_quantity(book_id, request.get_json(silent=True))


@admin_api.post('/books/export')
def export_books():
    return download(services.export_books(optional_json_body()), BOOK_HEADER, BOOK_WIDTHS, '신청도서목록')


@admin_api.get('/budget')
def budget():
    return services.budget(request.args)


@admin_api.post('/budget/export')
def export_budget():
    return download(services.budget_export(optional_json_body()), BUDGET_HEADER, BUDGET_WIDTHS, '예상예산')


@admin_api.get('/summary')
def summary():
    return services.summary()
