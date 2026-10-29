import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from pi_deepagent.web.auth import Redactor
from pi_deepagent.web.diagnostics import Diagnostics


class DiagnosticsTests(TestCase):
    def test_categories_do_not_echo_private_response_and_url_credentials_are_redacted(self):
        with TemporaryDirectory(dir=Path.cwd()) as temporary:
            log = Diagnostics(Path(temporary), Redactor())
            log.open()
            try:
                for message, code, expected in [
                    ('tool_output_persistence_failed: tool=pi_bash call=failed; private', 'runtime_error', '大型輸出保存失敗'),
                    ('Error code: 400 exceed_context_size_error private', 'runtime_error', '上下文容量不足'),
                    ('local_context_budget_exceeded private', 'ValueError', '上下文容量不足'),
                    ('Error code: 401 private', 'runtime_error', '驗證'),
                    ('HTTP 403 private', 'runtime_error', '驗證'),
                    ('HTTP 429 private', 'runtime_error', '配額'),
                    ('HTTP 500 private', 'runtime_error', 'HTTP 500'),
                    ('Connection refused private', 'runtime_error', '無法連線'),
                    ('private', 'timeout', '逾時'),
                    ('private', 'run_limit', '上限'),
                ]:
                    with self.subTest(code=code, message=message):
                        report = log.record('task', 'run', phase='model', code=code, message=message)
                        self.assertIn(expected, report['message'])
                        self.assertNotIn('private', report['message'])
                        if message.startswith('tool_output_persistence_failed:'):
                            self.assertEqual(report['code'], 'tool_output_persistence_failed')
                log.record('task', 'run', phase='model', message='https://admin:unknown-pass@example.com "api_key": "unknown-key"')
                text = log.path.read_text(encoding='utf-8')
                self.assertNotIn('unknown-pass', text)
                self.assertNotIn('unknown-key', text)
            finally:
                log.close()

    def test_redacted_bounded_report_and_trace_without_source_or_locals(self):
        with TemporaryDirectory(dir=Path.cwd()) as temporary:
            redactor = Redactor()
            redactor.add('known-sensitive-value')
            log = Diagnostics(Path(temporary), redactor)
            log.open()
            try:
                try:
                    raise ValueError('known-sensitive-value Bearer unknown-credential token=another-secret')
                except ValueError as exc:
                    report = log.record('task', 'run', phase='model', exception=exc)
                self.assertTrue(report['log_saved'])
                self.assertNotIn('traceback', report)
                saved = json.loads(log.path.read_text(encoding='utf-8'))
                self.assertEqual(saved['id'], report['id'])
                self.assertIn('test_web_diagnostics.py:', saved['traceback'])
                for secret in ('known-sensitive-value', 'unknown-credential', 'another-secret', 'raise ValueError'):
                    self.assertNotIn(secret, log.path.read_text(encoding='utf-8'))
                log.handler.maxBytes = 1
                log.record('task', 'run', phase='tool', message='second error')
                self.assertTrue(log.path.with_suffix('.log.1').exists())
                self.assertEqual(json.loads(log.path.read_text(encoding='utf-8'))['message'], 'second error')
            finally:
                log.close()

    def test_log_write_failure_preserves_report_and_never_claims_saved(self):
        with TemporaryDirectory(dir=Path.cwd()) as temporary:
            log = Diagnostics(Path(temporary), Redactor())
            log.open()
            try:
                with patch.object(log.handler, 'shouldRollover', side_effect=OSError('disk full')):
                    report = log.record('task', 'run', phase='model', message='provider offline')
                self.assertFalse(report['log_saved'])
                self.assertIn('本機日誌', report['message'])
            finally:
                log.close()
