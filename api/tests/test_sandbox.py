"""Containment checks for the Docker runner (skipped when no Docker daemon is available)."""
import asyncio
import os
import shutil
import subprocess

import pytest

os.environ["RUNNER"] = "docker"
from app import runner  # noqa: E402
from app.config import settings  # noqa: E402

docker_ok = shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
pytestmark = pytest.mark.skipif(not docker_ok, reason="no docker daemon")


def _run(code, payload=None):
    settings.runner = "docker"
    return asyncio.run(runner.run_python(code, payload or {}))


def test_no_network():
    r = _run("import socket,json\ntry:\n socket.create_connection(('1.1.1.1',80),timeout=3);print(json.dumps('ESCAPED'))\n"
             "except OSError as e:\n print(json.dumps('blocked'))")
    assert r["output"] == "blocked"


def test_read_only_and_no_secrets():
    r = _run("import os,json\ntry:\n open('/tmp/x','w');w='writable'\nexcept OSError:\n w='read-only'\n"
             "bad=('VULTR','STRIPE','SLACK','SMTP','GOOGLE','APP_ACCESS','DATABASE')\n"
             "print(json.dumps({'fs':w,'env':sorted(k for k in os.environ if k.startswith(bad))}))")
    assert r["output"] == {"fs": "read-only", "env": []}


def test_timeout_kills():
    settings.sandbox_timeout_s = 3
    try:
        r = _run("while True: pass")
    finally:
        settings.sandbox_timeout_s = 20
    assert r["ok"] is False and r["error"] == "timeout"
    left = subprocess.run(["docker", "ps", "-q", "--filter", "name=orgtwin-sbx-"], capture_output=True, text=True)
    assert left.stdout.strip() == "", "sandbox container survived the timeout"
