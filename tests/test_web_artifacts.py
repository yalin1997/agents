from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from pi_deepagent.web.artifacts import ArtifactStore, written_path
from pi_deepagent.web.auth import Redactor


class ArtifactTests(unittest.TestCase):
    def test_only_explicit_successful_pi_file_results_are_recognized(self):
        self.assertEqual(written_path('pi_write', 'Wrote 10 bytes to report.md'), 'report.md')
        self.assertEqual(written_path('pi_edit', 'Edited report.md (2 replacement(s))'), 'report.md')
        for name, content in [('pi_read', 'Wrote 10 bytes to report.md'),
                              ('pi_write', 'Error writing report.md: denied'),
                              ('pi_edit', 'old_text was not found'),
                              ('pi_write', 'Wrote 1 bytes to a\nsecond line')]:
            with self.subTest(name=name, content=content):
                self.assertIsNone(written_path(name, content))

    def test_binary_directory_hidden_outside_and_indirect_paths_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); workspace=root/'workspace';workspace.mkdir()
            store=ArtifactStore(root/'private',workspace,Redactor())
            (root/'outside.txt').write_text('outside',encoding='utf-8')
            for name, content in [('binary.bin', b'\xff'), ('null.txt', b'a\0b'),
                                  ('.env', b'secret'), ('ok.txt', b'visible')]:
                (workspace/name).write_bytes(content)
            for path in ('binary.bin','null.txt','.env','.',str(root/'outside.txt'),'missing'):
                with self.subTest(path=path):
                    self.assertIsNone(store.capture(str(uuid4()),str(uuid4()),path))
            # Simulate an indirect filesystem entry without requiring Windows symlink privileges.
            with patch.object(Path,'is_symlink',autospec=True,return_value=True):
                self.assertIsNone(store.capture(str(uuid4()),str(uuid4()),'ok.txt'))
            self.assertFalse((root/'private').exists())

    def test_text_snapshot_redacts_known_secrets_and_retains_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);workspace=root/'workspace';workspace.mkdir()
            redactor=Redactor();redactor.add('known-provider-secret')
            store=ArtifactStore(root/'private',workspace,redactor)
            (workspace/'report.md').write_text('known-provider-secret',encoding='utf-8')
            task_id=str(uuid4());artifact=store.capture(task_id,str(uuid4()),str(workspace/'report.md'))
            self.assertEqual(artifact['name'],'report.md')
            self.assertEqual(store.read(task_id,artifact['id']),b'[REDACTED]')
            self.assertEqual(artifact['size'],len(b'[REDACTED]'))
