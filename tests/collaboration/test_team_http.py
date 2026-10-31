"""Real loopback HTTP + AuthService + SQLite; draft routes never start a graph."""
from __future__ import annotations

import asyncio
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from uuid import uuid4

import uvicorn

from pi_deepagent.collaboration.contracts import CollaborationError
from pi_deepagent.collaboration.service import TeamService
from pi_deepagent.collaboration.storage import CollaborationStore
from pi_deepagent.web.app import create_app
from pi_deepagent.web.auth import AuthService, Redactor
from tests.collaboration.test_team_service import team


PASSWORD = "HTTP-fixture-admin-2026!"


class ReadyServer(uvicorn.Server):
    """Observe actual startup, without replacing transport or ASGI lifecycle."""
    async def startup(self, sockets=None):
        try:
            await super().startup(sockets=sockets)
        except SystemExit:
            raise RuntimeError("Fixture HTTP server could not start") from None
        finally:
            self.fixture_ready.set()


class TeamHTTPTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root/"workspace"; self.workspace.mkdir()
        self.data = self.root/"private"; self.data.mkdir()
        AuthService(self.data,Redactor()).initialize(PASSWORD)
        self.database = self.data/"tasks.sqlite"
        CollaborationStore.initialize_empty(self.database)
        self.cookie,self.csrf = "",""
        self.server_task = None
        self.addAsyncCleanup(self.stop_server)
        await self.start_server()

    def unexpected_runner(self, history, prompt):
        self.fail("Draft HTTP routes attempted model or command execution")

    async def start_server(self, *, enabled=True):
        listener = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        listener.bind(("127.0.0.1",0))
        self.port = listener.getsockname()[1]
        self.origin = "http://127.0.0.1:"+str(self.port)
        self.app = create_app(self.data,self.workspace,"google_genai:test",self.origin,
                              runner_factory=self.unexpected_runner,collaboration_enabled=enabled)
        config = uvicorn.Config(self.app,host="127.0.0.1",port=self.port,http="h11",loop="asyncio",ws="none",lifespan="on",
                                log_config=None,access_log=False,timeout_graceful_shutdown=2)
        self.server = ReadyServer(config)
        self.server.fixture_ready = asyncio.Event()
        self.server_task = asyncio.create_task(self.server.serve(sockets=[listener]))
        try:
            await asyncio.wait_for(self.server.fixture_ready.wait(),10)
            if self.server_task.done(): await self.server_task
            self.assertTrue(self.server.started)
        except BaseException:
            listener.close()
            raise

    async def stop_server(self):
        if self.server_task is None: return
        self.server.should_exit = True
        try:
            await asyncio.wait_for(self.server_task,10)
        finally:
            self.server_task = None

    async def request(self, method, path, body=None, *, extra=None, raw=None, auth=True):
        headers = {"host":"127.0.0.1:"+str(self.port),"origin":self.origin,"content-type":"application/json"}
        if auth and self.cookie: headers["cookie"] = self.cookie
        if auth and self.csrf: headers["x-csrf-token"] = self.csrf
        if extra: headers.update(extra)
        content = raw if raw is not None else (json.dumps(body,ensure_ascii=False).encode("utf-8") if body is not None else None)
        def exchange():
            connection = http.client.HTTPConnection("127.0.0.1",self.port,timeout=5)
            try:
                connection.request(method,path,body=content,headers=headers)
                response = connection.getresponse()
                return response.status,dict(response.getheaders()),response.read()
            finally:
                connection.close()
        status,out_headers,payload = await asyncio.to_thread(exchange)
        self.assertEqual(out_headers["cache-control"],"no-store")
        self.assertEqual(out_headers["x-content-type-options"],"nosniff")
        return status,out_headers,json.loads(payload)

    async def login(self):
        status,headers,payload = await self.request("POST","/api/login",{"username":"admin","password":PASSWORD},auth=False)
        self.assertEqual(status,200)
        self.cookie = headers["set-cookie"].split(";",1)[0]
        self.csrf = payload["csrf"]

    async def create(self, *, name="fixture team", request_id=None):
        value = {**team(name),"request_id":request_id or str(uuid4())}
        value["members"].append({"name":"verifier","responsibility":"verify","goal":"check required evidence","orchestrator":False})
        status,_,result = await self.request("POST","/api/teams",value)
        self.assertEqual(status,201)
        return result,value

    async def test_actual_http_create_patch_replay_read_and_restart_persistence(self):
        await self.login()
        created,body = await self.create()
        self.assertEqual(len(created["config"]["members"]),3)
        self.assertEqual((await self.request("POST","/api/teams",body))[2],created)
        patch = {**team("edited"),"expected_revision":0,"request_id":str(uuid4())}
        path = "/api/teams/"+created["id"]
        edited = await self.request("PATCH",path,patch)
        self.assertEqual((edited[0],edited[2]["revision"],edited[2]["config"]["name"]),(200,1,"edited"))
        self.assertEqual((await self.request("PATCH",path,patch))[2],edited[2])
        self.assertEqual((await self.request("GET",path))[2],edited[2])
        self.assertEqual([row["id"] for row in (await self.request("GET","/api/teams"))[2]["items"]],[created["id"]])
        await self.stop_server()
        self.assertIsNone(self.app.collaboration)
        await self.start_server()
        self.assertEqual((await self.request("GET",path))[0],401)
        await self.login()
        self.assertEqual((await self.request("GET",path))[2],edited[2])
        self.assertEqual((await self.request("POST","/api/teams",body))[2],created)
        self.assertEqual((await self.request("PATCH",path,patch))[2],edited[2])

    async def test_each_route_rejects_anonymous_nonadmin_logout_and_expired_session(self):
        await self.login(); created,_ = await self.create()
        routes = [("GET","/api/teams",None),("POST","/api/teams",{**team(),"request_id":str(uuid4())}),
                  ("GET","/api/teams/"+created["id"],None),
                  ("PATCH","/api/teams/"+created["id"],{**team(),"request_id":str(uuid4()),"expected_revision":0})]
        for method,path,body in routes:
            with self.subTest(state="anonymous",method=method): self.assertEqual((await self.request(method,path,body,auth=False))[0],401)
        session = self.app.auth.authenticate(self.cookie.split("=",1)[1])
        session.role = "reader"
        for method,path,body in routes:
            with self.subTest(state="nonadmin",method=method): self.assertEqual((await self.request(method,path,body))[0],403)
        session.role = "admin"
        self.assertEqual((await self.request("POST","/api/logout",{}))[0],200)
        for method,path,body in routes:
            with self.subTest(state="logout",method=method): self.assertEqual((await self.request(method,path,body))[0],401)
        await self.login()
        self.app.auth.authenticate(self.cookie.split("=",1)[1]).expires_at = 0
        for method,path,body in routes:
            with self.subTest(state="expired",method=method): self.assertEqual((await self.request(method,path,body))[0],401)

    async def test_post_and_patch_both_require_host_origin_csrf_and_json(self):
        await self.login(); created,_ = await self.create()
        routes = [("POST","/api/teams",{**team(),"request_id":str(uuid4())}),
                  ("PATCH","/api/teams/"+created["id"],{**team(),"request_id":str(uuid4()),"expected_revision":0})]
        for method,path,body in routes:
            for extra,expected in (({"host":"evil.example"},403),({"origin":"https://evil.example"},403),
                                   ({"origin":""},403),({"x-csrf-token":"bad"},403),({"content-type":"text/plain"},415)):
                with self.subTest(method=method,extra=extra): self.assertEqual((await self.request(method,path,body,extra=extra))[0],expected)
        self.assertEqual((await self.request("GET","/api/teams/"+created["id"]))[2]["revision"],0)

    async def test_operation_and_resource_scope_are_checked_before_replay(self):
        await self.login(); created,body = await self.create()
        path = "/api/teams/"+created["id"]
        adapter = self.app.collaboration
        adapter.teams = TeamService(adapter.store,self.app.auth,self.workspace,allowed_actions={"read"})
        for method,target,data in (("POST","/api/teams",body),("PATCH",path,{**team(),"expected_revision":0,"request_id":str(uuid4())})):
            self.assertEqual((await self.request(method,target,data))[0],403)
        adapter.teams = TeamService(adapter.store,self.app.auth,self.workspace)
        foreign = self.root/"foreign-workspace"; foreign.mkdir()
        adapter.teams = TeamService(adapter.store,self.app.auth,foreign)
        for method,target,data in (("GET",path,None),("POST","/api/teams",body),
                                   ("PATCH",path,{**team(),"expected_revision":0,"request_id":str(uuid4())})):
            self.assertEqual((await self.request(method,target,data))[0],403)
        self.assertEqual((await self.request("GET","/api/teams"))[2]["items"],[])

    async def test_stale_version_different_body_and_unknown_fields_are_rejected(self):
        await self.login(); created,body = await self.create()
        self.assertEqual((await self.request("POST","/api/teams",{**body,"goal":"different"}))[0],409)
        path = "/api/teams/"+created["id"]
        self.assertEqual((await self.request("PATCH",path,{**team(),"expected_revision":99,"request_id":str(uuid4())}))[0],409)
        for extra in ({"owner":"admin"},{"workspace":"C:/private"},{"role":"admin"},{"command":"host command"}):
            self.assertEqual((await self.request("POST","/api/teams",{**team(),**extra,"request_id":str(uuid4())}))[0],400)
            self.assertEqual((await self.request("PATCH",path,{**team(),**extra,"request_id":str(uuid4()),"expected_revision":0}))[0],400)
        self.assertEqual((await self.request("GET","/api/teams/"+str(uuid4())))[0],404)
        self.assertEqual((await self.request("PATCH",path,{"name":"partial","expected_revision":0,"request_id":str(uuid4())}))[0],400)

    async def test_query_validation_and_page_cursor_have_no_unscoped_data(self):
        await self.login()
        created = [(await self.create(name="team-"+str(index)))[0] for index in range(3)]
        page = (await self.request("GET","/api/teams?limit=2"))[2]
        self.assertEqual([row["id"] for row in page["items"]],sorted(row["id"] for row in created)[:2])
        following = (await self.request("GET","/api/teams?limit=2&cursor="+page["next_cursor"]))[2]
        self.assertEqual([row["id"] for row in following["items"]],sorted(row["id"] for row in created)[2:])
        self.assertIsNone(following["next_cursor"])
        for query in ("limit=0","limit=201","limit=-1","limit=x","limit=1&limit=2","cursor=bad","owner=admin","limit=1&cursor=&extra=x"):
            with self.subTest(query=query): self.assertEqual((await self.request("GET","/api/teams?"+query))[0],400)
        self.assertEqual((await self.request("GET","/api/teams/"+created[0]["id"]+"?limit=1"))[0],400)

    async def test_json_boundaries_apply_to_patch_as_well_as_post(self):
        await self.login(); created,_ = await self.create()
        for method,path in (("POST","/api/teams"),("PATCH","/api/teams/"+created["id"])):
            for raw,expected in ((b'{',400),(b'[]',400),(b'{"request_id":"a","request_id":"b"}',400),(b'x'*131073,413)):
                with self.subTest(method=method,raw_length=len(raw)): self.assertEqual((await self.request(method,path,raw=raw))[0],expected)
        self.assertEqual((await self.request("PATCH","/api/tasks/"+str(uuid4()),{}))[0],405)
        self.assertEqual((await self.request("POST","/api/teams/"+created["id"]+"/runs",{}))[0],404)
        self.assertEqual((await self.request("GET","/api/teams/"+created["id"]+"/extra"))[0],404)

    async def test_audit_keeps_safe_ids_and_secrets_do_not_reach_response_or_database(self):
        await self.login()
        secret = "http-known-secret-fixture"; self.app.redactor.add(secret)
        created,body = await self.create(name=secret)
        self.assertEqual(created["config"]["name"],"[REDACTED]")
        late,_ = await self.create(name="late-known-secret")
        self.app.redactor.add("late-known-secret")
        self.assertEqual((await self.request("GET","/api/teams/"+late["id"]))[2]["config"]["name"],"[REDACTED]")
        self.assertEqual((await self.request("GET","/api/teams/"+secret))[0],400)
        log = (self.data/"audit.log").read_text(encoding="utf-8")
        self.assertIn("/api/teams/"+created["id"],log)
        self.assertIn("request_id="+body["request_id"],log)
        for value in (secret,PASSWORD,self.cookie.split("=",1)[1],self.csrf): self.assertNotIn(value,log)
        with closing(sqlite3.connect(self.database)) as db:
            stored = str(db.execute("SELECT config FROM collab_teams WHERE id=?",(created["id"],)).fetchone())
            self.assertNotIn(secret,stored)

    async def test_parallel_http_edits_cas_and_legacy_drafts_still_work(self):
        await self.login(); created,_ = await self.create()
        path = "/api/teams/"+created["id"]
        results = await asyncio.gather(*[self.request("PATCH",path,{**team("choice-"+str(index)),"expected_revision":0,"request_id":str(uuid4())}) for index in range(2)])
        self.assertEqual(sorted(result[0] for result in results),[200,409])
        self.assertEqual((await self.request("GET",path))[2]["revision"],1)
        legacy = await self.request("POST","/api/tasks",{"request_id":str(uuid4())})
        self.assertEqual(legacy[0],201)
        self.assertEqual((await self.request("GET","/api/tasks/"+legacy[2]["id"]))[0],200)
        self.assertEqual(len((await self.request("GET","/api/teams"))[2]["items"]),1)

    async def test_disabled_feature_still_authenticates_and_does_not_start_runtime(self):
        await self.stop_server(); await self.start_server(enabled=False)
        self.cookie,self.csrf = "",""
        self.assertEqual((await self.request("GET","/api/teams",auth=False))[0],401)
        await self.login()
        self.assertEqual((await self.request("GET","/api/teams"))[0],503)
        self.assertEqual((await self.request("PATCH","/api/teams/"+str(uuid4()),{}))[0],503)
        self.assertIsNone(self.app.collaboration)

    async def test_missing_schema_fails_without_migration_or_partial_ready_state(self):
        await self.stop_server()
        other = self.root/"unprepared"; other.mkdir()
        AuthService(other,Redactor()).initialize(PASSWORD)
        database = other/"tasks.sqlite"
        with closing(sqlite3.connect(database)) as db:
            db.execute("CREATE TABLE original_data (value TEXT)")
            db.execute("INSERT INTO original_data VALUES ('keep')"); db.commit()
        before = database.read_bytes()
        app = create_app(other,self.workspace,"google_genai:test",self.origin,runner_factory=self.unexpected_runner,collaboration_enabled=True)
        with self.assertRaises(CollaborationError) as raised: await app.startup()
        self.assertEqual(raised.exception.code,"schema_required")
        self.assertFalse(app.ready)
        self.assertIsNone(app.collaboration)
        self.assertEqual(database.read_bytes(),before)

    async def test_default_legacy_startup_does_not_create_collaboration_schema(self):
        other = self.root/"legacy-only"; other.mkdir()
        AuthService(other,Redactor()).initialize(PASSWORD)
        app = create_app(other,self.workspace,"google_genai:test",self.origin,runner_factory=self.unexpected_runner)
        try:
            await app.startup()
            with closing(sqlite3.connect(other/"tasks.sqlite")) as db:
                self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'collab_%'").fetchall(),[])
            self.assertIsNone(app.collaboration)
        finally:
            await app.shutdown()


class TeamCLIConfigurationTests(TestCase):
    def test_help_exposes_explicit_opt_in_without_starting_server(self):
        result = subprocess.run([sys.executable,"-m","pi_deepagent.web","--help"],capture_output=True,text=True,encoding="utf-8",timeout=10,
                                env={**os.environ,"PYTHONIOENCODING":"utf-8"})
        self.assertEqual(result.returncode,0)
        self.assertIn("--collaboration",result.stdout)

    def test_opt_in_requires_schema_and_never_creates_or_migrates_it(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); workspace = root/"workspace"; workspace.mkdir(); data = root/"private"; data.mkdir()
            result = subprocess.run([sys.executable,"-m","pi_deepagent.web","--workspace",str(workspace),"--data-dir",str(data),
                                     "--model","google_genai:test","--collaboration"],capture_output=True,text=True,encoding="utf-8",timeout=10,
                                    env={**os.environ,"PYTHONIOENCODING":"utf-8"})
            self.assertEqual(result.returncode,2)
            self.assertIn("migration",result.stderr)
            self.assertFalse((data/"tasks.sqlite").exists())
