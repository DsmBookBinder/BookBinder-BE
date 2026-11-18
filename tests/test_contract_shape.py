import re
import unittest
from datetime import datetime, timezone
from tests.helpers import AppCase, CONVENIENCE, SOSEON

CAMEL = re.compile(r'^[a-z][a-zA-Z0-9]*$')
STATES = {'pending', 'approved', 'rejected', 'purchased'}
TIME_KEYS = {'createdAt', 'joinedAt', 'updatedAt', 'at', 'latestAt', 'firstAt'}


def walk(value, path='$'):
    """Yield (path, key, value) for every dict entry."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield path, key, inner
            yield from walk(inner, f'{path}.{key}')
    elif isinstance(value, list):
        for inner in value:
            yield from walk(inner, path)


class ContractShapeTests(AppCase):
    def check(self, body):
        for path, key, value in walk(body):
            self.assertRegex(key, CAMEL, f'{path}.{key} must be camelCase')
            if key in TIME_KEYS:
                self.assertIs(type(value), int, f'{path}.{key} must be epoch ms int')
            if key == 'id' or key.endswith('Id'):
                self.assertIsInstance(value, str, f'{path}.{key} must be a string id')
            if key == 'status':
                self.assertIn(value, STATES, f'{path}.{key}')
            if key == 'role':
                self.assertIn(value, {'student', 'librarian'})

    def test_every_response_uses_the_fe_shape(self):
        self.add_request('s1', SOSEON, datetime(2026, 11, 17, 1))
        self.add_request('s2', CONVENIENCE, datetime(2026, 11, 18, 1))
        self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': 'rejected', 'reason': '중복'})
        approved = self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [CONVENIENCE], 'status': 'approved'})
        self.assertEqual(approved.get_json()['changed'], 1)  # the preparation really changed the book
        notes = self.call('s2', 'get', '/api/me/notifications').get_json()['items']
        self.assertEqual([(n['bookId'], n['status']) for n in notes], [(CONVENIENCE, 'approved')])  # fields are checked on real data
        calls = [('s1', 'get', '/api/me'), ('lib', 'get', '/api/me'), ('s1', 'get', '/api/me/requests'), ('s2', 'get', '/api/me/notifications'),
                 ('s1', 'get', '/api/books?q=책&sort=popular'), ('s1', 'get', f'/api/books/{SOSEON}'), ('s1', 'get', '/api/books/popular'),
                 ('lib', 'get', '/api/admin/books'), ('lib', 'get', '/api/admin/budget?period=all'), ('lib', 'get', '/api/admin/summary'),
                 ('lib', 'post', '/api/admin/books/revert', {'json': {'bookIds': [CONVENIENCE]}})]
        for key, method, url, *extra in calls:
            response = self.call(key, method, url, **(extra[0] if extra else {}))
            self.assertEqual(response.status_code, 200, url)
            self.check(response.get_json())
        # createdAt is the exact epoch ms of the stored request time (2026-11-17T01:00:00Z), not just "an int".
        created = self.call('s1', 'get', '/api/me/requests').get_json()['items'][0]['request']['createdAt']
        self.assertEqual(created, int(datetime(2026, 11, 17, 1, tzinfo=timezone.utc).timestamp() * 1000))
        # status values on the way in are lowercase only.
        upper = self.call('lib', 'post', '/api/admin/books/status', json={'bookIds': [SOSEON], 'status': 'APPROVED'})
        self.assertEqual(upper.status_code, 400)
        self.assertEqual(self.call('lib', 'get', '/api/admin/books?status=PENDING').status_code, 400)
        error = self.call('s1', 'post', '/api/requests', json={'bookId': 'x'}).get_json()
        self.assertEqual(set(error), {'error'})
        self.assertEqual(set(error['error']) - {'fields'}, {'code', 'message'})


if __name__ == '__main__':
    unittest.main()
