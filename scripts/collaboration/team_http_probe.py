"""Capture a real draft-team HTTP round trip and sanitized SQLite/audit evidence.

Run from repository root: python -m scripts.collaboration.team_http_probe
--output <new-directory>. Uses an isolated test server, never a production DB.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from tests.collaboration.test_team_http import PASSWORD, TeamHTTPTests
from tests.collaboration.test_team_service import team


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)

    class RoundTrip(TeamHTTPTests):
        async def test_round_trip(self):
            records = []
            async def call(method, path, body=None, expected=200, auth=True):
                status, headers, result = await self.request(method,path,body,auth=auth)
                records.append({"method":method,"path":path,"request":body,"expected_status":expected,"actual_status":status,
                                "result":result,"cache_control":headers["cache-control"],"nosniff":headers["x-content-type-options"]})
                self.assertEqual(status,expected)
                return result
            await call("GET","/api/teams",expected=401,auth=False)
            await self.login()
            create_body = {**team("evidence team"),"request_id":str(uuid4())}
            create_body["members"].append({"name":"verifier","responsibility":"verify","goal":"check required evidence","orchestrator":False})
            created = await call("POST","/api/teams",create_body,expected=201)
            self.assertEqual(len(created["config"]["members"]),3)
            self.assertEqual(await call("POST","/api/teams",create_body,expected=201),created)
            path = "/api/teams/"+created["id"]
            update = {**team("revised evidence team"),"members":create_body["members"],"expected_revision":0,"request_id":str(uuid4())}
            edited = await call("PATCH",path,update)
            self.assertEqual((edited["revision"],edited["config"]["name"]),(1,"revised evidence team"))
            self.assertEqual(await call("PATCH",path,update),edited)
            await call("PATCH",path,{**update,"request_id":str(uuid4())},expected=409)
            self.assertEqual(await call("GET",path),edited)
            await call("GET","/api/teams?limit=1")
            await call("POST","/api/logout",{})
            await call("GET",path,expected=401)
            await self.stop_server()
            shutil.copyfile(self.database,args.output/"tasks.sqlite")
            shutil.copyfile(self.data/"audit.log",args.output/"audit.log")
            record = {"recorded_at":datetime.now(timezone.utc).isoformat(),"kind":"real_loopback_http_draft_only",
                      "origin":self.origin,"server_stopped":self.server_task is None,"team_id":created["id"],"revision":edited["revision"],
                      "temporary_workspace":str(self.workspace),"records":records,
                      "not_applicable":["product TeamRun","candidate","validation plan","Linux isolation","real model"],
                      "snapshot_use":"Evidence only. Reproduce with a fresh fixture, not as a production service database."}
            encoded = json.dumps(record,ensure_ascii=False,indent=2)
            audit = (args.output/"audit.log").read_text(encoding="utf-8")
            for secret in (PASSWORD,self.cookie.split("=",1)[1],self.csrf):
                self.assertNotIn(secret,encoded); self.assertNotIn(secret,audit)
            (args.output/"record.json").write_text(encoded+"\n",encoding="utf-8")

    suite = unittest.TestSuite([RoundTrip("test_round_trip")])
    with (args.output/"test.log").open("x",encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    files = {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(args.output.iterdir()) if path.is_file()}
    report = {"passed":result.wasSuccessful(),"ran":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
              "skipped":len(result.skipped),"artifacts":files}
    (args.output/"index.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key!="artifacts"}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
