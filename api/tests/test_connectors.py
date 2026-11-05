"""Connector registry: catalog, SSRF guard, live calls (with a fake HTTP layer), planner and a full run."""
import asyncio
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["RUNNER"] = "local"
os.environ["STEP_DELAY_S"] = "0"
for k in ("VULTR_INFERENCE_API_KEY", "STRIPE_SECRET_KEY", "SLACK_WEBHOOK_URL", "SMTP_HOST", "GOOGLE_SHEET_ID",
          "DISCORD_WEBHOOK_URL", "TEAMS_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "NOTION_TOKEN", "HUBSPOT_TOKEN", "WEBHOOK_ENABLED",
          "TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY"):
    os.environ.pop(k, None)

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import workflows  # noqa: E402
from app.config import settings  # noqa: E402
from app.connectors import registry  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()


def run(coro):
    return asyncio.run(coro)


def test_catalog_is_all_mock_without_credentials():
    cat = {c["key"]: c for c in registry.catalog()}
    assert {"discord", "teams", "telegram", "notion", "hubspot", "stripe", "webhook"} <= set(cat)
    assert all(c["mode"] == "mock" for k, c in cat.items() if k != "web")
    assert cat["web"]["mode"] == "basic"  # web search works without a key (DuckDuckGo + Wikipedia)
    assert cat["hubspot"]["actions"][0]["params"][0] == {"name": "email", "default": "{{row.email}}", "label": "email", "long": False}


@pytest.mark.parametrize("url", [
    "http://example.com/hook",            # plain http is off by default
    "ftp://8.8.8.8/x",
    "https://127.0.0.1/admin",
    "https://10.0.0.5/",
    "https://169.254.169.254/latest/meta-data",
    "https://[::1]/",
    "https://localhost/",
])
def test_ssrf_guard_rejects(url):
    with pytest.raises(registry.UnsafeURL):
        run(registry.check_url(url))


def test_ssrf_guard_allows_public_and_respects_allowlist(monkeypatch):
    assert run(registry.check_url("https://8.8.8.8/hook")) == "https://8.8.8.8/hook"
    monkeypatch.setattr(settings, "webhook_allowed_hosts", "hooks.zapier.com")
    with pytest.raises(registry.UnsafeURL):
        run(registry.check_url("https://8.8.8.8/hook"))


def test_live_discord_teams_hubspot(monkeypatch):
    calls = []

    async def fake_post(url, body, headers=None, method="POST"):
        calls.append((method, url, body))
        if "hubapi" in url and method == "POST":
            return httpx.Response(409, json={"message": "exists"})
        return httpx.Response(200, json={"id": "42"})

    monkeypatch.setattr(registry, "_post_json", fake_post)
    monkeypatch.setattr(settings, "discord_webhook_url", "https://discord.com/api/webhooks/x")
    monkeypatch.setattr(settings, "teams_webhook_url", "https://example.webhook.office.com/x")
    monkeypatch.setattr(settings, "hubspot_token", "pat-xyz")

    d = run(registry.run("discord", "post", {"text": "hello"}))
    t = run(registry.run("teams", "post", {"text": "hi team"}))
    h = run(registry.run("hubspot", "upsert_contact", {"email": "a@b.co", "firstname": "Ann", "lastname": "", "company": "B",
                                                       "lifecyclestage": ""}))
    assert d["mode"] == t["mode"] == h["mode"] == "live"
    assert calls[0][2] == {"content": "hello", "username": "OrgTwin"}
    assert calls[1][2]["text"] == "hi team" and calls[1][2]["attachments"][0]["content"]["body"][0]["text"] == "hi team"
    assert calls[2][0] == "POST" and calls[3][0] == "PATCH" and calls[3][1].endswith("/a@b.co?idProperty=email")
    assert h["summary"] == "updated contact a@b.co"


def test_live_error_is_reported(monkeypatch):
    async def fake_post(url, body, headers=None, method="POST"):
        return httpx.Response(404, text="Unknown Webhook")
    monkeypatch.setattr(registry, "_post_json", fake_post)
    monkeypatch.setattr(settings, "discord_webhook_url", "https://discord.com/api/webhooks/x")
    with pytest.raises(RuntimeError, match="HTTP 404"):
        run(registry.run("discord", "post", {"text": "x"}))


def test_stripe_amounts_and_mock_invoice():
    out = run(registry.run("stripe", "invoice", {"email": "a@b.co", "name": "A", "amount": "$1,250.50", "description": "Setup"}))
    assert out["mode"] == "mock" and out["detail"]["amount_due"] == 125050
    with pytest.raises(RuntimeError):
        run(registry.run("stripe", "invoice", {"email": "a@b.co", "name": "A", "amount": "lots", "description": "x"}))


def test_normalize_connector_steps():
    s = workflows.normalize_step({"tool": "msteams", "title": "Tell the team", "params": {}})
    assert s["tool"] == "teams" and s["action"] == "post" and s["params"] == {"text": "Tell the team"}
    h = workflows.normalize_step({"tool": "hubspot", "per_row": True})
    assert h["params"]["email"] == "{{row.email}}" and h["needs_approval"] is False
    st = workflows.normalize_step({"tool": "invoice", "per_row": True})
    assert st["tool"] == "stripe" and st["needs_approval"] is True
    assert workflows.is_executable([workflows.normalize_step({"tool": "sheets"}), h])


def test_planner_uses_new_connectors():
    steps = workflows.plan_from_text("Read the new leads in the spreadsheet, add them to HubSpot, "
                                     "write a summary and post it on Discord and Microsoft Teams")
    tools = [s["tool"] for s in steps]
    assert tools[0] == "sheets" and {"hubspot", "discord", "teams"} <= set(tools) and "slack" not in tools
    assert workflows.is_executable(steps)


def test_api_end_to_end_run_with_connectors():
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await c.post("/api/org/generate", json={"description": "Acme, SaaS with sales and finance"})
            cons = (await c.get("/api/connectors")).json()
            assert {"sheets", "email", "slack", "discord", "webhook"} <= {x["key"] for x in cons}
            t = (await c.post("/api/connectors/discord/test", json={})).json()
            assert t["ok"] and t["mode"] == "mock"
            bad = await c.post("/api/connectors/webhook/test", json={"url": "https://127.0.0.1/x"})
            assert bad.status_code == 400

            wf = (await c.post("/api/workflows/generate", json={
                "team_key": "sales", "role_key": "sdr",
                "goal": "Read new leads, sync them to HubSpot, write a summary and post it on Telegram"})).json()
            assert wf["executable"], wf["steps"]
            run_id = (await c.post(f"/api/workflows/{wf['id']}/run")).json()["run_id"]
            for _ in range(300):
                r = (await c.get(f"/api/runs/{run_id}")).json()
                if r["status"] == "waiting_approval":
                    ap = [a for a in (await c.get("/api/approvals")).json() if a["status"] == "pending"][0]
                    await c.post(f"/api/approvals/{ap['id']}", json={"approved": True})
                if r["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.02)
            assert r["status"] == "completed", r["events"][-1]
            msgs = (await c.get("/api/outbox")).json()["messages"]
            assert any(m["tool"] == "telegram" for m in msgs) and any(m["tool"] == "hubspot" for m in msgs)
    asyncio.run(go())
