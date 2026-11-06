"""Run untrusted / generated code in an isolated, throwaway container.

docker mode: no network, read-only FS, capped memory/CPU/PIDs, all capabilities dropped,
container removed after every run, no secrets passed in. Input goes via stdin, output via stdout.
local mode: plain subprocess — for development only, NOT isolated (the result says so).
"""
import asyncio
import json
import os
import sys
import time
import uuid

from .config import settings


async def run_python(code: str, payload: dict) -> dict:
    started = time.monotonic()
    name = f"orgtwin-sbx-{uuid.uuid4().hex[:12]}"
    if settings.runner == "docker":
        cmd = ["docker", "run", "--rm", "-i", "--name", name, "--network", "none", "--read-only",
               "--memory", settings.sandbox_memory, "--cpus", "0.5", "--pids-limit", "64",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--user", "65534:65534", settings.sandbox_image, "python", "-I", "-c", code]
        isolation = {"type": "docker", "network": "none", "memory": settings.sandbox_memory,
                     "cpus": 0.5, "read_only": True, "secrets": "none"}
    else:
        cmd = [sys.executable, "-I", "-c", code]
        isolation = {"type": "local", "warning": "not isolated (dev mode)"}
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
    if sys.platform == "win32":
        # Windows Python can't initialise without SYSTEMROOT (needed for its random-number source).
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env)
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()),
                                          timeout=settings.sandbox_timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        if settings.runner == "docker":
            # Killing the docker CLI does not stop the container; destroy it explicitly.
            killer = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await killer.wait()
        return {"ok": False, "error": "timeout", "isolation": isolation,
                "duration_ms": int((time.monotonic() - started) * 1000)}
    result = {"ok": proc.returncode == 0, "exit_code": proc.returncode, "isolation": isolation,
              "duration_ms": int((time.monotonic() - started) * 1000),
              "stderr": err.decode(errors="replace")[-2000:]}
    try:
        result["output"] = json.loads(out.decode() or "null")
    except json.JSONDecodeError:
        result["output"] = out.decode(errors="replace")[-4000:]
    return result
