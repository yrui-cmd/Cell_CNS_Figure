import io
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib import request
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import xiaomiao_setup as m

KEY = 'img_live_fixture.TEST_ONLY_NOT_A_REAL_KEY'


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return io.BytesIO(json.dumps(value).encode())


def response(amount=220, **extra):
    return dict(ok=True, available_credits=amount, **extra)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'xiaomiao_api.txt'

    def call(self, **kwargs):
        return m.setup(path=self.path, auto_open=False, **kwargs)

    def test_first_empty_and_auto_open(self):
        with patch.object(m, 'open_editor', return_value='requested') as editor:
            report = m.setup(path=self.path)
        self.assertEqual(self.path.read_text(), 'API_Key=""\n')
        self.assertFalse(report['queried'])
        self.assertEqual(report['status'], 'needs_key')
        editor.assert_called_once_with(self.path)
        self.assertEqual(len(report['features']), 3)

    def test_chat_then_file_and_old_environment(self):
        transport = Transport(response(), response(219))
        with patch.dict(os.environ, {'XIAOMIAO_API_KEY': 'OLD_ACCOUNT'}):
            report = self.call(supplied=' \t' + KEY + '\n', transport=transport)
            self.assertEqual(report['balance'], 220)
            report2 = self.call(transport=transport)
            env = m.child_environment(self.path)
            self.assertEqual(env['XIAOMIAO_API_KEY'], KEY)
            self.assertEqual(os.environ['XIAOMIAO_API_KEY'], 'OLD_ACCOUNT')
        self.assertEqual(report2['balance'], 219)
        for req in transport.requests:
            self.assertEqual(req.full_url, m.BALANCE_URL)
            self.assertEqual(req.get_header('Authorization'), 'Bearer ' + KEY)
        self.assertNotIn(KEY, json.dumps(report))

    def test_saved_whitespace_normalized(self):
        self.path.write_text('API_Key=" \t' + KEY + '\n"\n', encoding='utf-8')
        self.assertEqual(self.call(transport=Transport(response()))['status'], 'ok')
        self.assertEqual(self.path.read_text(), 'API_Key="' + KEY + '"\n')

    def test_trial_invitation_is_accepted_and_redacted(self):
        invitation = 'jexp_TEST_ONLY_TRIAL'
        report = self.call(supplied=' ' + invitation + '\n', transport=Transport(response(1)))
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(self.path.read_text(), 'API_Key="' + invitation + '"\n')
        self.assertNotIn(invitation, json.dumps(report))

    def test_every_call_reads_file_including_chat(self):
        transport = Transport(response(), response())
        with patch.object(m, 'read_key', wraps=m.read_key) as reader:
            self.call(supplied=KEY, transport=transport)
            reader.assert_called_once_with(self.path)
        replacement = 'img_live_new.FILE_CHANGED_BETWEEN_CALLS'
        m.atomic_write(self.path, replacement)
        self.call(transport=transport)
        self.assertEqual(transport.requests[-1].get_header('Authorization'), 'Bearer ' + replacement)
        self.assertEqual(m.child_environment(self.path)['XIAOMIAO_API_KEY'], replacement)

    def test_invalid_input_preserves_file(self):
        m.atomic_write(self.path, KEY)
        for value in ('img_live_a.b c', 'jexp_a b', 'API_Key="x"; print(1)'):
            result = self.call(supplied=value)
            self.assertEqual(result['status'], 'invalid_config')
            self.assertEqual(m.read_key(self.path), KEY)
        with self.path.open('r+', encoding='utf-8') as handle:
            handle.write('API_Key=""; __import__("os").abort()')
            handle.truncate()
        self.assertEqual(self.call()['status'], 'invalid_config')

    def test_atomic_failure_preserves_original(self):
        m.atomic_write(self.path, KEY)
        with patch.object(m.os, 'replace', side_effect=PermissionError):
            report = self.call(supplied='img_live_other.fixture')
        self.assertEqual(report['status'], 'permission')
        self.assertEqual(m.read_key(self.path), KEY)
        self.assertEqual(list(self.path.parent.glob('.xiaomiao-*')), [])

    def test_zero_and_selected_function(self):
        m.atomic_write(self.path, KEY)
        with patch.object(m, 'open_editor', return_value='requested') as editor:
            report = m.setup(path=self.path, transport=Transport(response(0)))
            self.assertEqual(report['balance'], 0)
            editor.assert_called_once()
        self.assertEqual(self.call(transport=Transport(response(16)))['status'], 'ok')
        self.assertEqual(self.call(feature='journal_figure', transport=Transport(response(16)))['status'], 'insufficient')

    def test_missing_and_unknown_permissions(self):
        result = self.call(supplied=KEY, transport=Transport({'ok': True}))
        self.assertEqual(result['status'], 'protocol')
        self.assertIsNone(result['balance'])
        self.assertTrue(all(f['permission'] is None for f in result['features']))
        result = self.call(transport=Transport(response(20, services={'journal_figure': {'enabled': False}})))
        self.assertIs(result['features'][1]['permission'], False)
        self.assertTrue(all(f['executed'] is False for f in result['features']))

    def test_auth_no_fallback_and_no_leak(self):
        t = Transport(HTTPError(m.BALANCE_URL, 401, KEY, {}, io.BytesIO(KEY.encode())))
        result = self.call(supplied=KEY, transport=t)
        self.assertEqual(result['status'], 'auth')
        self.assertEqual(len(t.requests), 1)
        self.assertNotIn(KEY, json.dumps(result))

    def test_rate_limit_retry_after(self):
        t = Transport(HTTPError(m.BALANCE_URL, 429, '', {'Retry-After': '2'}, None), response())
        waits = []
        m.query_balance(KEY, transport=t, sleep=waits.append)
        self.assertEqual(waits, [2])
        t = Transport(HTTPError(m.BALANCE_URL, 429, '', {'Retry-After': '120'}, None))
        with self.assertRaises(m.SetupError) as ctx:
            m.query_balance(KEY, transport=t, sleep=waits.append)
        self.assertEqual(ctx.exception.status, 'rate_limit')
        self.assertEqual(len(t.requests), 1)

    def test_network_bounded(self):
        t = Transport(*[URLError(KEY) for _ in range(3)])
        with self.assertRaises(m.SetupError) as ctx:
            m.query_balance(KEY, transport=t, sleep=lambda x: None)
        self.assertEqual(ctx.exception.status, 'network')
        self.assertEqual(len(t.requests), 3)
        self.assertNotIn(KEY, str(ctx.exception))

    def test_redirect_blocked(self):
        self.assertIsNone(m.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid'))
        result = self.call(supplied=KEY, transport=Transport(HTTPError(m.BALANCE_URL, 302, '', {}, None)))
        self.assertEqual(result['status'], 'redirect')

    def test_local_http_service_and_redirect_does_not_reach_target(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get('Authorization')))
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/should-not-be-requested')
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(response(42)).encode())
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        class LocalTransport:
            route = '/balance'
            def open(self, req, timeout):
                # Fake fixture only: production endpoint remains fixed, no CLI override.
                local = request.Request('http://127.0.0.1:' + str(server.server_port) + self.route,
                                        headers=dict(req.header_items()))
                return request.build_opener(m.NoRedirect()).open(local, timeout=timeout)
        try:
            transport = LocalTransport()
            report = self.call(supplied=KEY, transport=transport)
            self.assertEqual(report['balance'], 42)
            transport.route = '/redirect'
            report = self.call(transport=transport)
            self.assertEqual(report['status'], 'redirect')
            self.assertEqual([v[0] for v in seen], ['/balance', '/redirect'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cli_never_echoes_key_on_config_error(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-B', str(Path(m.__file__)), '--file',
                                 str(self.path), '--stdin-key', '--no-open'],
                                input=KEY + ' internal space', text=True,
                                capture_output=True, encoding='utf-8',
                                env={**os.environ, 'PYTHONUTF8': '1'})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'invalid_config')
        self.assertNotIn(KEY, result.stdout + result.stderr)

    def test_error_classifications(self):
        for code, expected in [(402, 'insufficient'), (403, 'forbidden'), (500, 'service')]:
            count = 3 if code == 500 else 1
            t = Transport(*[HTTPError(m.BALANCE_URL, code, KEY, {}, None) for _ in range(count)])
            with self.assertRaises(m.SetupError) as ctx:
                m.query_balance(KEY, transport=t, sleep=lambda x: None)
            self.assertEqual(ctx.exception.status, expected)

    def test_mac_hide_and_editor_failures(self):
        with patch.object(m, 'platform_name', return_value='macos'), patch.object(m.subprocess, 'run') as run:
            run.return_value.returncode = 0
            m.hide_file(self.path)
            self.assertEqual(run.call_args.args[0], ['chflags', 'hidden', str(self.path)])
        with patch.object(m, 'editor_command', return_value=['missing']), patch.object(m.subprocess, 'Popen', side_effect=OSError):
            self.assertEqual(m.open_editor(self.path), 'failed')

    def test_platform_paths_and_editors(self):
        with patch.object(m, 'platform_name', return_value='windows'), patch.object(m, 'windows_desktop', return_value=Path('D:/Redirected')):
            self.assertEqual(m.config_path(), Path('D:/Redirected/xiaomiao_api.txt'))
            self.assertEqual(m.editor_command(self.path)[0], 'notepad.exe')
        with patch.object(m, 'platform_name', return_value='macos'), patch.object(m.Path, 'home', return_value=Path('/users/test')):
            self.assertEqual(m.config_path(), Path('/users/test/Desktop/xiaomiao_api.txt'))
            self.assertEqual(m.editor_command(self.path)[:2], ['open', '-e'])
        with patch.object(m, 'platform_name', return_value='android'), patch.object(m.Path, 'home', return_value=Path('/termux/home')), patch.object(m.shutil, 'which', return_value='/bin/termux-open'):
            self.assertEqual(m.config_path(), Path('/termux/home/.config/xiaomiao/xiaomiao_api.txt'))
            self.assertIn('--edit', m.editor_command(self.path))
        with patch.object(m, 'platform_name', return_value='android'), patch.object(m.shutil, 'which', return_value=None):
            self.assertEqual(m.open_editor(self.path), 'unavailable')

    @unittest.skipUnless(os.name == 'nt', 'Windows real file attributes')
    def test_windows_hidden_rewrite(self):
        m.atomic_write(self.path, KEY)
        self.assertTrue(m.ctypes.windll.kernel32.GetFileAttributesW(str(self.path)) & 2)
        m.atomic_write(self.path, 'img_live_replaced.fixture')
        self.assertEqual(m.read_key(self.path), 'img_live_replaced.fixture')
        self.assertTrue(m.ctypes.windll.kernel32.GetFileAttributesW(str(self.path)) & 2)


if __name__ == '__main__':
    unittest.main()
