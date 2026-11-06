import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from flask import Flask
from app.db import db
from app.auth.models import User
from app.auth.routes import resolve_session, new_session, COOKIE
from app.books.models import BookRequest, RequestedBook, Notification
from app.admin.routes import admin_api
from app.requests.routes import requests_api


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite://')
        db.init_app(self.app)
        self.app.before_request(resolve_session)
        self.app.register_blueprint(admin_api)
        self.app.register_blueprint(requests_api)
        self.ctx = self.app.app_context(); self.ctx.push()
        db.create_all()
        db.session.add_all([User(id=1, login_id='student', password_hash='unused', name='학생', role='student', grade_class='2-3', student_no='20301'),
                            User(id=2, login_id='librarian', password_hash='unused', name='사서', role='librarian'),
                            User(id=3, login_id='other', password_hash='unused', name='다른학생', role='student', student_no='20302')])
        book = RequestedBook(id=1, isbn='9780306406157', title='=HYPERLINK("bad")', author='저자', publisher='출판사', price=999)
        db.session.add(book); db.session.flush()
        db.session.add_all([BookRequest(id=1,user_id=1,book=book,price=18000,requested_at=datetime(2026,9,22,15)),
                            BookRequest(id=2,user_id=3,book=book,price=18000,requested_at=datetime(2026,9,23,15))])
        db.session.commit()
        self.client = self.app.test_client(); self.login(2)

    def login(self, uid):
        token, csrf = new_session(uid); db.session.commit()
        self.client.set_cookie(COOKIE, token)
        self.headers = {'X-CSRF-Token': csrf}

    def tearDown(self):
        db.session.remove(); db.drop_all(); db.engine.dispose(); self.ctx.pop()

    def change(self, rid=1, status='APPROVED', expected='PENDING', **extra):
        return self.client.patch(f'/api/admin/requests/{rid}/status', json=dict(status=status, expected_status=expected, **extra), headers=self.headers)

    def export(self, ids=None, group=True):
        return self.client.post('/api/admin/requests/export', json={'request_ids': ids or ['1','2'], 'group_by_isbn':group}, headers=self.headers)

    def test_auth_roles_csrf(self):
        self.client.delete_cookie(COOKIE)
        self.assertEqual(self.client.get('/api/admin/requests').status_code,401)
        self.login(1)
        self.assertEqual(self.client.get('/api/admin/requests').status_code,403)
        self.assertEqual(self.export().status_code,403)
        self.login(2); self.headers={}
        self.assertEqual(self.change().get_json()['error']['code'],'CSRF_FAILED')
        self.assertEqual(self.export().status_code,403)

    def test_list_dates_exact_student_and_snapshot(self):
        result=self.client.get('/api/admin/requests?from=2026-09-23&to=2026-09-23').get_json()
        self.assertEqual(result['total'],1); self.assertEqual(result['items'][0]['id'],'1')
        self.assertEqual(result['items'][0]['price'],18000)
        self.assertEqual(result['items'][0]['user']['student_no'],'20301')
        self.assertEqual(self.client.get('/api/admin/requests?student_no=203').get_json()['total'],0)
        result=self.client.get('/api/admin/requests?page_size=1').get_json()
        self.assertEqual(result['total'],2); self.assertEqual(result['items'][0]['id'],'2')
        for query in ['from=2026-09-24&to=2026-09-23','from=2026-02-30','from=2026-9-23','page=0','page_size=101','status=pending','status=']:
            self.assertEqual(self.client.get('/api/admin/requests?'+query).status_code,400,query)

    def test_transitions_conflicts_and_independence(self):
        self.assertEqual(self.change().status_code,200)
        self.assertEqual(self.change().status_code,409)
        self.assertEqual(db.session.query(Notification).count(),1)
        self.assertEqual(db.session.get(BookRequest,2).status,'PENDING')
        self.assertEqual(self.change(status='PURCHASED',expected='APPROVED').status_code,200)
        self.assertEqual(self.change(status='REJECTED',expected='PURCHASED',reason='사유').status_code,409)
        self.assertEqual(self.change(rid=999).status_code,404)
        self.assertEqual(self.client.get('/api/admin/requests?status=PURCHASED').get_json()['total'],1)

    def test_student_sees_admin_state_and_frozen_price(self):
        self.change()
        book = db.session.get(RequestedBook, 1)
        book.price = 77777
        db.session.commit()
        self.login(1)
        result = self.client.get('/api/me/requests?status=APPROVED').get_json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['status'], 'APPROVED')
        self.assertEqual(result['items'][0]['price'], 18000)
        self.assertEqual(self.client.get('/api/me/notifications').get_json()['unread_count'], 1)

    def test_rejection_validation(self):
        for reason in ['', '   ', '가'*501]:
            self.assertEqual(self.change(status='REJECTED',reason=reason).status_code,400)
        result=self.change(status='REJECTED',reason='  사유  ').get_json()
        self.assertEqual(result['reject_reason'],'사유'); self.assertEqual(result['processed_by'],'2')
        self.assertTrue(result['processed_at'].endswith('Z'))
        self.assertEqual(self.client.patch('/api/admin/requests/2/status',json={'status':'APPROVED'},headers=self.headers).status_code,400)

    def test_notification_failure_rolls_back(self):
        original=db.session.add
        def fail(obj):
            if isinstance(obj,Notification): raise RuntimeError('private detail')
            return original(obj)
        with patch.object(db.session,'add',side_effect=fail):
            response=self.change()
        self.assertEqual(response.status_code,500)
        self.assertNotIn('private detail',response.get_data(as_text=True))
        self.assertEqual(db.session.get(BookRequest,1).status,'PENDING')
        self.assertEqual(db.session.query(Notification).count(),0)

    def test_export_validation(self):
        self.assertEqual(self.export().status_code,409)
        self.change(); self.change(rid=2)
        self.assertEqual(self.export(ids=['999']).status_code,404)
        row=db.session.get(BookRequest,2); row.price=None; db.session.commit()
        self.assertEqual(self.export().status_code,409)
        row.price=100; db.session.commit()
        self.assertEqual(self.export(group=False).status_code,409)
        self.assertEqual(self.export(ids=[str(i) for i in range(1,1002)]).status_code,413)
        self.assertEqual(self.export(ids=[True]).status_code,400)
        self.assertEqual(self.export(group='true').status_code,400)
        self.assertEqual(self.client.post('/api/admin/requests/export',json={'request_ids':[],'group_by_isbn':True},headers=self.headers).status_code,400)

    def test_xlsx_types_grouping_and_no_mutation(self):
        self.change(); self.change(rid=2)
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        for grouped, count in [(True,2),(False,3)]:
            result=self.export(ids=['1','1','2'],group=grouped)
            self.assertEqual(result.status_code,200)
            self.assertIn('.xlsx',result.headers['Content-Disposition'])
            self.assertEqual(result.headers['Cache-Control'],'no-store')
            with ZipFile(BytesIO(result.data)) as archive:
                root=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(len(root.findall('s:sheetData/s:row',ns)),count)
            self.assertEqual(root.findall('.//s:f',ns),[])
            self.assertEqual(root.find('.//s:c[@r="D2"]',ns).get('t'),'inlineStr')
            self.assertEqual(root.find('.//s:c[@r="E2"]/s:v',ns).text,'18000')
            self.assertNotIn('20301',result.data.decode('latin1'))
        self.assertEqual(db.session.get(BookRequest,1).status,'APPROVED')
        self.assertEqual(db.session.query(Notification).count(),2)

    def test_null_isbn_and_zero_price(self):
        book=RequestedBook(isbn=None,title='책',author='저자',publisher='출판사')
        db.session.add(book); db.session.flush()
        db.session.add_all([BookRequest(id=3,user_id=1,book=book,price=0,status='APPROVED'),BookRequest(id=4,user_id=3,book=book,price=0,status='APPROVED')]);db.session.commit()
        from app.admin.services import export_rows
        self.assertEqual(len(export_rows({'request_ids':['3','4'],'group_by_isbn':True})),2)

if __name__=='__main__': unittest.main()
