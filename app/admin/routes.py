from datetime import datetime
from flask import Blueprint, g, request, send_file, current_app
from app.db import db
from app.books.routes import error
from app.requests.routes import field
from app.admin import services
from app.admin.xlsx import workbook

admin_api = Blueprint('admin', __name__, url_prefix='/api/admin')


@admin_api.before_request
def authorize():
    user = getattr(g, 'current_user', None)
    if user is None or field(user, 'id') is None:
        return error('AUTH_REQUIRED', '로그인이 필요합니다.', 401)
    if field(user, 'role') != 'librarian':
        return error('FORBIDDEN', '사서만 이용할 수 있습니다.', 403)
    if request.method in ('POST', 'PATCH') and getattr(g, 'csrf_valid', False) is not True:
        return error('CSRF_FAILED', '요청을 확인할 수 없습니다.', 403)


@admin_api.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@admin_api.errorhandler(services.APIError)
def expected_error(exc):
    db.session.rollback()
    return error(exc.code, '요청 조건을 확인해 주세요.', exc.status)


@admin_api.errorhandler(Exception)
def unexpected_error(exc):
    db.session.rollback()
    current_app.logger.error('Admin operation failed (%s)', type(exc).__name__)
    return error('INTERNAL_ERROR', '서버 오류가 발생했습니다.', 500)


@admin_api.get('/requests')
def list_requests():
    return services.list_requests(request.args)


@admin_api.patch('/requests/<request_id>/status')
def change_status(request_id):
    return services.change_status(request_id, request.get_json(silent=True), field(g.current_user, 'id'))


@admin_api.post('/requests/export')
def export_requests():
    rows = services.export_rows(request.get_json(silent=True))
    filename = 'BookBinder_orders_' + datetime.now(services.KST).strftime('%Y%m%d') + '.xlsx'
    return send_file(workbook(rows), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=filename)
