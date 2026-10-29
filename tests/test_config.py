from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from pi_deepagent.config import load_environment


class EnvironmentTests(TestCase):
    def test_both_clis_load_key_and_model_before_building_defaults(self) -> None:
        from pi_deepagent.cli import _build_parser
        from pi_deepagent.a2a.cli import parser

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text(
                'GOOGLE_API_KEY=test-key\nPI_DEEPAGENT_MODEL=google_genai:test-model\n',
                encoding='utf-8',
            )
            for build, args in ((_build_parser, []), (parser, ['serve'])):
                with self.subTest(cli=build.__module__), patch.dict(os.environ, {}, clear=True), patch(
                    'pi_deepagent.config.Path.cwd', return_value=root
                ):
                    self.assertEqual(build().parse_args(args).model, 'google_genai:test-model')
                    self.assertEqual(os.environ['GOOGLE_API_KEY'], 'test-key')

    def test_existing_environment_takes_precedence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('GOOGLE_API_KEY=file-key\n', encoding='utf-8')
            with patch.dict(os.environ, {'GOOGLE_API_KEY': 'existing-key'}, clear=True), patch(
                'pi_deepagent.config.Path.cwd', return_value=root
            ):
                load_environment()
                self.assertEqual(os.environ['GOOGLE_API_KEY'], 'existing-key')

    def test_missing_file_does_not_load_parent_environment(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('GOOGLE_API_KEY=parent-key\n', encoding='utf-8')
            child = root / 'child'
            child.mkdir()
            with patch.dict(os.environ, {}, clear=True), patch(
                'pi_deepagent.config.Path.cwd', return_value=child
            ):
                load_environment()
                self.assertNotIn('GOOGLE_API_KEY', os.environ)
