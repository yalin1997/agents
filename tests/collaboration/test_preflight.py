"""Read-only preflight must not turn missing prerequisites into success."""
from __future__ import annotations

from pathlib import Path
import subprocess
import os
from tempfile import TemporaryDirectory
from unittest import TestCase

from pi_deepagent.collaboration.preflight import Preflight, ProbeResult


IMAGE = "sha256:" + "a" * 64
CONTEXT = "desktop-linux" if os.name == "nt" else "default"


class PreflightTests(TestCase):
    def make_probe(self, *, engine=True, os_type="linux", image=True):
        calls = []
        def probe(argv, timeout):
            calls.append(list(argv))
            self.assertEqual(timeout, 15)
            if argv[0] == "node":
                return ProbeResult(0, "v22.23.2")
            if "version" in argv:
                return ProbeResult(0 if engine else 1, '{"Client":{"Version":"28.1.1"},"Server":' +
                                   ('{"Version":"28.1.1","Os":"' + os_type + '","Arch":"amd64"}}' if engine else 'null}'))
            if "info" in argv:
                return ProbeResult(0, '{"OSType":"' + os_type + '","MemTotal":4294967296,"NCPU":4}')
            if "image" in argv:
                return ProbeResult(0 if image else 1, '[{"Id":"' + IMAGE + '","Os":"linux","Architecture":"amd64"}]')
            self.fail(f"Unexpected preflight operation: {argv}")
        return probe, calls

    def test_preflight_only_inspects_explicit_context_and_immutable_image(self):
        probe, calls = self.make_probe()
        result = Preflight(probe=probe, free_space=lambda _: 12 * 1024**3).inspect(Path.cwd(), image=IMAGE)
        self.assertTrue(result["eligible_for_sandbox_probe"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["engine"]["os"], "linux")
        self.assertEqual(result["image"]["id"], IMAGE)
        self.assertFalse(result["container_behavior_verified"])
        docker = [c for c in calls if c[0] == "docker"]
        self.assertEqual(len(docker), 3)
        self.assertTrue(all(c[1:3] == ["--context", CONTEXT] for c in docker))
        self.assertEqual(docker[-1], ["docker", "--context", CONTEXT, "image", "inspect", IMAGE])

    def test_engine_absent_does_not_start_install_or_query_another_engine(self):
        probe, calls = self.make_probe(engine=False)
        result = Preflight(probe=probe, free_space=lambda _: 12 * 1024**3).inspect(Path.cwd(), image=IMAGE)
        self.assertFalse(result["eligible_for_sandbox_probe"])
        self.assertIn("linux_engine_unavailable", result["blockers"])
        self.assertEqual([c for c in calls if c[0] == "docker"],
                         [["docker", "--context", CONTEXT, "version", "--format", "{{json .}}"]])

    def test_windows_engine_low_disk_and_missing_approved_image_are_blockers(self):
        probe, _ = self.make_probe(os_type="windows")
        result = Preflight(probe=probe, free_space=lambda _: 1024).inspect(Path.cwd())
        self.assertFalse(result["eligible_for_sandbox_probe"])
        self.assertEqual(set(result["blockers"]),
                         {"linux_engine_required", "disk_below_reserve", "approved_image_required"})

    def test_floating_or_injected_image_never_reaches_docker_inspect(self):
        for image in ("python:3.13", "--privileged", "repo@sha256:bad", "sha256:" + "z" * 64):
            with self.subTest(image=image):
                probe, calls = self.make_probe()
                result = Preflight(probe=probe, free_space=lambda _: 12 * 1024**3).inspect(Path.cwd(), image=image)
                self.assertIn("immutable_image_required", result["blockers"])
                self.assertFalse(any("image" in c for c in calls))

    def test_missing_image_timeout_invalid_json_and_missing_cli_fail_closed(self):
        probe, _ = self.make_probe(image=False)
        result = Preflight(probe=probe, free_space=lambda _: 12 * 1024**3).inspect(Path.cwd(), image=IMAGE)
        self.assertIn("approved_image_unavailable", result["blockers"])
        for failure in (subprocess.TimeoutExpired("docker", 15), FileNotFoundError("secret-host-path")):
            def broken(argv, timeout):
                raise failure
            result = Preflight(probe=broken, free_space=lambda _: 12 * 1024**3).inspect(Path.cwd(), image=IMAGE)
            self.assertIn("linux_engine_unavailable", result["blockers"])
            self.assertNotIn("secret-host-path", str(result))
        result = Preflight(probe=lambda *args: ProbeResult(0, "not JSON with secret"),
                           free_space=lambda _: 12 * 1024**3).inspect(Path.cwd(), image=IMAGE)
        self.assertIn("linux_engine_unavailable", result["blockers"])
        self.assertNotIn("not JSON with secret", str(result))

    def test_output_does_not_contain_unselected_engine_fields(self):
        probe, _ = self.make_probe()
        def contaminated(argv, timeout):
            result = probe(argv, timeout)
            if "version" in argv and argv[0] == "docker":
                return ProbeResult(0, result.stdout[:-1] + ',"ProxyPassword":"SEEDED-SECRET"}')
            return result
        result = Preflight(probe=contaminated, free_space=lambda _: 12 * 1024**3).inspect(Path.cwd(), image=IMAGE)
        self.assertTrue(result["eligible_for_sandbox_probe"])
        self.assertNotIn("SEEDED-SECRET", str(result))
        self.assertNotIn("ProxyPassword", str(result))
