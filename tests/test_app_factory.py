import os
import unittest
from unittest.mock import patch
from app import create_app


class SecretKeyTests(unittest.TestCase):
    def test_blank_or_missing_secret_key_stops_startup(self):
        for value in (None, '', '   '):
            env = {k: v for k, v in os.environ.items() if k != 'SECRET_KEY'}
            if value is not None:
                env['SECRET_KEY'] = value
            with patch.dict(os.environ, env, clear=True), patch('app.load_dotenv'):
                with self.assertRaises(RuntimeError, msg=repr(value)):
                    create_app()

    def test_app_starts_with_a_key_and_registers_auth(self):
        with patch.dict(os.environ, {'SECRET_KEY': 'x' * 32}), patch('app.load_dotenv'):
            app = create_app()
        self.assertIn('auth', app.blueprints)
        self.assertEqual(app.test_client().get('/api/health').get_json()['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
