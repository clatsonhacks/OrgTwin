"""Connect connectors from the app: sign-in popups (Google, Stripe) and the short forms."""
import asyncio
import base64
import json
import os
import tempfile
from urllib.parse import parse_qs, urlparse

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["RUNNER"] = "local"
os.environ["STEP_DELAY_S"] = "0"
for k in ("GOOGLE_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "SMTP_HOST", "STRIPE_SECRET_KEY", "DISCORD_WEBHOOK_URL",
          "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "STRIPE_CONNECT_CLIENT_ID", "STRIPE_CONNECT_SECRET_KEY",
          "GITHUB_TOKEN", "GITHUB_REPO"):
    os.environ.pop(k, None)

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import oauth, userconfig  # noqa: E402
from app.config import settings  # noqa: E402
from app.connectors import email, sheets  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)
_RealAsyncClient = httpx.AsyncClient


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for p in ("google", "stripe", "notion", "slack", "discord", "hubspot", "telegram"):
        userconfig.clear_oauth(p)
    for k in userconfig.CONNECTOR_SETUP:
        userconfig.clear_connector(k)
    yield
    for p in ("google", "stripe", "notion", "slack", "discord", "hubspot", "telegram"):
        userconfig.clear_oauth(p)
    for k in userconfig.CONNECTOR_SETUP:
        userconfig.clear_connector(k)


def _fake_http(monkeypatch, routes):
    """Route httpx calls by URL prefix to canned JSON responses."""
    def handler(req: httpx.Request):
        for prefix, fn in routes.items():
            if str(req.url).startswith(prefix):
                status, body = fn(req)
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={})
    def make(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", make)


def test_signin_needs_the_admin_oauth_app():
    r = client.post("/api/oauth/google/start")
    assert r.status_code == 400 and "GOOGLE_OAUTH_CLIENT_ID" in r.json()["detail"]
    st = client.get("/api/setup/connectors/sheets").json()
    assert st["signin"] == "google" and st["signin_available"] is False and st["fields"]  # form fallback


def test_google_popup_connects_sheets_and_gmail(monkeypatch):
    monkeypatch.setattr(settings, "google_oauth_client_id", "cid")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "csecret")
    url = client.post("/api/oauth/google/start").json()["url"]
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["cid"] and "gmail.send" in q["scope"][0] and "spreadsheets" in q["scope"][0]
    assert q["redirect_uri"][0].endswith("/api/oauth/google/callback") and q["access_type"] == ["offline"]

    claims = base64.urlsafe_b64encode(json.dumps({"email": "pat@example.com"}).encode()).decode().rstrip("=")
    _fake_http(monkeypatch, {oauth.GOOGLE_TOKEN: lambda req: (200, {
        "access_token": "at", "refresh_token": "rt", "expires_in": 3600, "id_token": f"x.{claims}.y",
        "scope": " ".join(oauth.GOOGLE_SCOPES)})})
    page = client.get("/api/oauth/google/callback", params={"code": "c", "state": q["state"][0]}).text
    assert '"ok": true' in page and "pat@example.com" in page

    assert settings.google_signed_in() and settings.google_account == "pat@example.com"
    assert email.mode() == "gmail"
    st = client.get("/api/setup/connectors/sheets").json()
    assert st["account"] == "pat@example.com" and st["needs_sheet"] is True and sheets.mode() == "mock"

    # the same state can't be replayed
    page = client.get("/api/oauth/google/callback", params={"code": "c", "state": q["state"][0]}).text
    assert '"ok": false' in page and "expired" in page

    # picking a sheet by pasting its link finishes Sheets
    monkeypatch.setattr(sheets, "columns", lambda: _async(["id", "name", "email"]))
    r = client.put("/api/setup/connectors/sheets",
                   json={"values": {"GOOGLE_SHEET_ID": "https://docs.google.com/spreadsheets/d/abc123/edit#gid=0"}}).json()
    assert settings.sheet_id == "abc123" and sheets.mode() == "google" and r["ok"]

    client.delete("/api/setup/connectors/email")
    assert not settings.google_signed_in() and email.mode() == "mock"


async def _async(v):
    return v


def test_denied_access_says_so():
    page = client.get("/api/oauth/stripe/callback", params={"error": "access_denied"}).text
    assert "Access was not allowed" in page and '"ok": false' in page


def test_stripe_connect_is_test_mode_only(monkeypatch):
    monkeypatch.setattr(settings, "stripe_connect_client_id", "ca_1")
    monkeypatch.setattr(settings, "stripe_connect_secret_key", "sk_test_platform")
    state = parse_qs(urlparse(client.post("/api/oauth/stripe/start").json()["url"]).query)["state"][0]
    _fake_http(monkeypatch, {oauth.STRIPE_TOKEN: lambda req: (200, {"access_token": "sk_live_x", "livemode": True})})
    page = client.get("/api/oauth/stripe/callback", params={"code": "c", "state": state}).text
    assert "test mode" in page and '"ok": false' in page and not settings.stripe_secret_key

    state = parse_qs(urlparse(client.post("/api/oauth/stripe/start").json()["url"]).query)["state"][0]
    _fake_http(monkeypatch, {
        oauth.STRIPE_TOKEN: lambda req: (200, {"access_token": "sk_test_user", "livemode": False, "stripe_user_id": "acct_1"}),
        "https://api.stripe.com/v1/account": lambda req: (200, {"settings": {"dashboard": {"display_name": "Acme"}}})})
    page = client.get("/api/oauth/stripe/callback", params={"code": "c", "state": state}).text
    assert '"ok": true' in page and "Acme" in page
    assert settings.stripe_secret_key == "sk_test_user" and settings.modes()["stripe"] == "stripe-test"
    listed = {c["key"]: c for c in client.get("/api/connectors").json()}
    assert listed["stripe"]["account"] == "Acme" and listed["stripe"]["mode"] == "live"


def test_form_saves_masks_and_clears():
    r = client.put("/api/setup/connectors/discord",
                   json={"values": {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/secretvalue"}}).json()
    assert settings.discord_webhook_url.endswith("secretvalue")
    f = r["setup"]["fields"][0]
    assert f["set"] and f["value"] == "" and f["hint"].startswith("••••") and "secretvalue" not in json.dumps(r["setup"])
    client.delete("/api/setup/connectors/discord")
    assert settings.discord_webhook_url == ""


def test_setup_is_protected_by_the_access_token(monkeypatch):
    monkeypatch.setattr(settings, "app_access_token", "tok")
    assert client.put("/api/setup/connectors/discord", json={"values": {}}).status_code == 401
    assert client.get("/api/auth/check").status_code == 401
    assert client.get("/api/auth/check", headers={"X-Access-Token": "tok"}).status_code == 200


def test_settings_survive_a_restart():
    userconfig.save_connector("telegram", {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "-1"})
    settings.telegram_bot_token = ""
    userconfig.load()
    assert settings.telegram_bot_token == "123:abc"
    asyncio.run(asyncio.sleep(0))


def _signin(monkeypatch, provider, routes, id_attr, secret_attr):
    monkeypatch.setattr(settings, id_attr, "cid")
    monkeypatch.setattr(settings, secret_attr, "csecret")
    url = client.post(f"/api/oauth/{provider}/start").json()["url"]
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["cid"] and q["redirect_uri"][0].endswith(f"/api/oauth/{provider}/callback")
    _fake_http(monkeypatch, routes)
    return q, client.get(f"/api/oauth/{provider}/callback", params={"code": "c", "state": q["state"][0]}).text


def test_slack_popup_gives_a_channel_webhook(monkeypatch):
    q, page = _signin(monkeypatch, "slack", {oauth.SLACK_TOKEN: lambda req: (200, {
        "ok": True, "team": {"name": "Brightpath"},
        "incoming_webhook": {"url": "https://hooks.slack.com/services/T/B/x", "channel": "#sales"}})},
        "slack_client_id", "slack_client_secret")
    assert q["scope"] == ["incoming-webhook"] and '"ok": true' in page
    assert settings.slack_webhook_url.endswith("/x") and settings.modes()["slack"] == "webhook"
    listed = {c["key"]: c for c in client.get("/api/connectors").json()}
    assert listed["slack"]["account"] == "#sales · Brightpath" and listed["slack"]["mode"] == "live"


def test_discord_popup_gives_a_channel_webhook(monkeypatch):
    q, page = _signin(monkeypatch, "discord", {oauth.DISCORD_TOKEN: lambda req: (200, {
        "webhook": {"url": "https://discord.com/api/webhooks/1/tok", "name": "OrgTwin", "channel_id": "42"}})},
        "discord_client_id", "discord_client_secret")
    assert q["scope"] == ["webhook.incoming"] and '"ok": true' in page
    assert settings.discord_webhook_url.endswith("/tok")


def test_notion_popup_then_pick_a_database(monkeypatch):
    dbs = [{"object": "database", "id": "db1", "title": [{"plain_text": "Leads"}], "url": "u1"},
           {"object": "database", "id": "db2", "title": [{"plain_text": "Notes"}], "url": "u2"}]

    def search(req):
        kind = json.loads(req.content)["filter"]["value"]
        return 200, {"results": dbs if kind == "database" else []}
    q, page = _signin(monkeypatch, "notion", {
        oauth.NOTION_TOKEN: lambda req: (200, {"access_token": "ntn_1", "workspace_name": "Brightpath HQ"}),
        "https://api.notion.com/v1/search": search,
        "https://api.notion.com/v1/databases/db2": lambda req: (200, {"title": [{"plain_text": "Notes"}]})},
        "notion_oauth_client_id", "notion_oauth_client_secret")
    assert q["owner"] == ["user"] and '"ok": true' in page and "pick the database" in page
    st = client.get("/api/setup/connectors/notion").json()
    assert st["account"] == "Brightpath HQ" and st["needs_database"]
    assert [d["title"] for d in client.get("/api/oauth/notion/databases").json()["databases"]] == ["Leads", "Notes"]
    r = client.post("/api/oauth/notion/database", json={"database_id": "db2"}).json()
    assert r["ok"] and "Notes" in r["message"] and settings.notion_database_id == "db2"


def test_hubspot_token_refreshes_itself(monkeypatch):
    calls = []

    def token(req):
        body = parse_qs(req.content.decode())
        calls.append(body["grant_type"][0])
        return 200, {"access_token": f"at{len(calls)}", "refresh_token": "rt", "expires_in": 1800}
    _, page = _signin(monkeypatch, "hubspot", {
        oauth.HUBSPOT_TOKEN: token,
        "https://api.hubapi.com/oauth/v1/access-tokens/": lambda req: (200, {"hub_domain": "brightpath.com"})},
        "hubspot_client_id", "hubspot_client_secret")
    assert '"ok": true' in page and "brightpath.com" in page and settings.hubspot_token == "at1"
    assert asyncio.run(oauth.hubspot_token()) == "at1"           # still fresh
    st = userconfig.oauth_state()["hubspot"]
    userconfig.save_oauth("hubspot", {**st, "expires_at": 0})    # expire it
    assert asyncio.run(oauth.hubspot_token()) == "at2" and calls == ["authorization_code", "refresh_token"]


def test_telegram_one_tap_finds_the_chat(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "123:abc")
    oauth._tg_offset = 0
    state = {}

    def updates(req):
        return 200, {"result": [{"update_id": 7, "message": {"text": "hi", "chat": {"id": 1}}},
                                {"update_id": 8, "message": {"text": f"/start {state['code']}",
                                                             "chat": {"id": -100, "title": "Sales team"}}}]}
    _fake_http(monkeypatch, {
        "https://api.telegram.org/bot123:abc/getMe": lambda req: (200, {"result": {"username": "OrgTwinBot"}}),
        "https://api.telegram.org/bot123:abc/getUpdates": updates,
        "https://api.telegram.org/bot123:abc/sendMessage": lambda req: (200, {"ok": True})})
    st = client.post("/api/connect/telegram/start").json()
    state["code"] = st["code"]
    assert st["url"] == f"https://t.me/OrgTwinBot?startgroup={st['code']}"
    r = client.get("/api/connect/telegram/check", params={"code": st["code"]}).json()
    assert r["connected"] and r["account"] == "Sales team" and settings.telegram_chat_id == "-100"
    assert client.get("/api/connect/telegram/check", params={"code": "nope"}).status_code == 400
