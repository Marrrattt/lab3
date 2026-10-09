"""Console navigation and HTTP boundary tests without changing the database."""
from contextlib import redirect_stdout, redirect_stderr
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
import io
import json
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

from interaction import OperationCancelled
from main import dispatch, main
from reporting import run_action
import web


class ConsoleCancellationTests(unittest.TestCase):
    def test_cancel_each_prompt_without_writes(self):
        scripts = [
            ('1', ['/back']), ('2', ['books', 'назад']),
            ('3', ['books', '/back']), ('4', ['books', '/back']),
            ('4', ['books', '1', '/back']), ('5', ['books', '/back']),
            ('5', ['books', 'id', '/back']), ('5', ['books', 'id', '[1]', '/back']),
            ('6', ['authors', 'отмена']), ('7', ['/back']),
            ('7', ['{"name":"x","country":"y"}', '/back']),
            ('8', ['authors', '/back']), ('9', ['/back']),
            ('9', ['{"name":"x","email":"x@example.test"}', '/back']),
        ]
        for choice, answers in scripts:
            with self.subTest(choice=choice, answers=answers):
                store = Mock()
                with patch('builtins.input', side_effect=answers), redirect_stdout(io.StringIO()):
                    with self.assertRaises(OperationCancelled):
                        dispatch(store, choice)
                self.assertEqual(store.mock_calls, [])

    def test_cancellation_is_not_reported_as_failure(self):
        with self.assertRaises(OperationCancelled):
            run_action('cancel', Mock(side_effect=OperationCancelled()))

    def test_main_returns_to_menu_on_back_and_ctrl_c(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.closed = False
        out, err = io.StringIO(), io.StringIO()
        with patch('main.configure_logging'), patch('main.connect', return_value=connection), \
             patch('getpass.getpass', return_value='test'), \
             patch('builtins.input', side_effect=['user','7','{"name":"x","country":"y"}','/back','4',KeyboardInterrupt(),'0']), \
             redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(), 0)
        self.assertEqual(out.getvalue().count('Операция отменена'), 2)
        self.assertNotIn('Ошибка', err.getvalue())


class WebBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.connection = Mock()
        self.patch_connect = patch('web.connect', return_value=self.connection)
        self.patch_connect.start()

    def tearDown(self):
        self.patch_connect.stop()
        with web.SESSIONS_LOCK:
            web.SESSIONS.clear()

    def request(self, path, data=None, headers=None):
        request = Request(self.base + path, data=None if data is None else json.dumps(data).encode(),
                          headers={'Content-Type':'application/json', **(headers or {})})
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers

    def login(self):
        status, data, headers = self.request('/api/login', {'username':'test','password':'test'})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        return data['csrf']

    def test_anonymous_requests_rejected(self):
        self.assertEqual(self.request('/api/session')[0], 401)
        self.assertEqual(self.request('/api/operation', {'action':'select','table':'books'})[0], 401)

    def test_login_session_csrf_and_logout(self):
        csrf = self.login()
        self.assertEqual(self.request('/api/session')[1]['username'], 'test')
        self.assertEqual(self.request('/api/operation', {'action':'select','table':'books'})[0], 403)
        with patch('web.dispatch_operation', return_value=[{'id':1,'title':'Test'}]) as operation:
            status, data, _ = self.request('/api/operation', {'action':'select','table':'books'}, {'X-CSRF-Token':csrf})
            self.assertEqual(status, 200)
            self.assertEqual(data['result'][0]['id'], 1)
            operation.assert_called_once()
        self.assertEqual(self.request('/api/logout', {}, {'X-CSRF-Token':csrf})[0], 200)
        self.connection.close.assert_called_once()
        self.assertEqual(self.request('/api/session')[0], 401)

    def test_cross_origin_rejected(self):
        self.assertEqual(self.request('/api/login', {'username':'x','password':'y'}, {'Origin':'http://evil.invalid'})[0], 403)
        self.assertFalse(self.connection.mock_calls)

    def test_password_never_returned_or_stored_in_session(self):
        self.login()
        payload = self.request('/api/session')[1]
        self.assertNotIn('password', payload)
        self.assertNotIn('password', next(iter(web.SESSIONS.values())))

    def test_unknown_action_is_safe_error(self):
        with self.assertRaises(web.InputError):
            web.dispatch_operation(Mock(), {'action':'drop_database'})


if __name__ == '__main__':
    unittest.main()
