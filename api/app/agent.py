"""Tool access for skills, and the "AI agent" step that uses it.

A skill's `meta.tools` lists what it may use:
  "github"                  every action of a connector (github, notion, slack, discord, web, …)
  "mcp:<server>"            every tool of an MCP server the admin added
  "mcp:<server>/<tool>"     one MCP tool
When a skill has a tools list, its steps may only use those tools (plus the always-on basics:
spreadsheet rows, AI writing, approvals and handoffs). The `agent/run` step lets the model pick and
call the granted tools itself until the task is done. Anything that sends or changes something
outside OrgTwin (an external connector action, or an MCP tool that is not marked read-only) waits
for a person's approval the first time it is used in a run.
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

from . import llm, mcp
from .connectors import registry

ALWAYS = {"sheets", "llm", "approval", "a2a", "agent"}   # basics every skill may use
GRANTABLE_CORE = {"email": ("Email", "✉️"), "slack": ("Slack", "💬")}
MAX_CALLS = 8


def catalog() -> dict:
    """Everything a skill can be given, for the tool picker."""
    conns = [{"id": k, "name": n, "icon": i, "kind": "connector", "actions": [{"key": "send" if k == "email" else "post",
                                                                                "label": "send" if k == "email" else "post", "external": True}]}
             for k, (n, i) in GRANTABLE_CORE.items()]
    conns += [{"id": c.key, "name": c.name, "icon": c.icon, "kind": "connector", "mode": c.mode(),
               "actions": [{"key": a.key, "label": a.label, "external": a.external} for a in c.actions.values()]}
              for c in registry.CONNECTORS.values()]
    servers = [{"id": f"mcp:{s['id']}", "name": s["name"], "icon": "🧩", "kind": "mcp", "error": s.get("error", ""),
                "tools": [{"id": f"mcp:{s['id']}/{t['name']}", "name": t["name"], "description": t["description"],
                           "read_only": t["read_only"]} for t in s.get("tools", [])]} for s in mcp.servers()]
    return {"connectors": conns, "mcp": servers, "always": sorted(ALWAYS)}


def valid_grants(grants: list[str]) -> list[str]:
    known = {c["id"] for c in catalog()["connectors"]}
    servers = {s["id"]: s for s in mcp.servers()}
    out = []
    for g in grants:
        g = str(g).strip()
        if g in known:
            out.append(g)
        elif g.startswith("mcp:"):
            sid, _, tool = g[4:].partition("/")
            if sid in servers and (not tool or any(t["name"] == tool for t in servers[sid].get("tools", []))):
                out.append(g)
            else:
                raise ValueError(f"unknown MCP server or tool: {g}")
        else:
            raise ValueError(f"unknown tool: {g}")
    return list(dict.fromkeys(out))


def step_allowed(tool: str, grants: list[str] | None) -> bool:
    if not grants:  # no tools list = unrestricted (skills made before tool access existed)
        return True
    return tool in ALWAYS or tool in grants


# ---------------- the agent loop ----------------

def _tools_for(grants: list[str]) -> list[dict]:
    """Callable tools: {name, description, params, needs_ok, run}."""
    out: list[dict] = []
    for g in grants:
        if g.startswith("mcp:"):
            sid, _, only = g[4:].partition("/")
            srv = mcp.get(sid)
            for t in (srv or {}).get("tools", []):
                if only and t["name"] != only:
                    continue
                out.append({"name": f"mcp__{sid}__{t['name']}"[:64], "description": t["description"] or t["name"],
                            "params": t["input_schema"], "needs_ok": not t["read_only"],
                            "run": (lambda a, sid=sid, tn=t["name"]: mcp.call_tool(sid, tn, a))})
        elif g in registry.CONNECTORS:
            c = registry.CONNECTORS[g]
            for a in c.actions.values():
                out.append({"name": f"{g}__{a.key}", "description": f"{c.name}: {a.doc}",
                            "params": {"type": "object", "properties": {p.name: {"type": "string", "description": p.label}
                                                                        for p in a.params}},
                            "needs_ok": a.external,
                            "run": (lambda args, g=g, k=a.key: _connector(g, k, args))})
    return out


async def _connector(tool: str, action: str, args: dict) -> str:
    params = registry.normalize_params(tool, action, {k: str(v) for k, v in (args or {}).items()})
    res = await registry.run(tool, action, params)
    return str(res.get("text") or res.get("summary") or json.dumps(res, default=str))[:8000]


def _prompt(team_name: str, tools: list[dict]) -> str:
    lines = [f"- {t['name']}: {t['description'][:200]}\n  args JSON schema: {json.dumps(t['params'])[:600]}" for t in tools]
    return (f"You are the {team_name} agent. Complete the task using the tools below when they help.\n"
            "Reply with ONLY one JSON object, no prose:\n"
            '  {"tool": "<tool name>", "args": {...}}   to call a tool, or\n'
            '  {"final": "<your answer for the person who asked>"}   when you are done.\n'
            "Call one tool at a time; you will see each result. Never invent results.\n\nTools:\n"
            + ("\n".join(lines) if lines else "(none — answer directly)"))


async def run(instruction: str, data: dict, team_name: str, grants: list[str],
              approve: Callable[[str, dict], Awaitable[bool]], on_call: Callable[[dict], None] | None = None) -> dict:
    """Run the tool loop. Returns {"text", "calls": [...], "mocked", "model"}."""
    tools = _tools_for(grants)
    by_name = {t["name"]: t for t in tools}
    system = _prompt(team_name, tools)
    history: list[str] = []
    calls: list[dict] = []
    ok_tools: set[str] = set()
    model, mocked = "mock", True
    for _ in range(MAX_CALLS + 1):
        user = (f"Task: {instruction}\nData: {json.dumps(data, default=str)[:8000]}\n\n"
                + ("Progress so far:\n" + "\n".join(history)[-12000:] if history else ""))
        names = ", ".join(by_name) or "none"
        res = await llm.complete(system, user, fallback=lambda: json.dumps(
            {"final": f"(demo mode) I would do this with: {names}. Task: {instruction}"}), json_mode=True)
        model, mocked = res.model, res.mocked
        try:
            msg = llm.extract_json(res.text)
        except (ValueError, json.JSONDecodeError):
            return {"text": res.text.strip(), "calls": calls, "mocked": mocked, "model": model}
        if not isinstance(msg, dict) or "tool" not in msg or len(calls) >= MAX_CALLS:
            final = msg.get("final") if isinstance(msg, dict) else None
            return {"text": str(final if final is not None else res.text).strip(), "calls": calls, "mocked": mocked, "model": model}
        name, args = str(msg.get("tool")), msg.get("args") if isinstance(msg.get("args"), dict) else {}
        t = by_name.get(name)
        if not t:
            history.append(f"- {name}: ERROR this skill has no tool called {name!r}. Use only the listed tools.")
            continue
        if t["needs_ok"] and name not in ok_tools:
            if not await approve(name, args):
                calls.append({"tool": name, "args": args, "result": "rejected by a person"})
                history.append(f"- {name}({json.dumps(args)[:300]}): a person REJECTED this call. Do not retry it.")
                continue
            ok_tools.add(name)
        try:
            out = await t["run"](args)
            calls.append({"tool": name, "args": args, "result": out[:500]})
            history.append(f"- {name}({json.dumps(args)[:300]}) →\n{out[:3000]}")
        except Exception as e:  # report to the model so it can adapt
            calls.append({"tool": name, "args": args, "error": f"{type(e).__name__}: {e}"[:300]})
            history.append(f"- {name}: ERROR {type(e).__name__}: {str(e)[:300]}")
        if on_call:
            on_call(calls[-1])
    return {"text": "Stopped after too many tool calls.", "calls": calls, "mocked": mocked, "model": model}
