"""Disposable, authenticated browser-QA server. Never used by the product CLI.

Run from the project root: python -m tests.web_browser_fixture (or runpy).
Uses a temporary workspace/data directory and deterministic external-model stand-in.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pi_deepagent.contracts import AgentEvent
from pi_deepagent.web.app import create_app
from pi_deepagent.web.auth import AuthService, Redactor


class BrowserRunner:
    def __init__(self, prompt, fail, workspace):
        self.prompt, self.fail = prompt, fail
        self.workspace = workspace
        self.task = None

    async def stream(self):
        self.task = asyncio.current_task()
        yield AgentEvent('start', 'fixture', 'run_start')
        await asyncio.sleep(2)
        if self.fail:
            yield AgentEvent('error', 'fixture', 'error', payload={'message': 'fixture failure'})
            return
        if '停止' in self.prompt:
            await asyncio.sleep(60)
        if '成果' in self.prompt:
            from pi_deepagent.tools import create_pi_tools
            yield AgentEvent('tool-start', 'fixture', 'tool_start', {'name':'pi_write'})
            await asyncio.sleep(1)
            result = create_pi_tools(self.workspace)[1]('report.md', '# 驗收報告\n\n這是隔離測試產出的檔案。\n')
            yield AgentEvent('tool-end', 'fixture', 'tool_result', {'name':'pi_write','content':result})
        text = ('隔離驗收報告已產出，請從成果區下載。' if '成果' in self.prompt else
                '瀏覽器驗收完成。這是可控模型的測試回覆，未修改檔案。')
        yield AgentEvent('delta', 'fixture', 'model_delta', payload={'text': text})
        yield AgentEvent('end', 'fixture', 'run_end', payload={'status': 'completed', 'output': text})

    def abort(self):
        if self.task:
            self.task.cancel()


def main():
    import uvicorn
    with TemporaryDirectory(prefix='pi-web-browser-') as directory:
        root = Path(directory)
        workspace = root / 'workspace'; workspace.mkdir()
        data = root / 'data'
        AuthService(data, Redactor()).initialize('browser-test-password-only')
        attempted = set()

        def factory(history, prompt):
            fail = '失敗' in prompt and prompt not in attempted
            attempted.add(prompt)
            return BrowserRunner(prompt, fail, workspace)

        app = create_app(data, workspace, 'test:browser', 'http://127.0.0.1:8767', runner_factory=factory)
        uvicorn.run(app, host='127.0.0.1', port=8767, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
