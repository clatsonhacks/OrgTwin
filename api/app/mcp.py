"""MCP (Model Context Protocol) servers that skills can use as tools.

The admin adds a server (name + Streamable-HTTP URL + optional bearer token) once. Its tools are listed
with `tools/list`; a skill owner then ticks which servers / tools a skill may use, and the skill's
"AI agent" step calls them with `tools/call`.

Transport: MCP Streamable HTTP (JSON-RPC 2.0 over POST; the reply is JSON or a short SSE stream).
Only public addresses are allowed unless MCP_ALLOW_PRIVATE=true (for a server on your own machine).
"""
from __future__ import annotations

import json
import re
import secrets
import time

import httpx

from . import userconfig
from .config import settings
from .connectors.registry import UnsafeURL, check_url

PROTOCOL = "2025-06-18"


class MCPError(RuntimeError):
    pass


# ---------------- stored servers ----------------

def servers() -> list[dict]:
    return userconfig._get("mcp_servers", None) or []


def _save(items: list[dict]) -> None:
    userconfig._put("mcp_servers", items)


def get(server_id: str) -> dict | None:
    return next((x for x in servers() if x["id"] == server_id), None)


def public(srv: dict) -> dict:
    """What the browser may see (never the token)."""
    return {"id": srv["id"], "name": srv["name"], "url": srv["url"], "has_token": bool(srv.get("token")),
            "tools": srv.get("tools", []), "checked_at": srv.get("checked_at"), "error": srv.get("error", "")}


async def add(name: str, url: str, token: str = "") -> dict:
    await _check(url)
    sid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "server"
    if get(sid):
        sid = f"{sid}-{secrets.token_hex(2)}"
    srv = {"id": sid, "name": name.strip()[:60], "url": url.strip(), "token": token.strip(), "tools": []}
    srv = await refresh_server(srv)
    _save(servers() + [srv])
    return srv


async def refresh(server_id: str) -> dict:
    srv = get(server_id)
    if not srv:
        raise MCPError("unknown MCP server")
    srv = await refresh_server(srv)
    _save([srv if x["id"] == server_id else x for x in servers()])
    return srv


async def refresh_server(srv: dict) -> dict:
    try:
        tools = await list_tools(srv)
        return {**srv, "tools": tools, "checked_at": time.time(), "error": ""}
    except (MCPError, httpx.HTTPError, UnsafeURL) as e:
        return {**srv, "checked_at": time.time(), "error": str(e)[:300]}


def remove(server_id: str) -> None:
    _save([x for x in servers() if x["id"] != server_id])


async def _check(url: str) -> None:
    if settings.mcp_allow_private:
        if not url.startswith(("http://", "https://")):
            raise UnsafeURL("URL must be http(s)://host/…")
        return
    if url.startswith("http://") and not settings.webhook_allow_http:
        raise UnsafeURL("use an https:// MCP URL (or set MCP_ALLOW_PRIVATE=true for a local server)")
    await check_url(url)


# ---------------- the protocol ----------------

class _Session:
    def __init__(self, srv: dict):
        self.srv, self.sid, self.n = srv, "", 0

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": PROTOCOL}
        if self.srv.get("token"):
            h["Authorization"] = f"Bearer {self.srv['token']}"
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        return h

    async def call(self, c: httpx.AsyncClient, method: str, params: dict | None = None, notify: bool = False):
        body: dict = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            self.n += 1
            body["id"] = self.n
        r = await c.post(self.srv["url"], json=body, headers=self._headers())
        if r.status_code in (401, 403):
            raise MCPError(f"{self.srv['name']} refused the request (HTTP {r.status_code}) — check its token")
        if r.status_code >= 400:
            raise MCPError(f"{self.srv['name']} returned HTTP {r.status_code}: {r.text[:200]}")
        self.sid = r.headers.get("mcp-session-id", self.sid)
        if notify:
            return None
        msg = _parse(r, body["id"])
        if "error" in msg:
            raise MCPError(f"{self.srv['name']}: {msg['error'].get('message', msg['error'])}")
        return msg.get("result", {})

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=60, follow_redirects=False)
        await self.call(self.client, "initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                    "clientInfo": {"name": "OrgTwin", "version": "0.1"}})
        await self.call(self.client, "notifications/initialized", notify=True)
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()


def _parse(r: httpx.Response, want_id: int) -> dict:
    ctype = r.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for block in r.text.split("\n\n"):
            data = "\n".join(line[5:].strip() for line in block.splitlines() if line.startswith("data:"))
            if not data:
                continue
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
        raise MCPError("no reply in the event stream")
    try:
        return r.json()
    except ValueError as e:
        raise MCPError("the server did not reply with JSON") from e


async def list_tools(srv: dict) -> list[dict]:
    await _check(srv["url"])
    async with _Session(srv) as sess:
        res = await sess.call(sess.client, "tools/list", {})
    return [{"name": t["name"], "description": (t.get("description") or "")[:300],
             "input_schema": t.get("inputSchema") or {"type": "object"},
             "read_only": bool((t.get("annotations") or {}).get("readOnlyHint"))} for t in res.get("tools", [])][:100]


async def call_tool(server_id: str, tool: str, args: dict) -> str:
    srv = get(server_id)
    if not srv:
        raise MCPError(f"MCP server {server_id!r} is not set up any more")
    await _check(srv["url"])
    async with _Session(srv) as sess:
        res = await sess.call(sess.client, "tools/call", {"name": tool, "arguments": args})
    parts = []
    for c in res.get("content", []):
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
        elif c.get("type") == "resource":
            parts.append(str((c.get("resource") or {}).get("text", ""))[:4000])
        else:
            parts.append(f"[{c.get('type')} content]")
    if res.get("structuredContent") and not parts:
        parts.append(json.dumps(res["structuredContent"])[:8000])
    text = "\n".join(parts)[:12000]
    if res.get("isError"):
        raise MCPError(f"{tool} failed: {text[:300]}")
    return text
