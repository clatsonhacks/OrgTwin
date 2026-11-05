"""End-to-end test of the API in demo mode (mock LLM + mock connectors, local runner)."""
import asyncio
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["RUNNER"] = os.environ.get("TEST_RUNNER", "local")
os.environ["STEP_DELAY_S"] = "0"
for k in ("VULTR_INFERENCE_API_KEY", "STRIPE_SECRET_KEY", "SLACK_WEBHOOK_URL", "SMTP_HOST", "GOOGLE_SHEET_ID"):
    os.environ.pop(k, None)

import httpx  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app import orggen  # noqa: E402

init_db()


async def _wait_status(c, run_id, want, timeout=15):
    for _ in range(int(timeout / 0.05)):
        r = (await c.get(f"/api/runs/{run_id}")).json()
        if r["status"] in want:
            return r
        await asyncio.sleep(0.05)
    raise AssertionError(f"run stuck in {r['status']}: {r['events'][-3:]}")


def test_lead_to_cash_end_to_end():
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            st = (await c.get("/api/status")).json()
            assert st["demo_mode"] is True

            org = (await c.post("/api/org/generate", json={
                "description": "We are Acme Analytics, a SaaS startup with sales, engineering and marketing."})).json()
            keys = {t["key"] for t in org["teams"]}
            assert {"sales", "finance", "support", "tech", "marketing"} <= keys
            assert org["source"] == "mock"

            run_id = (await c.post("/api/runs")).json()["run_id"]
            run = await _wait_status(c, run_id, {"waiting_approval", "failed"})
            assert run["status"] == "waiting_approval", run["events"][-1]

            # second run is refused while one is active
            assert (await c.post("/api/runs")).status_code == 409

            ap = (await c.get("/api/approvals")).json()[0]
            assert "Send invoice" in ap["title"]
            assert (await c.post(f"/api/approvals/{ap['id']}", json={"approved": True, "who": "test"})).status_code == 200

            run = await _wait_status(c, run_id, {"completed", "failed"})
            assert run["status"] == "completed", run["events"][-1]
            types = [e["type"] for e in run["events"]]
            assert types.count("handoff") == 2
            score = next(e for e in run["events"] if e["type"] == "step.completed" and e["payload"]["step"] == "score_lead")
            assert score["payload"]["result"]["output"]["tier"] in "ABC"

            box = (await c.get("/api/outbox")).json()
            assert len(box["emails"]) == 3  # outreach, invoice, onboarding
            m = (await c.get("/api/metrics")).json()
            assert m["runs_completed"] == 1 and m["hours_saved"] > 1

            wf = (await c.post("/api/workflows/generate", json={
                "team_key": "finance", "role_key": "ar-specialist", "goal": "Chase overdue invoices"})).json()
            assert wf["kind"] == "draft" and wf["executable"]
            assert [x["action"] for x in wf["steps"]][:2] == ["read_rows", "send"]
            assert wf["steps"][0]["params"]["where"] == {"status": "Overdue"}

            # hand the workflow to one specific person (desk) in the department
            org = (await c.get("/api/org")).json()
            role = next(r for t in org["teams"] if t["key"] == "finance" for r in t["roles"] if r["key"] == "ar-specialist")
            seat = f"ar-specialist#{role['headcount'] - 1}"
            out = (await c.put(f"/api/workflows/{wf['id']}/assign", json={
                "team_key": "finance", "role_key": "ar-specialist", "person": seat, "person_name": "Maya K."})).json()
            assert out["meta"]["person"] == seat and out["meta"]["person_name"] == "Maya K."
            bad = await c.put(f"/api/workflows/{wf['id']}/assign", json={
                "team_key": "finance", "role_key": "ar-specialist", "person": f"ar-specialist#{role['headcount']}"})
            assert bad.status_code == 400
            hero = next(w for w in (await c.get("/api/workflows")).json() if w["kind"] == "hero")
            moved = await c.put(f"/api/workflows/{hero['id']}/assign", json={
                "team_key": "finance", "role_key": "ar-specialist", "person": seat})
            assert moved.status_code == 400
    asyncio.run(go())


def test_rejection_stops_run():
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/org/generate", json={"description": "Small agency"})
            run_id = (await c.post("/api/runs")).json()["run_id"]
            await _wait_status(c, run_id, {"waiting_approval"})
            ap = (await c.get("/api/approvals")).json()[0]
            await c.post(f"/api/approvals/{ap['id']}", json={"approved": False})
            run = await _wait_status(c, run_id, {"rejected", "completed"})
            assert run["status"] == "rejected"
    asyncio.run(go())


def test_csv_org():
    teams = orggen.parse_csv("team,role,headcount,tasks,hours_per_week\nSales,SDR,3,Call leads;Update CRM,6\n"
                             "Sales,SDR,1,,\nIT Ops,Helpdesk,2,Reset passwords,8\n")
    org = orggen.normalize({"company": "X", "teams": teams})
    sales = next(t for t in org["teams"] if t["key"] == "sales")
    assert sales["roles"][0]["headcount"] == 4 and sales["automatable_hours"] == 24.0
    assert {"finance", "support"} <= {t["key"] for t in org["teams"]}
