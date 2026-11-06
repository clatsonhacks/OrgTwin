"""People & departments, cross-team requests with Slack/Discord notices, skill tools (MCP) and GitHub."""
import asyncio
import json
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["RUNNER"] = "local"
os.environ["STEP_DELAY_S"] = "0"
for k in ("VULTR_INFERENCE_API_KEY", "SLACK_WEBHOOK_URL", "DISCORD_WEBHOOK_URL", "GITHUB_TOKEN", "GITHUB_REPO", "APP_ACCESS_TOKEN"):
    os.environ.pop(k, None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app import llm, mcp  # noqa: E402
from app.config import settings  # noqa: E402
from app.connectors import registry  # noqa: E402
from app.db import Request, SessionLocal, User, Workflow, init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
_Real = httpx.AsyncClient


@pytest.fixture(autouse=True)
def no_people():
    """Other test files run in legacy (no people) mode: leave the DB that way."""
    def wipe():
        with SessionLocal() as s:
            s.execute(delete(User))
            s.execute(delete(Request))
            s.commit()
        mcp._save([])
    wipe()
    yield
    wipe()


def client():
    return _Real(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _org(c):
    org = (await c.post("/api/org/generate", json={
        "description": "Brightpath, 60 people: sales, finance, customer success, engineering, marketing"})).json()
    return {t["key"]: t for t in org["teams"]}


def _skill(team, role, name, steps, tools=None):
    with SessionLocal() as s:
        from app.main import _latest_org
        org = _latest_org(s)
        w = Workflow(org_id=org.id, team_key=team, role_key=role, name=name, goal=name, kind="skill",
                     steps=steps, meta={"tools": tools} if tools else {})
        s.add(w)
        s.commit()
        return w.id


async def _wait_request(c, rid):
    for _ in range(400):
        r = next(x for x in (await c.get("/api/requests")).json() if x["id"] == rid)
        if r["status"] in ("done", "failed", "no_skill"):
            return r
        for a in (await c.get("/api/approvals")).json():
            if a["status"] == "pending":
                pytest.fail(f"unexpected approval: {a['title']}")
        await asyncio.sleep(0.02)
    pytest.fail(f"request stuck: {r}")


def test_admin_departments_and_permissions():
    async def go():
        async with client() as c:
            teams = await _org(c)
            eng = "tech"
            role = teams[eng]["roles"][0]["key"]
            sales = "sales"
            # legacy mode: anyone can save a skill
            assert (await c.get("/api/me")).json()["setup_needed"] is True

            admin = (await c.post("/api/people/claim", json={"name": "Ada Admin"})).json()
            assert admin["role"] == "admin" and admin["token"].startswith("ot_")
            assert (await c.post("/api/people/claim", json={"name": "Eve"})).status_code == 409
            A = {"X-User-Token": admin["token"]}

            # nobody signed in: reading works, changes don't
            body = {"team_key": eng, "role_key": role, "name": "Weekly churn analysis",
                    "steps": [{"tool": "llm", "action": "write", "params": {"instruction": "Analyse: {{request}}", "save_as": "analysis"}}]}
            assert (await c.post("/api/skills", json=body)).status_code == 401
            assert (await c.get("/api/skills")).status_code == 200
            assert (await c.get("/api/people")).status_code == 401

            # admin invites someone from IT and ticks their department
            it = (await c.post("/api/people", headers=A, json={
                "name": "Ivan IT", "departments": [eng], "notify": {"slack": True, "discord": True, "slack_id": "U123"}})).json()
            assert it["invite_url"].endswith(it["token"]) and it["departments"] == [eng]
            bad = await c.post("/api/people", headers=A, json={"name": "X", "departments": ["nope"]})
            assert bad.status_code == 400
            I = {"X-User-Token": it["token"]}
            assert (await c.post("/api/people/join", json={"token": it["token"]})).json()["joined"] is True
            me = (await c.get("/api/me", headers=I)).json()
            assert me["user"]["name"] == "Ivan IT" and me["can_edit"] == [eng] and not me["admin"]

            # IT can create skills in IT only
            sk = (await c.post("/api/skills", headers=I, json=body)).json()
            assert sk["meta"]["created_by"] == "Ivan IT"
            other = {**body, "team_key": sales, "role_key": teams[sales]["roles"][0]["key"]}
            r = await c.post("/api/skills", headers=I, json=other)
            assert r.status_code == 403 and sales in r.json()["detail"]
            assert (await c.put(f"/api/workflows/{sk['id']}", headers=I, json={"steps": body["steps"], "name": "Churn v2"})).status_code == 200

            # members can't do admin things; the admin can add a second department
            assert (await c.post("/api/people", headers=I, json={"name": "Z"})).status_code == 403
            assert (await c.put(f"/api/people/{it['id']}", headers=I, json={"departments": [eng, sales]})).status_code == 403
            assert (await c.put(f"/api/people/{it['id']}", headers=A, json={"departments": [eng, sales]})).json()["departments"] == [eng, sales]
            assert (await c.post("/api/skills", headers=I, json=other)).status_code == 200
            # can't remove the last admin
            assert (await c.delete(f"/api/people/{admin['id']}", headers=A)).status_code == 400
            # a fresh link replaces the old one
            new = (await c.post(f"/api/people/{it['id']}/link", headers=A)).json()
            assert (await c.post("/api/people/join", json={"token": it["token"]})).status_code == 401
            assert (await c.post("/api/people/join", json={"token": new["token"]})).status_code == 200
    asyncio.run(go())


def test_sales_request_runs_the_it_skill_and_pings_it_in_slack_and_discord():
    async def go():
        async with client() as c:
            teams = await _org(c)
            eng = "tech"
            role = teams[eng]["roles"][0]["key"]
            admin = (await c.post("/api/people/claim", json={"name": "Ada"})).json()
            A = {"X-User-Token": admin["token"]}
            await c.post("/api/people", headers=A, json={"name": "Ivan IT", "departments": [eng],
                                                         "notify": {"slack": True, "discord": True, "slack_id": "U123", "discord_id": "999"}})
            sam = (await c.post("/api/people", headers=A, json={"name": "Sam Sales", "departments": ["sales"]})).json()
            sid = _skill(eng, role, "Churn analysis report", [
                {"title": "Analyse churn", "tool": "llm", "action": "write", "per_row": False, "needs_approval": False,
                 "params": {"instruction": "Write a churn analysis for: {{request}}", "save_as": "analysis"}}])
            req = (await c.post("/api/requests", headers={"X-User-Token": sam["token"]},
                                json={"text": "I need a churn analysis report for Q3 customers"})).json()
            assert req["workflow_id"] == sid and req["team_key"] == eng and req["from_name"] == "Sam Sales"
            assert req["from_team"] == "sales"
            done = await _wait_request(c, req["id"])
            assert done["status"] == "done" and "churn analysis" in done["result"].lower()
            chans = {n["channel"]: n for n in done["notified"]}
            assert set(chans) == {"slack", "discord"} and chans["slack"]["to"] == ["Ivan IT"]
            out = (await c.get("/api/outbox")).json()
            assert any("<@U123>" in m["text"] and "Sam Sales" in m["text"] for m in out["slack"])
            assert any(m["tool"] == "discord" and "<@999>" in m["summary"] for m in out["messages"])

            # nothing fits → the likely department is asked to make a skill
            nope = (await c.post("/api/requests", headers={"X-User-Token": sam["token"]},
                                 json={"text": "zzz qqq xyzzy"})).json()
            assert nope["status"] == "no_skill" and nope["notified"]

            quests = {q["key"]: q["done"] for q in (await c.get("/api/quests")).json()}
            assert quests["org"] and quests["admin"] and quests["invite"] and quests["assign"] and quests["skill"]
            assert quests["request"] and quests["notified"] and not quests["tools"]
    asyncio.run(go())


def _fake_mcp(calls):
    def handler(req: httpx.Request):
        if req.url.host != "mcp.example.com":
            return httpx.Response(404)
        msg = json.loads(req.content)
        calls.append(msg["method"])
        if msg["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}},
                                  headers={"Mcp-Session-Id": "s1"})
        if msg["method"] == "notifications/initialized":
            return httpx.Response(202)
        assert req.headers["mcp-session-id"] == "s1" and req.headers["authorization"] == "Bearer secret"
        if msg["method"] == "tools/list":
            sse = "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "query_tickets", "description": "Count support tickets", "annotations": {"readOnlyHint": True},
                 "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}}}},
                {"name": "close_ticket", "description": "Close a ticket", "inputSchema": {"type": "object"}}]}}) + "\n\n"
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        if msg["method"] == "tools/call":
            topic = msg["params"]["arguments"].get("topic")
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": f"42 open tickets about {topic}"}]}})
        return httpx.Response(400)
    return handler


def test_skill_tools_mcp_agent(monkeypatch):
    calls: list = []
    handler = _fake_mcp(calls)

    def make(*a, **kw):
        if "transport" not in kw:
            kw["transport"] = httpx.MockTransport(handler)
        return _Real(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", make)
    monkeypatch.setattr(settings, "mcp_allow_private", True)
    replies = iter(['{"tool": "mcp__helpdesk__query_tickets", "args": {"topic": "login"}}',
                    '{"final": "There are 42 open login tickets; most are password resets."}'])
    seen = []

    async def fake_complete(system, user, fallback, temperature=0.4, max_tokens=4000, json_mode=False):
        seen.append(system)
        return llm.LLMResult(next(replies), mocked=False, model="m")

    async def go():
        async with client() as c:
            teams = await _org(c)
            monkeypatch.setattr(settings, "inference_api_key", "k")  # pretend an AI is connected
            monkeypatch.setattr(llm, "complete", fake_complete)
            eng = "tech"
            role = teams[eng]["roles"][0]["key"]
            admin = (await c.post("/api/people/claim", json={"name": "Ada"})).json()
            A = {"X-User-Token": admin["token"]}
            srv = (await c.post("/api/mcp/servers", headers=A, json={
                "name": "Helpdesk", "url": "https://mcp.example.com/mcp", "token": "secret"})).json()
            assert srv["id"] == "helpdesk" and srv["has_token"] and "token" not in srv
            assert [t["name"] for t in srv["tools"]] == ["query_tickets", "close_ticket"] and srv["tools"][0]["read_only"]
            sid = _skill(eng, role, "Ticket insights", [
                {"title": "Look at the tickets", "tool": "agent", "action": "run", "per_row": False, "needs_approval": False,
                 "params": {"instruction": "Answer: {{request}}", "save_as": "answer"}}])
            assert (await c.put(f"/api/workflows/{sid}/tools", headers=A, json={"tools": ["mcp:nope"]})).status_code == 400
            w = (await c.put(f"/api/workflows/{sid}/tools", headers=A, json={"tools": ["mcp:helpdesk/query_tickets"]})).json()
            assert w["meta"]["tools"] == ["mcp:helpdesk/query_tickets"]
            req = (await c.post("/api/requests", headers=A, json={"text": "how many login tickets?", "workflow_id": sid})).json()
            done = await _wait_request(c, req["id"])
            assert done["status"] == "done" and "42 open login tickets" in done["result"]
            assert "mcp__helpdesk__query_tickets" in seen[0] and "close_ticket" not in seen[0]  # only granted tools
            assert calls.count("tools/call") == 1
            assert {q["key"]: q["done"] for q in (await c.get("/api/quests")).json()}["tools"]

            # a skill limited to GitHub cannot post to Discord
            bad = _skill(eng, role, "Limited", [{"title": "Post", "tool": "discord", "action": "post", "per_row": False,
                                                 "needs_approval": False, "params": {"text": "hi"}}], tools=["github"])
            r = (await c.post("/api/requests", headers=A, json={"text": "post it", "workflow_id": bad})).json()
            done = await _wait_request(c, r["id"])
            assert done["status"] == "failed" and "no access to: discord" in done["result"]
    asyncio.run(go())


def test_github_connector_mock_and_live(monkeypatch):
    async def go():
        res = await registry.run("github", "read_pr", {"number": "42", "save_as": "pr"})
        assert res["mode"] == "mock" and "CSV export" in res["text"]
        lst = await registry.run("github", "list_prs", {"state": "open"})
        assert "#41" in lst["text"]

        seen = []

        def handler(req):
            seen.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
            if req.url.path.endswith("/pulls/7"):
                return httpx.Response(200, json={"title": "Speed up search", "user": {"login": "dev"}, "state": "open", "body": "Why"})
            if req.url.path.endswith("/pulls/7/files"):
                return httpx.Response(200, json=[{"filename": "a.py", "additions": 3, "deletions": 1, "patch": "@@ +fast"}])
            if req.url.path.endswith("/reviews"):
                return httpx.Response(200, json={"html_url": "https://github.com/o/r/pull/7#r1"})
            return httpx.Response(404, json={})

        def make(*a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            return _Real(*a, **kw)
        monkeypatch.setattr(httpx, "AsyncClient", make)
        monkeypatch.setattr(settings, "github_token", "ghp_x")
        monkeypatch.setattr(settings, "github_repo", "o/r")
        pr = await registry.run("github", "read_pr", {"number": "#7"})
        assert pr["mode"] == "live" and "Speed up search" in pr["text"] and "@@ +fast" in pr["text"]
        rv = await registry.run("github", "review_pr", {"number": "7", "text": "LGTM", "event": "APPROVE"})
        assert rv["mode"] == "live" and seen[-1][2]["event"] == "COMMENT"  # agents never approve
    asyncio.run(go())
