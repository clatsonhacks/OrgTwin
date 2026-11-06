"""Cross-team workflows, web search, JSON planning and the recorder's noise filter."""
import asyncio
import json
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["RUNNER"] = "local"
os.environ["STEP_DELAY_S"] = "0"
for k in ("VULTR_INFERENCE_API_KEY", "STRIPE_SECRET_KEY", "SLACK_WEBHOOK_URL", "SMTP_HOST", "GOOGLE_SHEET_ID",
          "TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO"):
    os.environ.pop(k, None)

import httpx  # noqa: E402

from app import llm, skills, workflows  # noqa: E402
from app.config import settings  # noqa: E402
from app.connectors import registry  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()

TEAMS = [
    {"key": "sales", "name": "Sales", "roles": [{"key": "sdr", "name": "SDR", "tasks": []}]},
    {"key": "finance", "name": "Finance", "roles": [{"key": "ar", "name": "AR Specialist", "tasks": []}]},
    {"key": "support", "name": "Customer Success", "roles": [{"key": "csm", "name": "CSM", "tasks": []}]},
]


def _step(tool, action, team=None, **params):
    s = {"title": f"{tool} {action}", "tool": tool, "action": action, "params": params}
    if team:
        s["team"] = team
    return workflows.normalize_step(s)


def test_link_teams_inserts_and_cleans_handoffs():
    steps = [
        _step("llm", "write", "sales", instruction="qualify"),
        _step("llm", "write", "Finance", instruction="price it"),      # team by name → handoff inserted
        _step("a2a", "handoff", "finance", to="finance"),              # to the same team → dropped
        _step("a2a", "handoff", "finance", to="customer success"),     # by name → kept, sender = finance
        _step("slack", "post", "support", text="done"),                # already on support: no extra handoff
        _step("llm", "write", "nonsense", instruction="x"),            # unknown team → stays on support
    ]
    out = workflows.link_teams(steps, TEAMS)
    assert [(s["tool"], s["team"]) for s in out] == [
        ("llm", "sales"), ("a2a", "sales"), ("llm", "finance"), ("a2a", "finance"), ("slack", "support"), ("llm", "support")]
    assert out[1]["params"]["to"] == "finance" and out[3]["params"]["to"] == "support"
    assert workflows.is_executable(out)


def test_offline_flow_plan_assigns_departments():
    steps = workflows.plan_flow_offline(
        "Sales reads the Won leads in the sheet, then Finance emails each one an invoice reminder, "
        "then Customer Success posts a summary to Slack", TEAMS)
    teams = [s["team"] for s in steps if s["tool"] != "a2a"]
    assert teams[0] == "sales" and "finance" in teams and teams[-1] == "support"
    assert sum(s["tool"] == "a2a" for s in steps) == 2
    assert workflows.is_executable(steps)


def test_web_search_step_saves_results_for_later_steps(monkeypatch):
    async def fake_search(query, n):
        return [{"title": "Northwind Robotics raises Series B", "url": "https://news.example/nw", "snippet": "40 staff"}], "basic"
    monkeypatch.setattr(registry, "_search", fake_search)
    res = asyncio.run(registry.run("web", "search", {"query": "Northwind Robotics", "max_results": "3", "save_as": "search"}))
    assert res["save_as"] == "search" and "Series B" in res["text"] and res["mode"] == "basic"


def test_read_page_refuses_private_addresses():
    try:
        asyncio.run(registry.run("web", "read_page", {"url": "https://127.0.0.1/admin", "save_as": "page"}))
    except registry.UnsafeURL:
        return
    raise AssertionError("private address was fetched")


def test_complete_json_retries_then_parses(monkeypatch):
    calls = []

    async def fake_complete(system, user, fallback, temperature=0.4, max_tokens=4000, json_mode=False):
        calls.append(json_mode)
        text = "Let me think about this first..." if len(calls) == 1 else '{"name": "X", "steps": []}'
        return llm.LLMResult(text, mocked=False, model="m")
    monkeypatch.setattr(settings, "inference_api_key", "k")
    monkeypatch.setattr(llm, "complete", fake_complete)
    data, res, problem = asyncio.run(llm.complete_json("s", "u", fallback=lambda: None))
    assert problem is None and data == {"name": "X", "steps": []} and calls == [True, True]


def test_recorder_ignores_its_own_ui_and_plans_web_search(monkeypatch):
    async def fake_describe(prompt, images, max_tokens=1500):
        return llm.LLMResult(
            'Frame 1: OrgTwin (localhost:3000) — recording a skill; transcript reads "I open the leads sheet, take the rows marked New"\n'
            "Frame 2: recorder\n"
            "Frame 3: Google Chrome — types 'warehouse robotics market size' into Google search", mocked=False, model="vl")
    monkeypatch.setattr(llm, "describe_images", fake_describe)
    out = asyncio.run(skills.analyze(TEAMS[0], TEAMS[0]["roles"][0], "", ["data:image/jpeg;base64,AA"] * 3, ["name", "email"]))
    assert out["meta"]["observations"] == ["Frame 3: Google Chrome — types 'warehouse robotics market size' into Google search"]
    tools = [s["tool"] for s in out["steps"]]
    assert "web" in tools and "sheets" not in tools and "email" not in tools
    assert out["meta"]["planner"].startswith("rule-based fallback (no inference key)")


def test_design_and_run_a_cross_team_flow(monkeypatch):
    async def fake_search(query, n):
        return [{"title": "Result", "url": "https://x.example", "snippet": "found"}], "basic"
    monkeypatch.setattr(registry, "_search", fake_search)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            org = (await c.post("/api/org/generate", json={"description": "Acme: sales, finance and support"})).json()
            keys = [t["key"] for t in org["teams"]]
            assert {"sales", "finance", "support"} <= set(keys)
            wf = (await c.post("/api/flows/design", json={
                "goal": "Sales searches the web for warehouse robotics news, then Finance writes a budget note, "
                        "then Customer Success posts a summary to Slack"})).json()
            assert wf["kind"] == "flow" and wf["executable"] and wf["teams"][:1] == ["sales"]
            assert {"finance", "support"} <= set(wf["teams"])
            ideas = (await c.get("/api/flows/suggest")).json()
            assert ideas["offline"] and ideas["ideas"]

            run_id = (await c.post(f"/api/workflows/{wf['id']}/run")).json()["run_id"]
            for _ in range(500):
                run = (await c.get(f"/api/runs/{run_id}")).json()
                if run["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.02)
            assert run["status"] == "completed", run["events"][-1]
            ev = run["events"]
            handoffs = [(e["payload"]["from"], e["payload"]["to"]) for e in ev if e["type"] == "handoff"]
            assert handoffs == [("sales", "finance"), ("finance", "support")]
            started = [e["payload"]["team"] for e in ev if e["type"] == "step.started"]
            assert started[0] == "sales" and started[-1] == "support"
            assert "Sales → Finance → Customer Success" in ev[-1]["payload"]["result"]

            # editing keeps handoffs consistent: move Customer Success's steps to Finance → that handoff goes away
            steps = [{**s, "team": "finance"} if s.get("team") == "support" else s for s in wf["steps"]]
            upd = (await c.put(f"/api/workflows/{wf['id']}", json={"steps": steps})).json()
            assert "support" not in upd["teams"]
            assert sum(s["tool"] == "a2a" for s in upd["steps"]) == 1
    asyncio.run(go())
