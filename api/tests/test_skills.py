"""Skill Recorder: demonstration -> executable workflow -> real run with approval (demo mode)."""
import asyncio

import httpx

import tests.test_flow  # noqa: F401  (sets env + creates the test DB)
from app import llm, workflows
from app.connectors import email, sheets
from app.main import app

NARRATION = ("I open the leads spreadsheet and look at the rows where status is New. For each one I write "
             "a short follow up email and send it, then I mark the row as contacted. Finally I tell the team on Slack.")


def test_planner_from_narration():
    steps = workflows.plan_from_text(NARRATION)
    acts = [(s["tool"], s["action"]) for s in steps]
    assert acts[:3] == [("sheets", "read_rows"), ("email", "send"), ("sheets", "update_row")]
    assert steps[0]["params"]["where"] == {"status": "New"}
    assert steps[1]["per_row"] and steps[1]["needs_approval"] and steps[1]["params"]["to"] == "{{row.email}}"
    assert steps[2]["params"]["value"] == "Contacted"
    assert ("slack", "post") in acts
    assert workflows.is_executable(steps)


def test_normalize_rejects_unrunnable():
    steps = workflows.normalize([{"tool": "gmail", "per_row": True, "params": {"to": "{{row.email}}"}}])
    assert steps[0]["tool"] == "email"
    assert not workflows.is_executable(steps)  # per-row step without a read_rows before it


def test_record_save_run():
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/org/generate", json={"description": "We are Acme, sales and finance teams"})
            sheets.reset_mock()
            email.outbox.clear()
            a = (await c.post("/api/skills/analyze", json={
                "team_key": "sales", "role_key": "sdr", "transcript": NARRATION,
                "frames": ["data:image/jpeg;base64,AAAA"] * 3})).json()
            assert a["executable"] and a["meta"]["how"] == "narration only"
            assert a["meta"]["frames_received"] == 3 and "frames" not in a["meta"]

            saved = (await c.post("/api/skills", json={"team_key": "sales", "role_key": "sdr", "name": a["name"],
                                                       "steps": a["steps"], "meta": {**a["meta"], "frames": ["x"]}})).json()
            assert saved["kind"] == "skill" and saved["executable"] and "frames" not in saved["meta"]
            assert [w["id"] for w in (await c.get("/api/skills")).json()] == [saved["id"]]

            prev = (await c.get(f"/api/workflows/{saved['id']}/preview")).json()
            assert len(prev["rows"]) == 3

            run_id = (await c.post(f"/api/workflows/{saved['id']}/run")).json()["run_id"]
            for _ in range(300):
                if (await c.get(f"/api/runs/{run_id}")).json()["status"] == "waiting_approval":
                    break
                await asyncio.sleep(0.02)
            ap = (await c.get("/api/approvals")).json()[0]
            assert "3 row" in ap["title"] and len(ap["payload"]["rows"]) == 3
            await c.post(f"/api/approvals/{ap['id']}", json={"approved": True})
            for _ in range(300):
                run = (await c.get(f"/api/runs/{run_id}")).json()
                if run["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.02)
            assert run["status"] == "completed", run["events"][-1]
            assert len(email.outbox) == 3 and {m["to"] for m in email.outbox} == {"dana@example.com", "sam@example.com", "priya@example.com"}
            assert all(r["status"] == "Contacted" for r in await sheets.list_leads() if r["id"] in ("L-101", "L-102", "L-103"))
            assert run["context"]["hours_saved"] > 0
    asyncio.run(go())


def test_vision_observations_drive_the_plan(monkeypatch):
    async def fake_describe(prompt, images, max_tokens=1500):
        return llm.LLMResult("Frame 1: Google Sheets — 'Invoices' sheet, columns name, email, status; filtered status = Overdue\n"
                             "Frame 2: Gmail — composing a payment reminder email to the first row's email",
                             mocked=False, model="test-vl-model")
    monkeypatch.setattr(llm, "describe_images", fake_describe)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/org/generate", json={"description": "We are Acme, sales and finance"})
            a = (await c.post("/api/skills/analyze", json={"team_key": "finance", "role_key": "ar-specialist",
                                                           "frames": ["data:image/jpeg;base64,AAAA"] * 2})).json()
            assert a["meta"]["how"] == "vision only" and a["meta"]["vision_model"] == "test-vl-model"
            assert len(a["meta"]["observations"]) == 2
            assert a["steps"][0]["params"]["where"] == {"status": "Overdue"}
            assert "payment reminder" in a["steps"][1]["params"]["instruction"]
    asyncio.run(go())
