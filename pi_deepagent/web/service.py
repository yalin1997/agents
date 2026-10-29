"""SQLite-backed task service; HTTP transports never own run lifetimes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
from typing import Any
from uuid import UUID, uuid4

from .artifacts import ArtifactStore, written_path
from .diagnostics import Diagnostics


class WebError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class WorkspaceService:
    def __init__(self, database: Path, workspace: Path, runner_factory, redactor,
                 timeout: float = 600, max_output: int = 1048576) -> None:
        self.database = Path(database).resolve()
        self.workspace = Path(workspace).resolve()
        self.runner_factory = runner_factory
        self.redactor = redactor
        self.timeout = timeout
        self.max_output = max_output
        self.max_events = 1000
        self.artifacts = ArtifactStore(self.database.parent / 'artifacts', self.workspace, redactor)
        self.diagnostics = Diagnostics(self.database.parent, redactor)
        self._db: sqlite3.Connection | None = None
        self._lock_file = None
        self._worker: asyncio.Task | None = None
        self._active_id: str | None = None
        self._runner = None
        self._abort_sent = False
        self._closing = True

    async def startup(self) -> None:
        if self._db is not None:
            return
        if not self.workspace.is_dir():
            raise WebError(400, "工作目錄不存在。")
        if self.database.parent == self.workspace or self.database.parent.is_relative_to(self.workspace):
            raise WebError(400, "服務資料不得放在工作目錄內。")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        lock = open(self.database.parent / ".web.lock", "a+b")
        try:
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                if lock.read(1) == b"":
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise WebError(409, "此服務資料目錄已在使用。") from None
        self._lock_file = lock
        try:
            self._db = sqlite3.connect(self.database)
            self._db.row_factory = sqlite3.Row
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, workspace TEXT NOT NULL,
                    title TEXT NOT NULL, status TEXT NOT NULL, run_id TEXT,
                    revision INTEGER NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
            """)
            if 'archived' not in {r['name'] for r in self._db.execute('PRAGMA table_info(tasks)')}:
                self._db.execute('ALTER TABLE tasks ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS retired_requests (request_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS purge_jobs (
                    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    workspace TEXT NOT NULL, task_ids TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0);
            """)
            with self._db:
                rows = self._db.execute("SELECT * FROM tasks WHERE status IN ('running','stopping') AND owner='admin' AND workspace=?", (str(self.workspace),)).fetchall()
                for row in rows:
                    task = self._decode(row)
                    task["status"] = "interrupted"
                    task["error"] = "服務已重啟，先前執行已中斷；不會自動重跑。"
                    self._save(task)
            self.diagnostics.open()
            self._closing = False
        except BaseException:
            self.diagnostics.close()
            if self._db is not None:
                self._db.close()
                self._db = None
            self._unlock()
            raise

    def _unlock(self) -> None:
        lock, self._lock_file = self._lock_file, None
        if lock is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()

    async def shutdown(self) -> None:
        self._closing = True
        worker = self._worker
        if worker is not None and not worker.done():
            if self._active_id is not None:
                task = self.get_task(self._active_id)
                task["status"] = "stopping"
                with self._connection():
                    self._save(task)
            self._abort_runner()
            if self._runner is None:
                worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        if self._db is not None:
            self._db.close()
            self._db = None
        self._worker = None
        self._active_id = None
        self._unlock()
        self.diagnostics.close()

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            raise WebError(503, "服務尚未啟動。")
        return self._db

    @staticmethod
    def _uuid(value: Any) -> str:
        try:
            if not isinstance(value, str):
                raise ValueError
            UUID(value)
            return value
        except (ValueError, TypeError, AttributeError):
            raise WebError(400, "request_id 必須是 UUID。") from None

    def _decode(self, row: sqlite3.Row) -> dict:
        payload = json.loads(row["payload"])
        return {"id": row["id"], "title": row["title"], "status": row["status"],
                "run_id": row["run_id"], "revision": row["revision"], **payload, "archived": bool(row["archived"])}

    def get_task(self, task_id: str) -> dict:
        row = self._connection().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise WebError(404, "找不到任務。")
        if row["owner"] != "admin" or row["workspace"] != str(self.workspace):
            raise WebError(403, "無權存取此任務。")
        return self._sanitize(self._decode(row))

    def list_tasks(self, archived: bool = False) -> list[dict]:
        rows = self._connection().execute(
            "SELECT id,title,status,revision,archived FROM tasks WHERE owner='admin' AND workspace=? AND archived=? ORDER BY updated_at DESC,id",
            (str(self.workspace), int(archived))).fetchall()
        return [{**dict(row), "title": self.redactor.clean(row["title"])} for row in rows]

    def _sanitize(self, task: dict) -> dict:
        task.setdefault("archived", False)
        task.setdefault("artifacts", [])
        task.setdefault("diagnostic", None)
        if task["diagnostic"]:
            for key in ('message', 'tool', 'code'):
                task['diagnostic'][key] = self.redactor.clean(task['diagnostic'][key])
        task["title"] = self.redactor.clean(task["title"])
        task["output"] = self.redactor.clean(task["output"])
        if task["error"]:
            task["error"] = self.redactor.clean(task["error"])
        for message in task["messages"]:
            message["text"] = self.redactor.clean(message["text"])
            for event in message.get("tools", []):
                self._sanitize_tool(event)
        for event in task["events"]:
            self._sanitize_tool(event)
        for artifact in task["artifacts"]:
            artifact["name"] = self.redactor.clean(artifact["name"])
        return task

    def set_archived(self, task_id: str, archived: bool, request_id: str) -> dict:
        with self._connection():
            task = self.get_task(task_id)
            prior, fingerprint = self._prior(request_id, 'archive' if archived else 'restore', task_id, {})
            if prior is not None:
                return task
            if task['status'] in {'running', 'stopping'} or task_id == self._active_id:
                raise WebError(409, '請先停止執行，再變更封存狀態。')
            task['archived'] = archived
            self._save(task)
            self._record(request_id, fingerprint, task)
            return task

    def purge(self, task_ids: list[str], request_id: str) -> dict:
        self._uuid(request_id)
        if not isinstance(task_ids, list) or not task_ids or any(not isinstance(i, str) for i in task_ids):
            raise WebError(400, '請選取要永久刪除的對話。')
        ids = sorted({self._uuid(i) for i in task_ids})
        fingerprint = self._fingerprint('purge', str(self.workspace), {'task_ids': ids})
        db = self._connection()
        job = db.execute('SELECT * FROM purge_jobs WHERE request_id=?', (request_id,)).fetchone()
        if job:
            if job['workspace'] != str(self.workspace):
                raise WebError(403, '無權存取此操作。')
            if job['fingerprint'] != fingerprint:
                raise WebError(409, 'request_id 已用於不同請求。')
        else:
            self._prior(request_id, 'purge', str(self.workspace), {'task_ids': ids})
            # Validate every resource before making any changes.
            for task_id in ids:
                task = self.get_task(task_id)
                if not task['archived'] or task['status'] in {'running', 'stopping'} or task_id == self._active_id:
                    raise WebError(409, '只能永久刪除未執行的封存對話。')
            with db:
                db.execute('INSERT INTO purge_jobs VALUES (?,?,?,?,0)',
                           (request_id, fingerprint, str(self.workspace), json.dumps(ids)))
                # Legacy request snapshots include full conversations. Remove them too,
                # retaining only opaque request IDs so delayed creates cannot resurrect data.
                for row in db.execute('SELECT request_id,result FROM requests').fetchall():
                    result = json.loads(row['result'])
                    if result.get('id') in ids:
                        db.execute('INSERT OR IGNORE INTO retired_requests VALUES (?)', (row['request_id'],))
                        db.execute('DELETE FROM requests WHERE request_id=?', (row['request_id'],))
                db.executemany('DELETE FROM tasks WHERE id=?', [(i,) for i in ids])
        if not job or not job['complete']:
            try:
                root = self.artifacts.directory.resolve()
                for task_id in ids:
                    target = root / str(UUID(task_id))
                    if target.is_symlink() or target.is_junction() or not target.resolve().is_relative_to(root):
                        raise OSError('unsafe artifact directory')
                    if target.exists():
                        shutil.rmtree(target)
            except OSError:
                raise WebError(503, 'DB 紀錄已刪除，但成果快照清理未完成；請重試原操作。') from None
            with db:
                db.execute('UPDATE purge_jobs SET complete=1 WHERE request_id=?', (request_id,))
        return {'deleted_ids': ids}

    def _sanitize_tool(self, event: dict) -> None:
        event['text'] = self.redactor.clean(event.get('text', ''))
        for key in ('name', 'call_id', 'input', 'content'):
            if key in event:
                value = event[key]
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
                text = self.redactor.clean(text)
                if len(text) > 32768:
                    text = text[:32768] + '\n…［內容過長，已截斷］'
                    event[key + '_truncated'] = True
                event[key] = text

    def artifact(self, task_id: str, artifact_id: str) -> tuple[str, bytes]:
        task = self.get_task(task_id)
        item = next((a for a in task["artifacts"] if a["id"] == artifact_id), None)
        if item is None:
            raise WebError(404, "找不到成果。")
        try:
            content = self.artifacts.read(task_id, artifact_id)
        except (OSError, ValueError):
            raise WebError(404, "成果檔案已無法取得。") from None
        return item["name"], content

    def _save(self, task: dict) -> None:
        task["revision"] += 1
        self._sanitize(task)
        payload = {k: task[k] for k in ("messages", "events", "output", "error", "artifacts", "diagnostic")}
        self._connection().execute("""INSERT INTO tasks (id,owner,workspace,title,status,run_id,revision,updated_at,payload,archived) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title,status=excluded.status,
            run_id=excluded.run_id,revision=excluded.revision,updated_at=excluded.updated_at,payload=excluded.payload,archived=excluded.archived""",
            (task["id"], "admin", str(self.workspace), task["title"], task["status"], task["run_id"],
             task["revision"], datetime.now(timezone.utc).isoformat(), json.dumps(payload, ensure_ascii=False), int(task["archived"])))

    @staticmethod
    def _fingerprint(method: str, resource: str, payload: dict) -> str:
        text = json.dumps([method, resource, payload], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _prior(self, request_id: str, method: str, resource: str, payload: dict) -> tuple[dict | None, str]:
        self._uuid(request_id)
        fingerprint = self._fingerprint(method, resource, payload)
        if self._connection().execute('SELECT 1 FROM retired_requests WHERE request_id=?', (request_id,)).fetchone():
            raise WebError(409, '此請求所屬對話已永久刪除，請建立新請求。')
        if self._connection().execute('SELECT 1 FROM purge_jobs WHERE request_id=?', (request_id,)).fetchone():
            raise WebError(409, 'request_id 已用於不同請求。')
        row = self._connection().execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if row is not None:
            if row["fingerprint"] != fingerprint:
                raise WebError(409, "request_id 已用於不同請求。")
            return self._sanitize(json.loads(row["result"])), fingerprint
        return None, fingerprint

    def _record(self, request_id: str, fingerprint: str, result: dict) -> None:
        self._connection().execute("INSERT INTO requests VALUES (?,?,?)",
                                  (request_id, fingerprint, json.dumps(result, ensure_ascii=False)))

    def create(self, request_id: str) -> dict:
        with self._connection():
            prior, fingerprint = self._prior(request_id, "create", "tasks", {})
            if prior is not None:
                self.get_task(prior["id"])
                return prior
            if self._closing:
                raise WebError(503, "服務正在停止。")
            task = {"id": str(uuid4()), "title": "新任務", "status": "fresh", "run_id": None,
                    "revision": 0, "messages": [], "events": [], "output": "", "error": None}
            self._save(task)
            self._record(request_id, fingerprint, task)
            return task

    async def message(self, task_id: str, text: str, request_id: str) -> dict:
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise WebError(400, "請輸入 1 至 32000 字元的需求。")
        return self._accept(task_id, text, request_id, retry=False)

    async def retry(self, task_id: str, expected_run_id: str, request_id: str) -> dict:
        return self._accept(task_id, None, request_id, retry=True, expected_run_id=expected_run_id)

    def _accept(self, task_id: str, text: str | None, request_id: str, *, retry: bool,
                expected_run_id: str | None = None) -> dict:
        with self._connection():
            task = self.get_task(task_id)
            if task['archived']:
                raise WebError(409, '封存對話為唯讀，請先還原。')
            payload = {"expected_run_id": expected_run_id} if retry else {"text": text}
            prior, fingerprint = self._prior(request_id, "retry" if retry else "message", task_id, payload)
            if prior is not None:
                return prior
            if self._closing:
                raise WebError(503, "服務正在停止。")
            if self._worker is not None and not self._worker.done():
                raise WebError(409, "已有任務執行中。")
            if retry:
                if task["status"] not in {"failed", "interrupted"} or task["run_id"] != expected_run_id:
                    raise WebError(409, "任務狀態或執行已變更，請重新整理。")
                index = next((i for i in range(len(task["messages"])-1, -1, -1)
                              if task["messages"][i]["role"] == "user"), None)
                if index is None:
                    raise WebError(409, "沒有可重試的需求。")
                text = task["messages"][index]["text"]
                source = task["messages"][:index]
            else:
                if task["status"] in {"running", "stopping"}:
                    raise WebError(409, "任務執行中。")
                source = task["messages"]
            history = [{"role": m["role"], "text": m["text"]} for m in source
                       if m["role"] == "user" or (m.get("complete", False) and m["text"])]
            run_id = str(uuid4())
            if not retry:
                task["messages"].append({"role": "user", "text": self.redactor.clean(text),
                                         "run_id": run_id, "complete": True})
                if task["status"] == "fresh":
                    task["title"] = self.redactor.clean(text)[:48]
            task.update(status="running", run_id=run_id, output="", error=None, events=[], diagnostic=None)
            self._save(task)
            self._record(request_id, fingerprint, task)
        self._active_id = task_id
        self._abort_sent = False
        self._worker = asyncio.create_task(self._work(task_id, run_id, history, text))
        return task

    async def stop(self, task_id: str, expected_run_id: str, request_id: str) -> dict:
        with self._connection():
            task = self.get_task(task_id)
            prior, fingerprint = self._prior(request_id, "stop", task_id, {"expected_run_id": expected_run_id})
            if prior is not None:
                return prior
            if task["run_id"] != expected_run_id or task["status"] not in {"running", "stopping"}:
                raise WebError(409, "任務狀態或執行已變更，請重新整理。")
            task["status"] = "stopping"
            self._save(task)
            self._record(request_id, fingerprint, task)
        self._abort_runner()
        return task

    def _abort_runner(self) -> None:
        if self._runner is not None and not self._abort_sent:
            self._abort_sent = True
            try:
                self._runner.abort()
            except Exception:
                # A faulty provider cannot leak raw details through control APIs.
                if self._worker is not None:
                    self._worker.cancel()

    def _activity(self, task: dict, text: str, kind: str = "status") -> None:
        if len(task["events"]) >= self.max_events:
            raise _RunLimit("活動記錄超過上限，執行已停止。")
        task["events"].append({"sequence": len(task["events"]), "text": self.redactor.clean(text), "kind": kind})

    async def _work(self, task_id: str, run_id: str, history: list[dict], prompt: str) -> None:
        raw_output = ""
        failure = None
        terminal = None
        iterator = None
        written = set()
        diagnostic = None
        phase, last_tool = 'initialization', None

        def diagnose(**details):
            return self.diagnostics.record(task_id, run_id, phase=phase, tool=last_tool, **details)
        try:
            if self.get_task(task_id)["status"] == "stopping":
                terminal = "stopped"
            else:
                self._runner = self.runner_factory(history, prompt)
                phase = 'model'
                iterator = self._runner.stream()
                async with asyncio.timeout(self.timeout):
                    async for event in iterator:
                        task = self.get_task(task_id)
                        if task["run_id"] != run_id:
                            break
                        kind, payload = event.kind, event.payload
                        if kind == "model_delta":
                            text = payload.get("text", "")
                            if isinstance(text, str):
                                raw_output += text
                                if len(raw_output) > self.max_output:
                                    raise _RunLimit("回覆超過上限，執行已停止。")
                        elif kind == "error":
                            failure = "模型或工具執行失敗，請檢查服務設定後重試。"
                            diagnostic = diagnose(code=payload.get('error_code'), message=payload.get('message', ''),
                                                  trace=getattr(self._runner, 'error_traceback', ''))
                            if diagnostic['code'] == 'tool_output_persistence_failed':
                                failure = diagnostic['message']
                        elif kind == "run_end":
                            value = payload.get("status")
                            if value == "completed":
                                terminal = "completed"
                            elif value == "aborted":
                                terminal = "stopped"
                            else:
                                failure = "執行未正常完成，請重試。"
                            if not raw_output and isinstance(payload.get("output"), str):
                                raw_output = payload["output"]
                                if len(raw_output) > self.max_output:
                                    raise _RunLimit("回覆超過上限，執行已停止。")
                        elif kind in {"run_start", "tool_start", "tool_result"}:
                            if kind == "run_start":
                                label = "開始執行"
                            else:
                                tool = payload.get("tool_call", {})
                                name = payload.get("name") or (tool.get("name") if isinstance(tool, dict) else None)
                                last_tool = str(name or '工具')
                                phase = 'tool' if kind == 'tool_start' else 'after_tool'
                                label = ("使用工具：" if kind == "tool_start" else "工具已回傳：") + self.redactor.clean(str(name or "工具"))[:120]
                            self._activity(task, label, kind)
                            if kind != 'run_start':
                                detail = task['events'][-1]
                                detail.update(run_id=run_id, name=str(name or '工具'))
                                call_id = payload.get('tool_call_id') or (tool.get('call_id') if isinstance(tool, dict) else None)
                                if call_id is not None:
                                    detail['call_id'] = str(call_id)
                                if kind == 'tool_start':
                                    detail['input'] = payload.get('input', tool.get('arguments', {}) if isinstance(tool, dict) else {})
                                elif 'content' in payload:
                                    detail['content'] = payload['content']
                                    detail['is_error'] = bool(payload.get('is_error') or payload.get('status') == 'error')
                                self._sanitize_tool(detail)
                            if kind == "tool_result":
                                content = payload.get("content")
                                path = written_path(str(name or ''), content) if isinstance(content, str) else None
                                if path and len(written) < self.artifacts.MAX_FILES:
                                    written.add(path)
                        task["output"] = self.redactor.clean(raw_output, final=False)
                        self._update_assistant(task, run_id, False)
                        with self._connection():
                            self._save(task)
        except TimeoutError:
            failure = "執行逾時，已停止；請確認已發生的操作後重試。"
            diagnostic = diagnose(code='timeout', message=failure)
            self._abort_runner()
        except _RunLimit as exc:
            failure = str(exc)
            diagnostic = diagnose(code='run_limit', message=failure)
            self._abort_runner()
        except asyncio.CancelledError:
            task = self.get_task(task_id)
            if task["status"] == "stopping" or self._closing:
                terminal = "stopped"
            else:
                failure = "執行遭中斷，請確認狀態後重試。"
        except Exception as exc:
            failure = "執行失敗，請檢查模型服務後重試。"
            diagnostic = diagnose(code=type(exc).__name__, exception=exc)
            if diagnostic['code'] == 'tool_output_persistence_failed':
                failure = diagnostic['message']
        finally:
            if iterator is not None:
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    try:
                        await close()
                    except asyncio.CancelledError:
                        if self.get_task(task_id)["status"] == "stopping" or self._closing:
                            terminal = "stopped"
                        else:
                            failure = failure or "執行清理遭中斷，請重新啟動服務。"
                    except Exception as exc:
                        failure = failure or "執行清理失敗，請重新啟動服務。"
                        cleanup = self.diagnostics.record(task_id, run_id, phase='cleanup', tool=last_tool,
                                                          code=type(exc).__name__, exception=exc)
                        diagnostic = diagnostic or cleanup
            try:
                task = self.get_task(task_id)
                if task["run_id"] == run_id and task["status"] in {"running", "stopping"}:
                    task["status"] = "failed" if failure or terminal is None else terminal
                    task["error"] = failure or ("執行未正常結束，請重試。" if terminal is None else None)
                    if task['status'] == 'failed':
                        diagnostic = diagnostic or diagnose(code='incomplete_run', message=task['error'])
                        task['diagnostic'] = diagnostic
                    task["output"] = self.redactor.clean(raw_output)[:self.max_output]
                    self._update_assistant(task, run_id, task["status"] == "completed")
                    if task["status"] == "completed":
                        for path in sorted(written):
                            artifact = self.artifacts.capture(task_id, run_id, path)
                            if artifact:
                                if len(task["artifacts"]) >= self.artifacts.MAX_FILES and not any(a["name"] == artifact["name"] for a in task["artifacts"]):
                                    continue
                                task["artifacts"] = [a for a in task["artifacts"] if a["name"] != artifact["name"]]
                                task["artifacts"].append(artifact)
                    if len(task["events"]) < self.max_events:
                        self._activity(task, {"completed":"已完成", "stopped":"已停止；先前操作不會自動復原。", "failed":"執行失敗"}[task["status"]])
                    with self._connection():
                        self._save(task)
            finally:
                self._runner = None
                self._active_id = None
                self._worker = None

    @staticmethod
    def _update_assistant(task: dict, run_id: str, complete: bool) -> None:
        tools = [e for e in task["events"] if e.get("kind") in {"tool_start", "tool_result"}]
        if task["messages"] and task["messages"][-1]["role"] == "assistant" and task["messages"][-1]["run_id"] == run_id:
            task["messages"][-1].update(text=task["output"], complete=complete, tools=tools)
        elif task["output"] or tools:
            task["messages"].append({"role": "assistant", "text": task["output"], "run_id": run_id, "complete": complete, "tools": tools})


class _RunLimit(Exception):
    pass
