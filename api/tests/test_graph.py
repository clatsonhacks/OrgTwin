"""Org knowledge graph + skill reuse when designing new workflows (demo mode)."""
import asyncio
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["RUNNER"] = "local"
os.environ["STEP_DELAY_S"] = "0"
for k in ("VULTR_INFERENCE_API_KEY", "STRIPE_SECRET_KEY", "SLACK_WEBHOOK_URL", "SMTP_HOST", "GOOGLE_SHEET_ID"):
    os.environ.pop(k, None)

import httpx  # noqa: E402

from app import graph, workflows  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()

CHASE = [
    {"title": "Read overdue invoices", "tool": "sheets", "action": "read_rows", "params": {"where": {"status": "Overdue"}}},
    {"title": "Email a payment reminder", "tool": "email", "action": "send", "per_row": True, "needs_approval": True,
     "params": {"to": "{{row.email}}", "subject": "Invoice reminder", "instruction": "Polite reminder"}},
    {"title": "Mark as Reminded", "tool": "sheets", "action": "update_row", "per_row": True,
     "params": {"column": "status", "value": "Reminded"}},
]


def test_tokens_stem():
    assert graph.tokens("Chasing the overdue invoices") == graph.tokens("chase overdue invoice")


def test_graph_and_reuse():
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/org/generate", json={"description": "Acme, a SaaS startup with sales and finance."})
            skill = (await c.post("/api/skills", json={
                "team_key": "finance", "role_key": "ar-specialist", "name": "Chase overdue invoices",
                "steps": CHASE, "meta": {"person": "ar-specialist#0", "person_name": "Hana L."}})).json()

            g = (await c.get("/api/graph")).json()
            ids = {n["id"] for n in g["nodes"]}
            assert {"org", "team:finance", "role:finance/ar-specialist", "person:finance/ar-specialist#0",
                    f"wf:{skill['id']}", "tool:email"} <= ids
            assert {"source": "person:finance/ar-specialist#0", "target": f"wf:{skill['id']}", "type": "owns"} in g["edges"]

            sug = (await c.get("/api/graph/suggest", params={
                "team": "finance", "role": "accountant", "goal": "Chase overdue invoices then post a summary on Slack"})).json()
            assert sug and sug[0]["id"] == skill["id"] and sug[0]["why"] == "same team"
            none = (await c.get("/api/graph/suggest", params={"team": "finance", "role": "accountant",
                                                              "goal": "Plan the team offsite"})).json()
            assert none == []

            wf = (await c.post("/api/workflows/generate", json={
                "team_key": "finance", "role_key": "accountant",
                "goal": "Chase overdue invoices then post a summary on Slack"})).json()
            assert wf["meta"]["uses_skills"] == [skill["id"]] and wf["executable"]
            reused = [s for s in wf["steps"] if s.get("from_skill")]
            assert [s["title"] for s in reused] == [s["title"] for s in CHASE]
            assert wf["steps"][0]["action"] == "read_rows"
            assert any(s["tool"] == "slack" for s in wf["steps"])  # the new part was added on top

            g = (await c.get("/api/graph")).json()
            assert {"source": f"wf:{wf['id']}", "target": f"wf:{skill['id']}", "type": "reuses"} in g["edges"]
    asyncio.run(go())


def test_expand_skill_refs_from_model_output():
    sk = {"id": 7, "name": "Chase", "steps": CHASE}
    steps, used = workflows.expand_skill_refs(
        [{"tool": "skill", "skill_id": "7"}, {"tool": "slack", "action": "post", "params": {"text": "done"}},
         {"tool": "skill", "skill_id": 99}], {7: sk})
    assert used == [7] and len(steps) == 4 and steps[0]["from_skill"] == {"id": 7, "name": "Chase"}
