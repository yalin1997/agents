import tempfile
import unittest
from pathlib import Path

from pi_deepagent.web.auth import AuthError, AuthService, Redactor

PASSWORD = 'Correct-horse-1999!'


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.path = Path(self.temp.name)
        self.now = 1000.0
        self.redactor = Redactor()
        self.auth = AuthService(self.path, self.redactor, clock=lambda: self.now)
        self.auth.initialize(PASSWORD)

    def tearDown(self):
        self.temp.cleanup()

    def test_password_hash_and_real_validation(self):
        raw = (self.path / 'admin.json').read_text()
        self.assertNotIn(PASSWORD, raw)
        with self.assertRaises(AuthError) as error:
            self.auth.login('admin', 'wrong', 'client')
        self.assertEqual(error.exception.status, 401)
        token, csrf = self.auth.login('admin', PASSWORD, 'client')
        self.assertGreaterEqual(len(token), 43)
        self.assertEqual(self.auth.authenticate(token).role, 'admin')
        self.assertEqual(self.auth.authenticate(token).csrf, csrf)
        self.assertNotIn(token, repr(self.auth.sessions))
        self.assertNotIn(PASSWORD, self.redactor.clean('secret=' + PASSWORD))

    def test_expiration_revocation_and_restart(self):
        token, _ = self.auth.login('admin', PASSWORD, 'client')
        self.now += 8 * 3600
        with self.assertRaises(AuthError): self.auth.authenticate(token)
        token, _ = self.auth.login('admin', PASSWORD, 'client')
        self.auth.logout(token)
        with self.assertRaises(AuthError): self.auth.authenticate(token)
        token, _ = self.auth.login('admin', PASSWORD, 'client')
        restarted = AuthService(self.path, Redactor())
        with self.assertRaises(AuthError): restarted.authenticate(token)

    def test_rate_limit_and_recovery(self):
        for _ in range(5):
            with self.assertRaises(AuthError) as error: self.auth.login('no-user', 'wrong', 'client')
            self.assertEqual(error.exception.status, 401)
        with self.assertRaises(AuthError) as error: self.auth.login('admin', PASSWORD, 'client')
        self.assertEqual(error.exception.status, 429)
        self.now += 61
        self.assertTrue(self.auth.login('admin', PASSWORD, 'client')[0])

    def test_initialization_is_non_overwriting_and_strong(self):
        with self.assertRaises(FileExistsError): self.auth.initialize('replacement-password')
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            with self.assertRaises(ValueError): AuthService(Path(folder), Redactor()).initialize('pi-demo')

    def test_redactor_handles_overlapping_and_split_secrets(self):
        r = Redactor(); r.add('secret-long', 'secret')
        self.assertEqual(r.clean('a secret-long b secret'), 'a [REDACTED] b [REDACTED]')
        self.assertEqual(r.clean('a secr', final=False), 'a ')
        self.assertEqual(r.clean('a secret-long end', final=False), 'a [REDACTED] end')
        self.assertEqual(r.clean('a secr', final=True), 'a secr')

    def test_cli_rejects_workspace_credentials_before_password_prompt(self):
        from unittest.mock import patch
        from pi_deepagent.web.__main__ import main
        with patch('pi_deepagent.web.__main__.getpass.getpass') as prompt:
            with self.assertRaises(SystemExit) as error:
                main(['init-admin','--workspace',str(self.path),'--data-dir',str(self.path/'private')])
            self.assertEqual(error.exception.code,2)
            prompt.assert_not_called()
        self.assertFalse((self.path/'private').exists())

    def test_cli_init_success_and_mismatch_do_not_overwrite(self):
        import contextlib,io
        from unittest.mock import patch
        from pi_deepagent.web.__main__ import main
        workspace=self.path/'workspace';workspace.mkdir()
        data=self.path/'server'
        with contextlib.redirect_stdout(io.StringIO()),patch('pi_deepagent.web.__main__.getpass.getpass',side_effect=[PASSWORD,PASSWORD]):
            self.assertEqual(main(['init-admin','--workspace',str(workspace),'--data-dir',str(data)]),0)
        self.assertNotIn(PASSWORD,(data/'admin.json').read_text())
        with contextlib.redirect_stderr(io.StringIO()),patch('pi_deepagent.web.__main__.getpass.getpass',side_effect=[PASSWORD,'different']):
            with self.assertRaises(SystemExit):main(['init-admin','--workspace',str(workspace),'--data-dir',str(self.path/'other')])
        self.assertFalse((self.path/'other').exists())

    def test_cli_serve_fixed_loopback_and_validates_port(self):
        from unittest.mock import patch
        from pi_deepagent.web.__main__ import main
        import contextlib,io
        workspace=self.path/'workspace';workspace.mkdir()
        with contextlib.redirect_stdout(io.StringIO()),patch('uvicorn.run') as serve:
            self.assertEqual(main(['serve','--workspace',str(workspace),'--data-dir',str(self.path),'--port','8765']),0)
            self.assertEqual(serve.call_args.kwargs['host'],'127.0.0.1')
            self.assertEqual(serve.call_args.kwargs['workers'],1)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):main(['serve','--workspace',str(workspace),'--data-dir',str(self.path),'--port','0'])
