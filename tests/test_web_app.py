import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from pi_deepagent.contracts import AgentEvent
from pi_deepagent.web.app import create_app
from pi_deepagent.web.auth import AuthService, Redactor

PASSWORD = 'Correct-horse-1999!'
ORIGIN = 'http://127.0.0.1:8765'


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.base = Path(self.temp.name)
        self.workspace = self.base / 'workspace'; self.workspace.mkdir()
        self.data = self.base / 'private'; self.data.mkdir()
        AuthService(self.data, Redactor()).initialize(PASSWORD)
        class Runner:
            async def stream(self):
                yield AgentEvent('event1','run1','model_delta',{'text':'answer'})
                yield AgentEvent('event2','run1','run_end',{'status':'completed'})
            def abort(self): pass
        self.app = create_app(self.data, self.workspace, 'google_genai:test', ORIGIN, runner_factory=lambda history,prompt: Runner())
        await self.app.startup()
        self.cookie = ''; self.csrf = ''

    async def asyncTearDown(self):
        await self.app.shutdown()
        self.temp.cleanup()

    async def request(self, method, path, body=None, extra=None, raw=None):
        headers = {'host': '127.0.0.1:8765', 'origin': ORIGIN, 'content-type': 'application/json'}
        if self.cookie: headers['cookie'] = self.cookie
        if self.csrf: headers['x-csrf-token'] = self.csrf
        if extra: headers.update(extra)
        request_body = raw if raw is not None else json.dumps(body or {}).encode()
        incoming = [{'type':'http.request','body': request_body, 'more_body':False}]
        sent=[]
        async def receive(): return incoming.pop(0) if incoming else {'type':'http.disconnect'}
        async def send(message): sent.append(message)
        path, _, query = path.partition('?')
        await self.app({'type':'http','method':method,'path':path,'query_string':query.encode(),'headers':[(k.encode(),v.encode()) for k,v in headers.items()], 'client':('127.0.0.1',1234)}, receive, send)
        status=sent[0]['status']; out_headers=dict(sent[0]['headers']); content=b''.join(m.get('body',b'') for m in sent[1:])
        self.assertEqual(out_headers[b'cache-control'],b'no-store')
        self.assertEqual(out_headers[b'x-content-type-options'],b'nosniff')
        return status,out_headers,content

    async def login(self):
        status,headers,content=await self.request('POST','/api/login',{'username':'admin','password':PASSWORD})
        self.assertEqual(status,200)
        self.cookie=headers[b'set-cookie'].decode().split(';')[0]
        self.csrf=json.loads(content)['csrf']
        self.assertIn(b'HttpOnly',headers[b'set-cookie']); self.assertIn(b'SameSite=Strict',headers[b'set-cookie'])

    async def test_all_private_routes_require_login(self):
        uid=str(uuid4())
        for method,path in [('GET','/api/session'),('GET','/api/tasks'),('POST','/api/tasks'),('GET',f'/api/tasks/{uid}'),('GET',f'/api/tasks/{uid}/result'),('GET',f'/api/tasks/{uid}/artifacts/{uid}'),('POST',f'/api/tasks/{uid}/messages'),('POST',f'/api/tasks/{uid}/stop'),('POST',f'/api/tasks/{uid}/retry'),('POST',f'/api/tasks/{uid}/archive'),('POST',f'/api/tasks/{uid}/restore'),('POST','/api/tasks/purge'),('POST','/api/logout')]:
            with self.subTest(path=path,method=method): self.assertEqual((await self.request(method,path))[0],401)

    async def test_host_origin_csrf_and_unknown_fields(self):
        self.assertEqual((await self.request('GET','/',extra={'host':'evil.example'}))[0],403)
        self.assertEqual((await self.request('POST','/api/login',{'username':'admin','password':PASSWORD},extra={'origin':'https://evil.example'}))[0],403)
        await self.login()
        self.assertEqual((await self.request('POST','/api/tasks',{'request_id':str(uuid4())},extra={'x-csrf-token':'bad'}))[0],403)
        self.assertEqual((await self.request('POST','/api/tasks',{'request_id':str(uuid4()),'role':'admin'}))[0],400)
        self.assertEqual((await self.request('POST','/api/tasks',raw=b'{'))[0],400)
        self.assertEqual((await self.request('POST','/api/tasks',raw=b'x'*131073))[0],413)

    async def test_real_admin_create_deduplicate_read_and_logout(self):
        await self.login()
        rid=str(uuid4()); first=await self.request('POST','/api/tasks',{'request_id':rid}); self.assertEqual(first[0],201)
        second=await self.request('POST','/api/tasks',{'request_id':rid}); self.assertEqual(json.loads(first[2]),json.loads(second[2]))
        task=json.loads(first[2]); self.assertEqual((await self.request('GET','/api/tasks/'+task['id']))[0],200)
        self.assertEqual(len(json.loads((await self.request('GET','/api/tasks'))[2])['tasks']),1)
        self.assertEqual((await self.request('GET','/api/tasks/'+task['id']+'/result'))[0],409)
        self.assertEqual((await self.request('POST','/api/logout'))[0],200)
        self.assertEqual((await self.request('GET','/api/tasks'))[0],401)

    async def test_role_and_resource_authority(self):
        await self.login()
        token=self.cookie.split('=',1)[1]
        session=self.app.auth.authenticate(token); session.role='viewer'
        self.assertEqual((await self.request('GET','/api/tasks'))[0],403)
        session.role='admin'
        self.assertEqual((await self.request('GET','/api/tasks/'+str(uuid4())))[0],404)
        self.assertEqual((await self.request('GET','/admin.json'))[0],404)

    async def test_failure_diagnostics_use_existing_task_authorization(self):
        class FailingRunner:
            async def stream(self):
                yield AgentEvent('error', 'runtime', 'error', {'message':'HTTP 400 private provider response'})
        self.app.service.runner_factory = lambda history, prompt: FailingRunner()
        await self.login()
        task = json.loads((await self.request('POST','/api/tasks',{'request_id':str(uuid4())}))[2])
        path = '/api/tasks/' + task['id']
        await self.request('POST',path+'/messages',{'request_id':str(uuid4()),'text':'test'})
        await self.app.service._worker
        status, _, body = await self.request('GET',path)
        self.assertEqual(status,200)
        d = json.loads(body)['diagnostic']
        self.assertIn('HTTP 400',d['message'])
        self.assertNotIn(b'private provider response',body)
        self.assertTrue(d['log_saved'])
        token = self.cookie.split('=',1)[1]
        session = self.app.auth.authenticate(token)
        session.role = 'viewer'
        self.assertEqual((await self.request('GET',path))[0],403)
        session.role = 'admin'
        with self.app.service._connection():
            self.app.service._connection().execute("UPDATE tasks SET owner='other' WHERE id=?",(task['id'],))
        self.assertEqual((await self.request('GET',path))[0],403)
        await self.request('POST','/api/logout')
        self.assertEqual((await self.request('GET',path))[0],401)

    async def test_private_headers_and_input_limits(self):
        await self.login()
        self.assertEqual((await self.request('POST','/api/tasks',{'request_id':'invalid'}))[0],400)
        response=await self.request('POST','/api/tasks',{'request_id':str(uuid4())}); task=json.loads(response[2])
        for text in ['', '   ', 12, 'x'*32001]:
            self.assertEqual((await self.request('POST',f"/api/tasks/{task['id']}/messages",{'request_id':str(uuid4()),'text':text}))[0],400)
        self.assertEqual((await self.request('POST','/api/tasks',{'request_id':str(uuid4())},extra={'content-type':'text/plain'}))[0],415)

    def test_data_directory_cannot_be_inside_workspace(self):
        with self.assertRaises(ValueError): create_app(self.workspace/'private',self.workspace,'google_genai:test',ORIGIN)

    async def test_every_route_rejects_wrong_role_and_revoked_session(self):
        await self.login()
        token=self.cookie.split('=',1)[1]
        uid=str(uuid4())
        routes=[('GET','/api/session'),('GET','/api/tasks'),('POST','/api/tasks'),('GET',f'/api/tasks/{uid}'),('GET',f'/api/tasks/{uid}/result'),('GET',f'/api/tasks/{uid}/artifacts/{uid}'),('POST',f'/api/tasks/{uid}/messages'),('POST',f'/api/tasks/{uid}/stop'),('POST',f'/api/tasks/{uid}/retry'),('POST',f'/api/tasks/{uid}/archive'),('POST',f'/api/tasks/{uid}/restore'),('POST','/api/tasks/purge'),('POST','/api/logout')]
        session=self.app.auth.authenticate(token);session.role='viewer'
        for method,path in routes:
            with self.subTest(role='viewer',path=path): self.assertEqual((await self.request(method,path))[0],403)
        session.role='admin';self.app.auth.logout(token)
        for method,path in routes:
            with self.subTest(role='revoked',path=path): self.assertEqual((await self.request(method,path))[0],401)

    async def test_successful_message_result_headers_and_redaction(self):
        await self.login()
        response=await self.request('POST','/api/tasks',{'request_id':str(uuid4())});task=json.loads(response[2])
        path=f"/api/tasks/{task['id']}"
        response=await self.request('POST',path+'/messages',{'request_id':str(uuid4()),'text':'summarize project'})
        self.assertEqual(response[0],202)
        for _ in range(100):
            response=await self.request('GET',path); snapshot=json.loads(response[2])
            if snapshot['status']!='running':break
            await asyncio.sleep(0)
        self.assertEqual(snapshot['status'],'completed')
        self.assertEqual(snapshot['output'],'answer')
        response=await self.request('GET',path+'/result')
        self.assertEqual(response[0],200);self.assertEqual(response[2],b'answer')
        self.assertIn(b'attachment',response[1][b'content-disposition'])
        self.assertEqual(response[1][b'content-type'],b'text/plain; charset=utf-8')
        log=(self.data/'audit.log').read_text(encoding='utf-8')
        self.assertNotIn(PASSWORD,log);self.assertNotIn(self.csrf,log);self.assertNotIn('summarize project',log)
        self.assertIn('identity=admin',log);self.assertIn(task['id'],log)

    async def test_unknown_fields_duplicate_keys_and_surrogate_rejected(self):
        await self.login()
        for raw in [b'{"request_id":"x","request_id":"y"}',b'[]',b'{"request_id":"\\ud800"}']:
            with self.subTest(raw=raw):self.assertEqual((await self.request('POST','/api/tasks',raw=raw))[0],400)

    async def test_expired_session_cannot_read(self):
        await self.login(); token=self.cookie.split('=',1)[1]
        self.app.auth.authenticate(token).expires_at=0
        self.assertEqual((await self.request('GET','/api/tasks'))[0],401)
        uid=str(uuid4())
        self.assertEqual((await self.request('GET',f'/api/tasks/{uid}/artifacts/{uid}'))[0],401)

    async def test_artifact_download_has_task_scope_redaction_and_safe_attachment(self):
        await self.login()
        secret='artifact-provider-secret';self.app.redactor.add(secret)
        class Runner:
            async def stream(inner):
                (self.workspace/'成果.html').write_text('<script>'+secret+'</script>',encoding='utf-8')
                yield AgentEvent('one','run','tool_result',{'name':'pi_write','content':'Wrote 40 bytes to 成果.html'})
                yield AgentEvent('two','run','run_end',{'status':'completed','output':'完成檔案'})
            def abort(inner):pass
        self.app.service.runner_factory=lambda history,prompt:Runner()
        task=json.loads((await self.request('POST','/api/tasks',{'request_id':str(uuid4())}))[2])
        path='/api/tasks/'+task['id']
        await self.request('POST',path+'/messages',{'request_id':str(uuid4()),'text':'create artifact'})
        await self.app.service._worker
        snapshot=json.loads((await self.request('GET',path))[2])
        self.assertEqual(len(snapshot['artifacts']),1)
        artifact=snapshot['artifacts'][0]
        download=path+'/artifacts/'+artifact['id']
        response=await self.request('GET',download)
        self.assertEqual(response[0],200)
        self.assertEqual(response[2],b'<script>[REDACTED]</script>')
        self.assertEqual(response[1][b'content-type'],b'application/octet-stream')
        self.assertIn(b"filename*=UTF-8''%E6%88%90%E6%9E%9C.html",response[1][b'content-disposition'])
        other=json.loads((await self.request('POST','/api/tasks',{'request_id':str(uuid4())}))[2])
        self.assertEqual((await self.request('GET',f"/api/tasks/{other['id']}/artifacts/{artifact['id']}"))[0],404)
        (self.data/'artifacts'/task['id']/artifact['id']).unlink()
        self.assertEqual((await self.request('GET',download))[0],404)
        with self.app.service._connection():
            self.app.service._connection().execute("UPDATE tasks SET owner='other' WHERE id=?",(task['id'],))
        self.assertEqual((await self.request('GET',download))[0],403)
        await self.request('POST','/api/logout')
        self.assertEqual((await self.request('GET',download))[0],401)

    async def test_model_output_known_secrets_never_reach_snapshot_or_download(self):
        await self.login()
        secret='private-provider-secret-123';self.app.redactor.add(secret)
        class Runner:
            async def stream(self):
                yield AgentEvent('one','run','model_delta',{'text':'prefix '+secret[:8]})
                yield AgentEvent('two','run','model_delta',{'text':secret[8:]+' suffix'})
                yield AgentEvent('three','run','run_end',{'status':'completed'})
            def abort(self):pass
        self.app.service.runner_factory=lambda history,prompt:Runner()
        response=await self.request('POST','/api/tasks',{'request_id':str(uuid4())});task=json.loads(response[2]);path='/api/tasks/'+task['id']
        self.assertEqual((await self.request('POST',path+'/messages',{'request_id':str(uuid4()),'text':'a request'}))[0],202)
        for _ in range(100):
            response=await self.request('GET',path)
            self.assertNotIn(secret.encode(),response[2]);self.assertNotIn(secret[:8].encode(),response[2])
            snapshot=json.loads(response[2])
            if snapshot['status']!='running':break
            await asyncio.sleep(0)
        self.assertEqual(snapshot['output'],'prefix [REDACTED] suffix')
        self.assertEqual((await self.request('GET',path+'/result'))[2],b'prefix [REDACTED] suffix')

    async def test_stop_and_retry_use_explicit_run_identity(self):
        await self.login()
        started=asyncio.Event()
        class WaitingRunner:
            async def stream(self):
                self.task=asyncio.current_task()
                started.set()
                await asyncio.Event().wait()
                yield AgentEvent('end','run','run_end',{'status':'completed'})
            def abort(self):self.task.cancel()
        self.app.service.runner_factory=lambda history,prompt:WaitingRunner()
        task=json.loads((await self.request('POST','/api/tasks',{'request_id':str(uuid4())}))[2]);path='/api/tasks/'+task['id']
        response=await self.request('POST',path+'/messages',{'request_id':str(uuid4()),'text':'work'})
        self.assertEqual(response[0],202);run_id=json.loads(response[2])['run_id'];await asyncio.wait_for(started.wait(),1)
        self.assertEqual((await self.request('POST',path+'/stop',{'request_id':str(uuid4()),'expected_run_id':str(uuid4())}))[0],409)
        self.assertEqual((await self.request('POST',path+'/stop',{'request_id':str(uuid4()),'expected_run_id':run_id}))[0],202)
        for _ in range(100):
            snapshot=json.loads((await self.request('GET',path))[2])
            if snapshot['status']!='stopping':break
            await asyncio.sleep(0)
        self.assertEqual(snapshot['status'],'stopped')
        self.assertEqual((await self.request('POST',path+'/retry',{'request_id':str(uuid4()),'expected_run_id':run_id}))[0],409)
        class FailedRunner:
            async def stream(self):
                yield AgentEvent('error','run','error',{})
            def abort(self):pass
        self.app.service.runner_factory=lambda history,prompt:FailedRunner()
        snapshot=json.loads((await self.request('POST',path+'/messages',{'request_id':str(uuid4()),'text':'fail'}))[2]);run_id=snapshot['run_id']
        for _ in range(100):
            snapshot=json.loads((await self.request('GET',path))[2])
            if snapshot['status']=='failed':break
            await asyncio.sleep(0)
        self.assertEqual(snapshot['status'],'failed')
        self.assertEqual((await self.request('POST',path+'/retry',{'request_id':str(uuid4()),'expected_run_id':run_id}))[0],202)

    async def test_lifespan_fails_when_another_process_holds_data_lock(self):
        another=create_app(self.data,self.workspace,'google_genai:test',ORIGIN,runner_factory=lambda history,prompt:None)
        sent=[]
        async def receive():return {'type':'lifespan.startup'}
        async def send(message):sent.append(message)
        await another({'type':'lifespan'},receive,send)
        self.assertEqual([message['type'] for message in sent],['lifespan.startup.failed'])
        self.assertFalse(another.ready)

    async def test_lifespan_success_and_public_static_policy(self):
        other=self.base/'other-data';AuthService(other,Redactor()).initialize(PASSWORD)
        app=create_app(other,self.workspace,'google_genai:test',ORIGIN,runner_factory=lambda history,prompt:None)
        incoming=[{'type':'lifespan.startup'},{'type':'lifespan.shutdown'}];sent=[]
        async def receive():return incoming.pop(0)
        async def send(message):sent.append(message)
        await app({'type':'lifespan'},receive,send)
        self.assertEqual([m['type'] for m in sent],['lifespan.startup.complete','lifespan.shutdown.complete'])
        self.assertFalse(app.ready)
        for path in ['/','/app.css','/app.js','/state.mjs']:
            response=await self.request('GET',path)
            self.assertEqual(response[0],200)
            self.assertIn(b"script-src 'self'",response[1][b'content-security-policy'])
        self.assertEqual((await self.request('GET','/../admin.json'))[0],404)
        await self.login()
        self.assertEqual((await self.request('DELETE','/api/tasks'))[0],405)
        self.assertEqual((await self.request('POST','/api/unknown'))[0],404)

    async def test_archive_routes_and_batch_purge_enforce_security(self):
        await self.login()
        response = await self.request('POST', '/api/tasks', {'request_id': str(uuid4())})
        task = json.loads(response[2])
        path = '/api/tasks/' + task['id']
        for action in ['archive', 'restore']:
            body = {'request_id': str(uuid4())}
            for headers in [{'x-csrf-token':'bad'}, {'origin':'https://evil.example'}]:
                self.assertEqual((await self.request('POST', path+'/'+action, body, extra=headers))[0], 403)
            self.assertEqual((await self.request('POST', path+'/'+action, body))[0], 200)
        self.assertEqual((await self.request('POST', path+'/archive', {'request_id':str(uuid4())}))[0], 200)
        self.assertEqual(json.loads((await self.request('GET', '/api/tasks'))[2])['tasks'], [])
        archived = json.loads((await self.request('GET', '/api/tasks?archived=true'))[2])['tasks']
        self.assertEqual([t['id'] for t in archived], [task['id']])
        self.assertEqual((await self.request('POST', path+'/messages', {'request_id':str(uuid4()), 'text':'no'}))[0], 409)
        body = {'request_id': str(uuid4()), 'task_ids': [task['id']]}
        for headers in [{'x-csrf-token':'bad'}, {'origin':'https://evil.example'}]:
            self.assertEqual((await self.request('POST', '/api/tasks/purge', body, extra=headers))[0], 403)
        response = await self.request('POST', '/api/tasks/purge', body)
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[2]), {'deleted_ids':[task['id']]})
        self.assertEqual((await self.request('GET', path))[0], 404)
        self.assertEqual((await self.request('POST', '/api/tasks/purge', body))[0], 200)
        log = (self.data/'audit.log').read_text(encoding='utf-8')
        self.assertIn(path+'/archive', log)
        self.assertIn('/api/tasks/purge ids='+task['id'], log)

    async def test_archive_restore_and_purge_reject_other_workspace_resources(self):
        import sqlite3
        from contextlib import closing
        await self.login()
        response = await self.request('POST', '/api/tasks', {'request_id':str(uuid4())})
        task_id = json.loads(response[2])['id']
        with closing(sqlite3.connect(self.app.service.database)) as db, db:
            db.execute("UPDATE tasks SET workspace='other',archived=1 WHERE id=?", (task_id,))
        for action in ['archive', 'restore']:
            self.assertEqual((await self.request('POST', '/api/tasks/'+task_id+'/'+action, {'request_id':str(uuid4())}))[0], 403)
        self.assertEqual((await self.request('POST', '/api/tasks/purge', {'request_id':str(uuid4()),'task_ids':[task_id]}))[0], 403)
        with closing(sqlite3.connect(self.app.service.database)) as db:
            self.assertEqual(db.execute('SELECT archived FROM tasks WHERE id=?', (task_id,)).fetchone()[0], 1)
