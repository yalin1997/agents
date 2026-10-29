from __future__ import annotations

import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from pi_deepagent.contracts import AgentEvent
from pi_deepagent.web.service import WebError, WorkspaceService
from pi_deepagent.web.auth import Redactor


def rid():
    return str(uuid4())


def event(kind, **payload):
    return AgentEvent(event_id=rid(), run_id="runtime-owned", kind=kind, payload=payload)


class ControlledRunner:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.started = asyncio.Event()
        self.cleaned = asyncio.Event()
        self.abort_called = False
        self.abort_count = 0
        self.hold_cleanup = None

    async def stream(self):
        self.started.set()
        try:
            while True:
                value = await self.queue.get()
                if value is None:
                    return
                if isinstance(value, BaseException):
                    raise value
                yield value
        finally:
            if self.hold_cleanup is not None:
                await self.hold_cleanup.wait()
            self.cleaned.set()

    def abort(self):
        self.abort_called = True
        self.abort_count += 1
        self.queue.put_nowait(event("run_end", status="aborted", output=""))
        self.queue.put_nowait(None)

    def complete(self, text="answer"):
        self.queue.put_nowait(event("model_delta", text=text))
        self.queue.put_nowait(event("run_end", status="completed", output=text))
        self.queue.put_nowait(None)


class ServiceTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.calls = []
        self.runners = []
        def factory(history, prompt):
            runner = ControlledRunner()
            self.calls.append((history, prompt))
            self.runners.append(runner)
            return runner
        self.factory = factory
        self.service = WorkspaceService(self.root / "data" / "web.sqlite", self.workspace,
                                        factory, Redactor())
        await self.service.startup()

    async def asyncTearDown(self):
        for runner in self.runners:
            if runner.hold_cleanup is not None:
                runner.hold_cleanup.set()
        await self.service.shutdown()
        self.tmp.cleanup()

    async def settle(self, task_id, statuses=("completed", "failed", "stopped")):
        async with asyncio.timeout(2):
            while True:
                task = self.service.get_task(task_id)
                if task["status"] in statuses:
                    return task
                await asyncio.sleep(0)

    async def start(self, text="hello"):
        task = self.service.create(rid())
        before = len(self.runners)
        result = await self.service.message(task["id"], text, rid())
        async with asyncio.timeout(2):
            while len(self.runners) == before:
                await asyncio.sleep(0)
            await self.runners[-1].started.wait()
        return result, self.runners[-1]

    async def test_persistence_failure_is_explicit_and_does_not_start_another_run(self):
        task, runner = await self.start()
        runner.queue.put_nowait(event('error', error_code='runtime_error',
                                    message='tool_output_persistence_failed: tool=pi_bash call=failed; private'))
        runner.queue.put_nowait(None)
        saved = await self.settle(task['id'])
        self.assertEqual(saved['status'], 'failed')
        self.assertIn('大型輸出保存失敗', saved['error'])
        self.assertIn('未自動重跑工具', saved['error'])
        self.assertEqual(saved['diagnostic']['code'], 'tool_output_persistence_failed')
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('private', json.dumps(saved, ensure_ascii=False))

    async def test_idempotency_and_single_run_slot(self):
        create_id = rid()
        task = self.service.create(create_id)
        self.assertEqual(task, self.service.create(create_id))
        request = rid()
        accepted = await self.service.message(task["id"], "hello", request)
        self.assertEqual(accepted, await self.service.message(task["id"], "hello", request))
        with self.assertRaises(WebError) as wrong:
            await self.service.message(task["id"], "different", request)
        self.assertEqual(wrong.exception.status, 409)
        other = self.service.create(rid())
        with self.assertRaises(WebError) as busy:
            await self.service.message(other["id"], "hello", rid())
        self.assertEqual(busy.exception.status, 409)
        self.assertEqual(len(self.service.get_task(task["id"])["messages"]), 1)

    async def test_failure_report_matches_log_persists_and_resets_on_retry(self):
        self.service.redactor.add('provider-secret')
        task, runner = await self.start()
        runner.queue.put_nowait(event('tool_start', name='pi_bash', input={'command':'private command'}))
        runner.queue.put_nowait(event('tool_result', name='pi_bash', content='private output'))
        runner.queue.put_nowait(event('error', error_code='model_error', message='HTTP 400 provider-secret invalid tool_call_id'))
        runner.queue.put_nowait(None)
        failed = await self.settle(task['id'])
        d = failed['diagnostic']
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(d['phase'], 'after_tool')
        self.assertEqual(d['tool'], 'pi_bash')
        self.assertEqual(d['run_id'], failed['run_id'])
        self.assertIn('HTTP 400', d['message'])
        self.assertNotIn('invalid tool_call_id', d['message'])
        path = self.root / 'data' / 'diagnostics.log'
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['id'], d['id'])
        self.assertEqual(saved['task_id'], task['id'])
        self.assertEqual(saved['message'], 'HTTP 400 [REDACTED] invalid tool_call_id')
        for private in ('provider-secret', 'private command', 'private output'):
            self.assertNotIn(private, path.read_text(encoding='utf-8'))
        await self.service.shutdown()
        await self.service.startup()
        self.assertEqual(self.service.get_task(task['id'])['diagnostic'], d)
        retry = await self.service.retry(task['id'], failed['run_id'], rid())
        self.assertIsNone(retry['diagnostic'])
        await asyncio.sleep(0)
        self.runners[-1].complete()
        self.assertIsNone((await self.settle(task['id']))['diagnostic'])

    async def test_runner_exception_keeps_trace_in_local_log_only(self):
        task, runner = await self.start()
        runner.queue.put_nowait(ValueError('unexpected provider failure'))
        failed = await self.settle(task['id'])
        self.assertEqual(failed['diagnostic']['code'], 'ValueError')
        self.assertIn('本機日誌', failed['diagnostic']['message'])
        self.assertNotIn('traceback', failed['diagnostic'])
        log = json.loads((self.root / 'data' / 'diagnostics.log').read_text(encoding='utf-8'))
        self.assertEqual(log['message'], 'unexpected provider failure')
        self.assertIn('test_web_service.py:', log['traceback'])

    async def test_tool_history_stays_with_each_assistant_turn(self):
        task, runner = await self.start()
        runner.queue.put_nowait(event("tool_start", name="pi_read"))
        runner.queue.put_nowait(event("tool_result", name="pi_read", content="private tool output"))
        runner.complete("first answer")
        done = await self.settle(task["id"])
        assistant = done["messages"][-1]
        self.assertEqual([e["kind"] for e in assistant["tools"]], ["tool_start", "tool_result"])
        self.assertEqual(assistant["tools"][1]["content"], "private tool output")
        await self.service.message(task["id"], "second", rid())
        while len(self.runners) < 2:
            await asyncio.sleep(0)
        self.runners[-1].complete("second answer")
        done = await self.settle(task["id"])
        self.assertEqual(done["messages"][1]["tools"], assistant["tools"])
        self.assertEqual(done["messages"][-1].get("tools", []), [])

    async def test_only_completed_written_files_become_immutable_artifacts(self):
        task, runner = await self.start()
        report = self.workspace / "report.md"
        report.write_text("# Deliverable", encoding="utf-8")
        runner.queue.put_nowait(event("tool_result", name="pi_write", content="Wrote 13 bytes to report.md"))
        runner.complete("See the report.")
        done = await self.settle(task["id"])
        self.assertEqual(len(done["artifacts"]), 1)
        artifact = done["artifacts"][0]
        self.assertEqual(artifact["name"], "report.md")
        report.write_text("later change", encoding="utf-8")
        name, content = self.service.artifact(task["id"], artifact["id"])
        self.assertEqual((name, content), ("report.md", b"# Deliverable"))
        other = self.service.create(rid())
        with self.assertRaises(WebError) as error:
            self.service.artifact(other["id"], artifact["id"])
        self.assertEqual(error.exception.status, 404)
        await self.service.shutdown(); await self.service.startup()
        self.assertEqual(self.service.artifact(task["id"], artifact["id"])[1], b"# Deliverable")

    async def test_text_only_failed_and_unsafe_files_are_not_artifacts(self):
        task, runner = await self.start()
        (self.root / "outside.txt").write_text("outside", encoding="utf-8")
        (self.workspace / ".env").write_text("secret", encoding="utf-8")
        (self.workspace / "large.txt").write_text("x" * (1048576 + 1), encoding="utf-8")
        for path in ("../outside.txt", ".env", "missing.txt", "large.txt"):
            runner.queue.put_nowait(event("tool_result", name="pi_write", content=f"Wrote 1 bytes to {path}"))
        runner.queue.put_nowait(event("tool_result", name="pi_write", content="Error writing fake.txt: denied"))
        runner.complete("ordinary chat")
        self.assertEqual((await self.settle(task["id"]))["artifacts"], [])

        await self.service.message(task["id"], "failed file", rid())
        while len(self.runners) < 2:
            await asyncio.sleep(0)
        (self.workspace / "partial.txt").write_text("partial", encoding="utf-8")
        self.runners[-1].queue.put_nowait(event("tool_result", name="pi_write", content="Wrote 7 bytes to partial.txt"))
        self.runners[-1].queue.put_nowait(event("error"))
        self.runners[-1].queue.put_nowait(None)
        self.assertEqual((await self.settle(task["id"]))["artifacts"], [])

    async def test_artifact_limit_still_allows_replacing_the_same_filename(self):
        self.service.artifacts.MAX_FILES = 1
        task, runner = await self.start()
        (self.workspace/'report.md').write_text('first',encoding='utf-8')
        runner.queue.put_nowait(event('tool_result',name='pi_write',content='Wrote 5 bytes to report.md'))
        runner.complete()
        first=(await self.settle(task['id']))['artifacts'][0]
        await self.service.message(task['id'],'update report',rid())
        while len(self.runners)<2:
            await asyncio.sleep(0)
        (self.workspace/'report.md').write_text('latest',encoding='utf-8')
        self.runners[-1].queue.put_nowait(event('tool_result',name='pi_edit',content='Edited report.md (1 replacement(s))'))
        self.runners[-1].complete()
        done=await self.settle(task['id'])
        self.assertEqual(len(done['artifacts']),1)
        self.assertNotEqual(done['artifacts'][0]['id'],first['id'])
        self.assertEqual(self.service.artifact(task['id'],done['artifacts'][0]['id'])[1],b'latest')

    async def test_tool_only_turn_is_visible_but_empty_assistant_is_not_model_history(self):
        task, runner = await self.start()
        runner.queue.put_nowait(event('tool_start',name='pi_read'))
        runner.complete('')
        done=await self.settle(task['id'])
        self.assertEqual(done['messages'][-1]['text'],'')
        self.assertEqual(done['messages'][-1]['tools'][0]['kind'],'tool_start')
        await self.service.message(task['id'],'continue',rid())
        while len(self.runners)<2:
            await asyncio.sleep(0)
        self.assertEqual(self.calls[-1][0],[{'role':'user','text':'hello'}])
        self.runners[-1].complete('done')
        await self.settle(task['id'])

    async def test_completion_persists_and_history_is_task_scoped(self):
        accepted, runner = await self.start("first")
        runner.complete("answer one")
        done = await self.settle(accepted["id"])
        self.assertEqual(done["output"], "answer one")
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["run_id"], accepted["run_id"])
        self.assertNotEqual(done["run_id"], "runtime-owned")
        self.assertGreater(done["revision"], accepted["revision"])
        await self.service.shutdown()
        await self.service.startup()
        await self.service.message(done["id"], "second", rid())
        await asyncio.sleep(0)
        self.assertEqual(self.calls[-1], ([{"role":"user", "text":"first"},
                                          {"role":"assistant", "text":"answer one"}], "second"))
        self.runners[-1].complete("answer two")
        await self.settle(done["id"])
        other, other_runner = await self.start("unrelated")
        self.assertEqual(self.calls[-1], ([], "unrelated"))
        other_runner.complete()
        await self.settle(other["id"])

    async def test_stop_guard_and_cleanup_keep_slot(self):
        task, runner = await self.start()
        runner.hold_cleanup = asyncio.Event()
        with self.assertRaises(WebError) as mismatch:
            await self.service.stop(task["id"], rid(), rid())
        self.assertEqual(mismatch.exception.status, 409)
        stop_id = rid()
        stopped = await self.service.stop(task["id"], task["run_id"], stop_id)
        self.assertEqual(stopped["status"], "stopping")
        self.assertEqual(stopped, await self.service.stop(task["id"], task["run_id"], stop_id))
        await asyncio.sleep(0)
        other = self.service.create(rid())
        with self.assertRaises(WebError) as busy:
            await self.service.message(other["id"], "new", rid())
        self.assertEqual(busy.exception.status, 409)
        await self.service.stop(task["id"], task["run_id"], rid())
        self.assertEqual(runner.abort_count, 1)
        runner.hold_cleanup.set()
        final = await self.settle(task["id"])
        self.assertEqual(final["status"], "stopped")
        self.assertTrue(runner.abort_called)

    async def test_error_then_eof_and_retry_excludes_partial_assistant(self):
        task, runner = await self.start("original")
        runner.queue.put_nowait(event("model_delta", text="partial"))
        runner.queue.put_nowait(event("error", message="DO NOT EXPOSE PROVIDER SECRET"))
        runner.queue.put_nowait(None)
        failed = await self.settle(task["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("SECRET", json.dumps(failed))
        self.assertEqual(failed["messages"][-1]["complete"], False)
        retry = rid()
        accepted = await self.service.retry(task["id"], task["run_id"], retry)
        self.assertEqual(accepted, await self.service.retry(task["id"], task["run_id"], retry))
        await asyncio.sleep(0)
        self.assertEqual(self.calls[-1], ([], "original"))
        self.assertEqual(len([m for m in self.service.get_task(task["id"])["messages"] if m["role"]=="user"]), 1)
        self.runners[-1].complete()
        done = await self.settle(task["id"])
        with self.assertRaises(WebError) as old_stop:
            await self.service.stop(task["id"], task["run_id"], rid())
        self.assertEqual(old_stop.exception.status, 409)
        self.assertEqual(done["status"], "completed")

    async def test_eof_without_terminal_is_failure(self):
        task, runner = await self.start()
        runner.queue.put_nowait(None)
        failed = await self.settle(task["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["error"])

    async def test_limits_and_timeout_are_failed_not_stopped(self):
        self.service.max_output = 3
        task, runner = await self.start()
        runner.queue.put_nowait(event("model_delta", text="oversized"))
        failed = await self.settle(task["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertLessEqual(len(failed["output"]), 3)
        self.service.max_output = 100
        self.service.timeout = .01
        task2 = self.service.create(rid())
        accepted = await self.service.message(task2["id"], "timeout", rid())
        failed2 = await self.settle(accepted["id"])
        self.assertEqual(failed2["status"], "failed")
        self.assertTrue(self.runners[-1].cleaned.is_set())

    async def test_input_validation_and_workspace_owner_guards(self):
        task = self.service.create(rid())
        for bad in ("", "  ", None, 3, "x" * 32001):
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises(WebError) as invalid:
                    await self.service.message(task["id"], bad, rid())
                self.assertEqual(invalid.exception.status, 400)
        with self.assertRaises(WebError) as missing:
            self.service.get_task(rid())
        self.assertEqual(missing.exception.status, 404)
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute("UPDATE tasks SET owner='other' WHERE id=?", (task["id"],))
        with self.assertRaises(WebError) as forbidden:
            self.service.get_task(task["id"])
        self.assertEqual(forbidden.exception.status, 403)

    async def test_restart_marks_nonterminal_interrupted_and_lock_excludes_second_service(self):
        task = self.service.create(rid())
        second = WorkspaceService(self.service.database, self.workspace, self.factory, Redactor())
        with self.assertRaises(WebError) as locked:
            await second.startup()
        self.assertEqual(locked.exception.status, 409)
        await self.service.shutdown()
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute("UPDATE tasks SET status='running',run_id=? WHERE id=?", (rid(), task["id"]))
        await self.service.startup()
        recovered = self.service.get_task(task["id"])
        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual(self.calls, [])

    async def test_split_secret_never_persisted_and_title_redacted_before_truncation(self):
        secret = "sensitive-value-" + "x" * 60
        self.service.redactor.add(secret)
        task, runner = await self.start("show " + secret)
        self.assertNotIn("sensitive", task["title"])
        runner.queue.put_nowait(event("model_delta", text="prefix " + secret[:10]))
        async with asyncio.timeout(2):
            while self.service.get_task(task["id"])["output"] != "prefix ":
                await asyncio.sleep(0)
        runner.queue.put_nowait(event("model_delta", text=secret[10:] + " suffix"))
        runner.queue.put_nowait(event("tool_start", name="pi_read", input={"credential":secret}))
        runner.queue.put_nowait(event("run_end", status="completed", output=""))
        runner.queue.put_nowait(None)
        done = await self.settle(task["id"])
        self.assertEqual(done["output"], "prefix [REDACTED] suffix")
        self.assertEqual(self.calls[-1][1], "show " + secret)
        self.assertNotIn(secret, json.dumps(done))
        with closing(sqlite3.connect(self.service.database)) as db:
            dump = "".join(db.iterdump())
        self.assertNotIn(secret, dump)
        self.assertIn('[REDACTED]', done['messages'][-1]['tools'][0]['input'])
        self.assertIn("pi_read", dump)

    async def test_shutdown_drains_running_worker_before_reopening(self):
        task, runner = await self.start()
        await self.service.shutdown()
        self.assertTrue(runner.cleaned.is_set())
        await self.service.startup()
        self.assertEqual(self.service.get_task(task["id"])["status"], "stopped")

    async def test_immediate_stop_before_worker_start_skips_model(self):
        task = self.service.create(rid())
        accepted = await self.service.message(task["id"], "do work", rid())
        await self.service.stop(task["id"], accepted["run_id"], rid())
        done = await self.settle(task["id"])
        self.assertEqual(done["status"], "stopped")
        self.assertEqual(self.calls, [])

    async def test_completion_wins_stop_race_and_old_controls_cannot_change_next_run(self):
        task, runner = await self.start()
        runner.complete("completed first")
        await self.service.stop(task["id"], task["run_id"], rid())
        done = await self.settle(task["id"])
        self.assertEqual(done["status"], "completed")
        accepted = await self.service.message(task["id"], "next", rid())
        for action in (self.service.stop, self.service.retry):
            with self.assertRaises(WebError) as stale:
                await action(task["id"], task["run_id"], rid())
            self.assertEqual(stale.exception.status, 409)
        self.assertEqual(self.service.get_task(task["id"])["run_id"], accepted["run_id"])

    async def test_event_limit_and_factory_exception_release_slot(self):
        self.service.max_events = 2
        task, runner = await self.start()
        for i in range(3):
            runner.queue.put_nowait(event("tool_start", name="pi_read"))
        failed = await self.settle(task["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(len(failed["events"]), 2)
        def broken_factory(history, prompt):
            raise ValueError("DO NOT EXPOSE FACTORY SECRET")
        self.service.runner_factory = broken_factory
        next_task = self.service.create(rid())
        await self.service.message(next_task["id"], "next", rid())
        failed2 = await self.settle(next_task["id"])
        self.assertEqual(failed2["status"], "failed")
        self.assertNotIn("SECRET", json.dumps(failed2))

    async def test_request_ids_persist_across_restart_and_reject_cross_resource_reuse(self):
        key = rid()
        task = self.service.create(key)
        await self.service.shutdown()
        await self.service.startup()
        self.assertEqual(task, self.service.create(key))
        with self.assertRaises(WebError) as conflict:
            await self.service.message(task["id"], "hello", key)
        self.assertEqual(conflict.exception.status, 409)
        with self.assertRaises(WebError) as invalid:
            self.service.create("not-a-uuid")
        self.assertEqual(invalid.exception.status, 400)
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute("UPDATE tasks SET workspace='different' WHERE id=?", (task["id"],))
        self.assertEqual(self.service.list_tasks(), [])
        with self.assertRaises(WebError) as forbidden:
            self.service.get_task(task["id"])
        self.assertEqual(forbidden.exception.status, 403)

    async def test_dynamic_secret_filter_applies_to_existing_snapshots_and_ledger(self):
        key = rid()
        task = self.service.create(key)
        await self.service.message(task["id"], "later-sensitive-value", rid())
        await asyncio.sleep(0)
        self.runners[-1].complete("later-sensitive-value")
        await self.settle(task["id"])
        self.service.redactor.add("later-sensitive-value")
        self.assertNotIn("later-sensitive-value", json.dumps(self.service.get_task(task["id"])))

    async def test_startup_validation_and_unstarted_access(self):
        await self.service.startup()
        service = WorkspaceService(self.workspace / "private.sqlite", self.workspace, self.factory, Redactor())
        with self.assertRaises(WebError) as inside:
            await service.startup()
        self.assertEqual(inside.exception.status, 400)
        missing = WorkspaceService(self.root / "elsewhere" / "a.sqlite", self.root / "missing", self.factory, Redactor())
        with self.assertRaises(WebError) as no_workspace:
            await missing.startup()
        self.assertEqual(no_workspace.exception.status, 400)
        with self.assertRaises(WebError) as unstarted:
            missing.list_tasks()
        self.assertEqual(unstarted.exception.status, 503)

    async def test_failed_retry_requires_expected_run_and_user_message(self):
        task = self.service.create(rid())
        for expected in (rid(), None):
            with self.assertRaises(WebError) as invalid:
                await self.service.retry(task["id"], expected, rid())
            self.assertEqual(invalid.exception.status, 409)
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute("UPDATE tasks SET status='failed',run_id=? WHERE id=?", ("run", task["id"]))
        with self.assertRaises(WebError) as no_user:
            await self.service.retry(task["id"], "run", rid())
        self.assertEqual(no_user.exception.status, 409)

    async def test_terminal_output_without_delta_and_unknown_status(self):
        task, runner = await self.start()
        runner.queue.put_nowait(event("run_end", status="completed", output="final only"))
        runner.queue.put_nowait(None)
        done = await self.settle(task["id"])
        self.assertEqual(done["output"], "final only")
        task2, runner2 = await self.start()
        runner2.queue.put_nowait(event("run_end", status="unexpected", output=""))
        runner2.queue.put_nowait(None)
        failed = await self.settle(task2["id"])
        self.assertEqual(failed["status"], "failed")

    async def test_shutdown_before_worker_starts_recovers_interrupted(self):
        task = self.service.create(rid())
        await self.service.message(task["id"], "pending", rid())
        await self.service.shutdown()
        self.assertEqual(self.calls, [])
        await self.service.startup()
        self.assertEqual(self.service.get_task(task["id"])["status"], "interrupted")

    async def test_redaction_before_output_limit_prevents_truncated_secret_leak(self):
        secret = "secret-with-long-value"
        self.service.redactor.add(secret)
        self.service.max_output = 10
        task, runner = await self.start()
        runner.queue.put_nowait(event("model_delta", text=secret))
        failed = await self.settle(task["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["output"], "[REDACTED]")

    async def test_startup_does_not_recover_foreign_workspace_or_owner_tasks(self):
        task = self.service.create(rid())
        await self.service.shutdown()
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute("UPDATE tasks SET workspace='foreign',status='running' WHERE id=?", (task["id"],))
        await self.service.startup()
        with closing(sqlite3.connect(self.service.database)) as db:
            row = db.execute("SELECT workspace,status FROM tasks WHERE id=?", (task["id"],)).fetchone()
        self.assertEqual(row, ("foreign", "running"))

    async def test_cached_create_cannot_bypass_owner_workspace_guard(self):
        key = rid()
        task = self.service.create(key)
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute("UPDATE tasks SET workspace='foreign' WHERE id=?", (task["id"],))
        with self.assertRaises(WebError) as forbidden:
            self.service.create(key)
        self.assertEqual(forbidden.exception.status, 403)

    async def test_new_message_after_failure_excludes_partial_assistant(self):
        task, runner = await self.start("first")
        runner.queue.put_nowait(event("model_delta", text="unfinished assistant"))
        runner.queue.put_nowait(None)
        await self.settle(task["id"])
        await self.service.message(task["id"], "different request", rid())
        await asyncio.sleep(0)
        self.assertEqual(self.calls[-1], ([{"role":"user","text":"first"}], "different request"))

    async def test_nested_real_runtime_iterator_cleanup_holds_slot_on_limit(self):
        from unittest.mock import patch
        from pi_deepagent.web.runner import make_runner_factory
        begun, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Graph:
            async def astream_events(self, state, **kwargs):
                try:
                    yield {"event":"on_chat_model_stream", "data":{"chunk":{"content":"oversized"}}}
                finally:
                    begun.set()
                    await release.wait()
                    finished.set()
        self.service.runner_factory = make_runner_factory(self.workspace, "fake:local")
        self.service.max_output = 1
        with patch("pi_deepagent.web.runner.create_pi_deepagent", return_value=Graph()):
            task = self.service.create(rid())
            await self.service.message(task["id"], "limit", rid())
            try:
                await asyncio.wait_for(begun.wait(), 2)
                self.assertFalse(finished.is_set())
                self.assertEqual(self.service.get_task(task["id"])["status"], "running")
                other = self.service.create(rid())
                with self.assertRaises(WebError) as busy:
                    await self.service.message(other["id"], "another", rid())
                self.assertEqual(busy.exception.status, 409)
            finally:
                release.set()
            done = await self.settle(task["id"])
        self.assertTrue(finished.is_set())
        self.assertEqual(done["status"], "failed")

    async def test_tool_name_redacted_before_truncation(self):
        secret = "private-tool-" + "X" * 200
        self.service.redactor.add(secret)
        task, runner = await self.start()
        runner.queue.put_nowait(event("tool_start", name=secret))
        runner.complete("done")
        done = await self.settle(task["id"])
        self.assertNotIn(secret[:100], json.dumps(done))
        self.assertIn("[REDACTED]", json.dumps(done))

    async def test_archive_restore_persists_and_archived_tasks_are_read_only(self):
        task = self.service.create(rid())
        archive_id = rid()
        archived = self.service.set_archived(task['id'], True, archive_id)
        self.assertTrue(archived['archived'])
        self.assertEqual(self.service.list_tasks(), [])
        self.assertEqual([t['id'] for t in self.service.list_tasks(True)], [task['id']])
        for operation in [lambda: self.service.message(task['id'], 'no', rid()),
                          lambda: self.service.retry(task['id'], rid(), rid())]:
            with self.assertRaises(WebError) as denied:
                await operation()
            self.assertEqual(denied.exception.status, 409)
        await self.service.shutdown()
        await self.service.startup()
        self.assertTrue(self.service.get_task(task['id'])['archived'])
        self.service.set_archived(task['id'], False, rid())
        self.assertFalse(self.service.get_task(task['id'])['archived'])
        self.assertFalse(self.service.set_archived(task['id'], True, archive_id)['archived'])

    async def test_running_and_stopping_tasks_cannot_be_archived(self):
        task, runner = await self.start()
        for status in ['running', 'stopping']:
            if status == 'stopping':
                await self.service.stop(task['id'], task['run_id'], rid())
            with self.assertRaises(WebError) as denied:
                self.service.set_archived(task['id'], True, rid())
            self.assertEqual(denied.exception.status, 409)
        await self.settle(task['id'])

    async def test_purge_removes_snapshots_cached_content_and_blocks_old_creates(self):
        create_id = rid()
        task = self.service.create(create_id)
        task_id = task['id']
        source = self.workspace / 'report.md'
        source.write_text('keep actual file', encoding='utf-8')
        snapshot = self.service.artifacts.capture(task_id, rid(), 'report.md')
        self.assertIsNotNone(snapshot)
        self.service.set_archived(task_id, True, rid())
        purge_id = rid()
        result = self.service.purge([task_id], purge_id)
        self.assertEqual(result, {'deleted_ids': [task_id]})
        self.assertEqual(self.service.purge([task_id], purge_id), result)
        self.assertEqual(source.read_text(encoding='utf-8'), 'keep actual file')
        self.assertFalse((self.service.artifacts.directory / task_id).exists())
        with closing(sqlite3.connect(self.service.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)
        with self.assertRaises(WebError) as gone:
            self.service.get_task(task_id)
        self.assertEqual(gone.exception.status, 404)
        with self.assertRaises(WebError) as replay:
            self.service.create(create_id)
        self.assertEqual(replay.exception.status, 409)
        with self.assertRaises(WebError) as conflict:
            self.service.create(purge_id)
        self.assertEqual(conflict.exception.status, 409)

    async def test_purge_validates_whole_batch_before_deleting(self):
        first, second = self.service.create(rid()), self.service.create(rid())
        self.service.set_archived(first['id'], True, rid())
        for invalid, code in [(second['id'], 409), (rid(), 404)]:
            with self.assertRaises(WebError) as denied:
                self.service.purge([first['id'], invalid], rid())
            self.assertEqual(denied.exception.status, code)
            self.assertTrue(self.service.get_task(first['id'])['archived'])
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute("UPDATE tasks SET workspace='other' WHERE id=?", (second['id'],))
        with self.assertRaises(WebError) as denied:
            self.service.purge([first['id'], second['id']], rid())
        self.assertEqual(denied.exception.status, 403)
        self.assertTrue(self.service.get_task(first['id'])['archived'])

    async def test_purge_cleanup_failure_retries_after_restart(self):
        from unittest.mock import patch
        task = self.service.create(rid())
        (self.workspace / 'report.md').write_text('report', encoding='utf-8')
        self.assertIsNotNone(self.service.artifacts.capture(task['id'], rid(), 'report.md'))
        self.service.set_archived(task['id'], True, rid())
        purge_id = rid()
        with patch('pi_deepagent.web.service.shutil.rmtree', autospec=True, side_effect=OSError('busy')):
            with self.assertRaises(WebError) as incomplete:
                self.service.purge([task['id']], purge_id)
        self.assertEqual(incomplete.exception.status, 503)
        self.assertTrue((self.service.artifacts.directory / task['id']).exists())
        self.assertEqual(self.service.list_tasks(True), [])
        await self.service.shutdown()
        await self.service.startup()
        self.assertEqual(self.service.purge([task['id']], purge_id)['deleted_ids'], [task['id']])
        self.assertFalse((self.service.artifacts.directory / task['id']).exists())

    async def test_legacy_schema_migration_is_repeatable_and_keeps_conversation(self):
        task = self.service.create(rid())
        await self.service.shutdown()
        with closing(sqlite3.connect(self.service.database)) as db, db:
            db.execute('ALTER TABLE tasks DROP COLUMN archived')
        for _ in range(2):
            await self.service.startup()
            saved = self.service.get_task(task['id'])
            self.assertFalse(saved['archived'])
            self.assertEqual(saved['title'], task['title'])
            await self.service.shutdown()
        await self.service.startup()

    async def test_structured_tool_values_are_persisted_bounded_and_redacted(self):
        secret = 'tool-secret-value-123'
        self.service.redactor.add(secret)
        task, runner = await self.start()
        runner.queue.put_nowait(event('tool_start', tool_call={'call_id':'call-1', 'name':'pi_read', 'arguments':{'path':secret}}))
        runner.queue.put_nowait(event('tool_result', tool_call_id='call-1', name='pi_read', content=secret+'x'*40000, is_error=True))
        runner.queue.put_nowait(event('tool_result', tool_call_id='call-2', name='other', content=None))
        runner.complete('done')
        done = await self.settle(task['id'])
        tools = done['messages'][-1]['tools']
        self.assertEqual([t['call_id'] for t in tools], ['call-1', 'call-1', 'call-2'])
        self.assertNotIn(secret, json.dumps(done))
        self.assertIn('[REDACTED]', tools[0]['input'])
        self.assertTrue(tools[1]['is_error'])
        self.assertTrue(tools[1]['content_truncated'])
        self.assertLess(len(tools[1]['content']), 32800)
        self.assertIn('截斷', tools[1]['content'])
        self.assertEqual(tools[2]['content'], 'null')
        await self.service.shutdown()
        await self.service.startup()
        self.assertEqual(self.service.get_task(task['id'])['messages'][-1]['tools'], tools)
