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
          "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "STRIPE_CONNECT_CLIENT_ID", "STRIPE_CONNECT_SECRET_KEY"):
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
    for p in ("google", "stripe"):
        userconfig.clear_oauth(p)
    for k in userconfig.CONNECTOR_SETUP:
        userconfig.clear_connector(k)
    yield
    for p in ("google", "stripe"):
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
