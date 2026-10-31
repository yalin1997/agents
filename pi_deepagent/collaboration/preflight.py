"""Read-only, reproducible prerequisites probe. Never starts or installs anything.

Run: python -m pi_deepagent.collaboration.preflight --output evidence.json
An eligible result permits the next sandbox probe, not a claim of verified
container behavior or a completed collaboration acceptance gate.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess


GIB = 1024**3
IMMUTABLE_IMAGE = re.compile(r"(?:[a-zA-Z0-9][a-zA-Z0-9._:/-]*@)?sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class ProbeResult:
    returncode: int
    stdout: str


def _probe(argv, timeout):
    # Do not pass model credentials or the entire host environment to helpers.
    names = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE",
             "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMDATA", "HOME"}
    environment = {k: v for k, v in os.environ.items() if k.upper() in names}
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, shell=False, env=environment)
    # stderr and arbitrary fields are intentionally not published as evidence.
    return ProbeResult(result.returncode, result.stdout)


class Preflight:
    def __init__(self, *, probe=_probe, free_space=lambda path: shutil.disk_usage(path).free):
        self.probe = probe
        self.free_space = free_space

    def inspect(self, data_root: Path, *, image=None):
        context = "desktop-linux" if os.name == "nt" else "default"
        commands = []
        blockers = []
        def query(args):
            argv = ["docker", "--context", context, *args]
            entry = {"argv": argv}
            commands.append(entry)
            try:
                result = self.probe(argv, 15)
                entry["returncode"] = result.returncode
                if result.returncode != 0:
                    entry["status"] = "failed"
                    return None
                value = json.loads(result.stdout)
                entry["status"] = "known"
                return value
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                entry.update(status="unavailable", error_kind=type(exc).__name__)
                return None

        root = Path(data_root).resolve(strict=True)
        free = self.free_space(root)
        if free < 8 * GIB:
            blockers.append("disk_below_reserve")
        versions = {}
        for package in ("deepagents", "langgraph", "langgraph-checkpoint", "langchain", "uvicorn"):
            try:
                versions[package] = version(package)
            except PackageNotFoundError:
                versions[package] = None
                blockers.append("package_missing:" + package)
        engine = {}
        value = query(["version", "--format", "{{json .}}"])
        server = value.get("Server") if isinstance(value, dict) else None
        if not isinstance(server, dict):
            blockers.append("linux_engine_unavailable")
        else:
            engine = {"version": server.get("Version"), "os": server.get("Os"), "architecture": server.get("Arch")}
            if server.get("Os") != "linux":
                blockers.append("linux_engine_required")
            info = query(["info", "--format", "{{json .}}"])
            if isinstance(info, dict) and isinstance(info.get("MemTotal"), int) and isinstance(info.get("NCPU"), int):
                engine.update(memory_bytes=info["MemTotal"], logical_cpus=info["NCPU"])
                if info["MemTotal"] < 2 * GIB or info["NCPU"] < 1:
                    blockers.append("engine_resources_insufficient")
            else:
                blockers.append("engine_resources_unknown")
        inspected_image = None
        if image is None:
            blockers.append("approved_image_required")
        elif not isinstance(image, str) or not IMMUTABLE_IMAGE.fullmatch(image):
            blockers.append("immutable_image_required")
        elif engine.get("os") == "linux":
            inspected = query(["image", "inspect", image])
            if isinstance(inspected, list) and len(inspected) == 1 and isinstance(inspected[0], dict):
                item = inspected[0]
                inspected_image = {"reference": image, "id": item.get("Id"), "os": item.get("Os"),
                                   "architecture": item.get("Architecture")}
                if item.get("Os") != "linux" or not IMMUTABLE_IMAGE.fullmatch(str(item.get("Id", ""))):
                    blockers.append("approved_image_invalid")
            else:
                blockers.append("approved_image_unavailable")
        try:
            node = self.probe(["node", "--version"], 15)
            node_version = node.stdout.strip() if node.returncode == 0 and re.fullmatch(r"v\d+\.\d+\.\d+\s*", node.stdout) else None
        except (OSError, subprocess.TimeoutExpired):
            node_version = None
        if node_version is None:
            blockers.append("host_node_unavailable")
        return {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.system(), "python": platform.python_version(), "host_node": node_version,
            "packages": versions, "docker_context": context, "engine": engine, "image": inspected_image,
            "host_disk_free_bytes": free, "host_disk_reserve_bytes": 8 * GIB,
            "blockers": blockers, "eligible_for_sandbox_probe": not blockers,
            "container_behavior_verified": False, "gpu_model_probed": False, "commands": commands,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path.cwd())
    parser.add_argument("--image", help="Previously approved immutable image reference; inspection only")
    parser.add_argument("--output", type=Path, help="New evidence file; existing files are never overwritten")
    args = parser.parse_args(argv)
    result = Preflight().inspect(args.data_root, image=args.image)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    print(encoded, end="")
    return 0 if result["eligible_for_sandbox_probe"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
