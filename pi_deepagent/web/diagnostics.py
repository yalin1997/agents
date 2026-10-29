"""Bounded local reports. Provider exception bodies never enter browser state."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import traceback
from uuid import uuid4


class Diagnostics:
    def __init__(self, directory: Path, redactor):
        self.path = directory / 'diagnostics.log'
        self.redactor = redactor
        self.handler = None

    def open(self):
        self.handler = RotatingFileHandler(self.path, maxBytes=2 * 1024 * 1024,
                                          backupCount=3, encoding='utf-8')
        self.handler.setFormatter(logging.Formatter('%(message)s'))

    def close(self):
        if self.handler:
            self.handler.close()
            self.handler = None

    def record(self, task_id, run_id, *, phase, tool=None, code='execution_error',
               message='', exception=None, trace=''):
        # Runtime transports may flatten exception types into runtime_error.
        if str(message or exception or '').startswith('tool_output_persistence_failed:'):
            code = 'tool_output_persistence_failed'
        # Redact before truncating: a truncated credential cannot be matched later.
        clean = lambda value, limit: self.redactor.clean(str(value))[:limit]
        report = {'id': str(uuid4()), 'time': datetime.now(timezone.utc).isoformat(),
                  'task_id': task_id, 'run_id': run_id, 'phase': phase,
                  'tool': clean(tool or '', 120),
                  'code': clean(code or 'execution_error', 120),
                  'message': clean(message or (str(exception) if exception else '執行未正常結束。'), 8000)}
        # Credentials embedded in URLs/headers need filtering even when not in env.
        def scrub(text):
            text = re.sub(r'(?i)(bearer\s+)\S+', r'\1[REDACTED]', text)
            text = re.sub(r'''(?i)((?:api[_-]?key|token|password|secret)["']?\s*[=:]\s*["']?)[^\s,;"'}&]+''', r'\1[REDACTED]', text)
            return re.sub(r'(https?://)[^/\s@]+@', r'\1[REDACTED]@', text)
        report['message'] = scrub(report['message'])
        if exception is not None:
            # Stack frames without locals/source lines (may contain private data).
            trace = '\n'.join(f'{f.filename}:{f.lineno} in {f.name}'
                              for f in traceback.extract_tb(exception.__traceback__))
        local = {**report, 'traceback': scrub(clean(trace, 16000))}
        # Exceptions can echo request contents or unknown credentials. Only local
        # admins with OS access see the redacted raw message; browser gets a hint.
        text = report['message'].lower()
        status = re.search(r'(?:error code[:\s]*|http\s*|status(?:_code)?[=: ]+)([45]\d\d)\b', text)
        if code == 'tool_output_persistence_failed':
            hint = '工具已執行，但大型輸出保存失敗；本次執行已停止，未自動重跑工具。請檢查工作區的寫入權限與磁碟空間，依錯誤編號查閱日誌。'
        elif 'exceed_context_size_error' in text or 'exceeds the available context size' in text or 'local_context_budget_exceeded' in text:
            hint = '模型上下文容量不足；請縮小本輪需求或提高模型服務的 context size。詳情請依錯誤編號查閱日誌。'
        elif status and status[1] in ('401', '403'):
            hint = '模型服務拒絕驗證；請檢查服務端金鑰與模型存取權限。'
        elif status and status[1] == '429':
            hint = '模型服務達到用量或速率限制；請確認配額後再重試。'
        elif status:
            hint = f'模型服務回傳 HTTP {status[1]}；請依錯誤編號查閱日誌，檢查請求格式與服務相容性。'
        elif 'connect' in text or 'connection' in text:
            hint = '無法連線至模型服務；請檢查服務是否啟動及端點設定。'
        elif code == 'timeout':
            hint = '執行逾時；請檢查服務回應與已執行的操作。'
        elif code == 'run_limit':
            hint = '執行超過工作台輸出或活動記錄上限。'
        else:
            hint = '請依錯誤編號查閱本機日誌中的詳細原因。'
        report['message'] = hint
        report['log_saved'] = False
        if self.handler:
            try:
                # Direct write propagates I/O errors so the UI can report log failure.
                self.handler.acquire()
                try:
                    record = logging.LogRecord('diagnostics', logging.ERROR, '', 0,
                                               json.dumps(local, ensure_ascii=False), (), None)
                    if self.handler.shouldRollover(record):
                        self.handler.doRollover()
                    self.handler.stream.write(self.handler.format(record) + '\n')
                    self.handler.flush()
                finally:
                    self.handler.release()
                report['log_saved'] = True
            except OSError:
                pass
        return report
