"""One-click "Connect": a sign-in popup from the app itself that asks the user to allow access.

Flow (OAuth 2.0 authorization code):
  1. The app calls POST /api/oauth/{provider}/start and opens the returned URL in a popup.
  2. The user signs in at Google / Stripe / Notion / Slack / Discord / HubSpot and clicks Allow.
  3. The provider redirects the popup to GET /api/oauth/{provider}/callback; we swap the code for a
     token, store it (userconfig) and the popup tells the app "connected" and closes itself.

Telegram has no OAuth. It gets a deep link instead: the user taps "Open Telegram", presses Start on
OrgTwin's bot, and the bot learns which chat to post to.

The admin registers OrgTwin once with each service (client id + secret in .env). No user ever copies
a key. Stripe stays test-mode only, like the rest of OrgTwin.
"""
from __future__ import annotations

import base64
import html
import json
import secrets
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Awaitable, Callable
from urllib.parse import urlencode

import httpx

from . import userconfig
from .config import settings

GOOGLE_SCOPES = ["openid", "email",
                 "https://www.googleapis.com/auth/spreadsheets",
                 "https://www.googleapis.com/auth/gmail.send"]
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
STRIPE_TOKEN = "https://connect.stripe.com/oauth/token"
NOTION_TOKEN = "https://api.notion.com/v1/oauth/token"
SLACK_TOKEN = "https://slack.com/api/oauth.v2.access"
DISCORD_TOKEN = "https://discord.com/api/oauth2/token"
HUBSPOT_TOKEN = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_SCOPES = "crm.objects.contacts.read crm.objects.contacts.write"


class OAuthError(RuntimeError):
    pass


@dataclass
class Provider:
    key: str
    name: str
    id_attr: str            # settings attribute with the client id
    secret_attr: str        # settings attribute with the client secret
    env: tuple[str, str]    # their .env names (shown to the admin)
    authorize: Callable[[str, str], str]           # (redirect_uri, state) -> URL
    exchange: Callable[[httpx.AsyncClient, str], Awaitable[str]]  # (client, code) -> message


def _cid(p: str) -> str:
    return getattr(settings, PROVIDERS[p].id_attr)


def _csecret(p: str) -> str:
    return getattr(settings, PROVIDERS[p].secret_attr)


def redirect_uri(provider: str) -> str:
    return f"{settings.public_api_url.rstrip('/')}/api/oauth/{provider}/callback"


def _url(base: str, q: dict) -> str:
    return f"{base}?{urlencode(q)}"


# ---------------- Google: Sheets + Gmail ----------------

def _google_auth(ru: str, state: str) -> str:
    return _url("https://accounts.google.com/o/oauth2/v2/auth", {
        "client_id": _cid("google"), "redirect_uri": ru, "response_type": "code", "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state})


async def _google_exchange(c: httpx.AsyncClient, code: str) -> str:
    r = await c.post(GOOGLE_TOKEN, data={"code": code, "client_id": _cid("google"), "client_secret": _csecret("google"),
                                         "redirect_uri": redirect_uri("google"), "grant_type": "authorization_code"})
    if r.status_code >= 400:
        raise OAuthError(f"Google did not accept the sign-in (HTTP {r.status_code})")
    tok = r.json()
    refresh = tok.get("refresh_token") or userconfig.oauth_state().get("google", {}).get("refresh_token", "")
    if not refresh:
        raise OAuthError("Google did not give offline access — remove OrgTwin at myaccount.google.com/permissions and try again")
    email = _jwt_claims(tok["id_token"]).get("email", "") if tok.get("id_token") else ""
    granted = tok.get("scope", "")
    userconfig.save_oauth("google", {"refresh_token": refresh, "email": email, "name": email, "scopes": granted})
    _cache["google"] = (tok.get("access_token", ""), time.time() + int(tok.get("expires_in", 0)) - 60)
    missing = [n for s, n in ((GOOGLE_SCOPES[2], "Sheets"), (GOOGLE_SCOPES[3], "Gmail")) if s not in granted]
    if missing:
        return f"Connected {email or 'Google'}, but {' and '.join(missing)} access was not allowed."
    return f"Connected {email or 'your Google account'} — Sheets and Gmail are ready."


# ---------------- Stripe Connect (test mode) ----------------

def _stripe_auth(ru: str, state: str) -> str:
    return _url("https://connect.stripe.com/oauth/authorize", {
        "response_type": "code", "client_id": _cid("stripe"), "scope": "read_write", "redirect_uri": ru, "state": state})


async def _stripe_exchange(c: httpx.AsyncClient, code: str) -> str:
    r = await c.post(STRIPE_TOKEN, data={"grant_type": "authorization_code", "code": code}, auth=(_csecret("stripe"), ""))
    if r.status_code >= 400:
        raise OAuthError(f"Stripe did not accept the sign-in: {r.json().get('error_description', r.status_code)}")
    tok = r.json()
    if tok.get("livemode") or not str(tok.get("access_token", "")).startswith("sk_test_"):
        raise OAuthError("OrgTwin only uses Stripe in test mode — connect with test mode on")
    name = tok.get("stripe_user_id", "")
    a = await c.get("https://api.stripe.com/v1/account", auth=(tok["access_token"], ""))
    if a.status_code < 400:
        acc = a.json()
        name = ((acc.get("settings") or {}).get("dashboard", {}).get("display_name")
                or (acc.get("business_profile") or {}).get("name") or acc.get("email") or name)
    userconfig.save_oauth("stripe", {"access_token": tok["access_token"], "account": tok.get("stripe_user_id", ""), "name": name})
    return f"Connected Stripe ({name}, test mode) — agents can send invoices now."


# ---------------- Notion (public integration) ----------------

def _notion_auth(ru: str, state: str) -> str:
    return _url("https://api.notion.com/v1/oauth/authorize", {
        "client_id": _cid("notion"), "response_type": "code", "owner": "user", "redirect_uri": ru, "state": state})


async def _notion_exchange(c: httpx.AsyncClient, code: str) -> str:
    r = await c.post(NOTION_TOKEN, auth=(_cid("notion"), _csecret("notion")),
                     json={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri("notion")})
    if r.status_code >= 400:
        raise OAuthError(f"Notion did not accept the sign-in (HTTP {r.status_code})")
    tok = r.json()
    name = tok.get("workspace_name") or "Notion workspace"
    userconfig.save_oauth("notion", {"access_token": tok["access_token"], "name": name,
                                     "workspace_id": tok.get("workspace_id", "")})
    # the user can pick a database during the consent screen (Notion's "duplicate template" or page picker)
    dbs = await notion_databases()
    if len(dbs) == 1:
        userconfig.save_connector("notion", {"NOTION_DATABASE_ID": dbs[0]["id"]})
        return f"Connected {name} — agents will add pages to “{dbs[0]['title']}”."
    return f"Connected {name}. Now pick the database agents should write to."


# ---------------- Slack (incoming webhook via OAuth; Slack shows a channel picker) ----------------

def _slack_auth(ru: str, state: str) -> str:
    return _url("https://slack.com/oauth/v2/authorize", {
        "client_id": _cid("slack"), "scope": "incoming-webhook", "redirect_uri": ru, "state": state})


async def _slack_exchange(c: httpx.AsyncClient, code: str) -> str:
    r = await c.post(SLACK_TOKEN, data={"client_id": _cid("slack"), "client_secret": _csecret("slack"),
                                        "code": code, "redirect_uri": redirect_uri("slack")})
    tok = r.json() if r.status_code < 400 else {}
    hook = tok.get("incoming_webhook") or {}
    if not tok.get("ok") or not hook.get("url"):
        raise OAuthError(f"Slack did not accept the sign-in ({tok.get('error', r.status_code)})")
    name = f"{hook.get('channel', '#channel')} · {(tok.get('team') or {}).get('name', 'Slack')}"
    userconfig.save_oauth("slack", {"webhook_url": hook["url"], "name": name})
    return f"Connected Slack — agents will post in {name}."


# ---------------- Discord (incoming webhook via OAuth; Discord shows a server + channel picker) ----------------

def _discord_auth(ru: str, state: str) -> str:
    return _url("https://discord.com/oauth2/authorize", {
        "client_id": _cid("discord"), "response_type": "code", "scope": "webhook.incoming", "redirect_uri": ru,
        "state": state})


async def _discord_exchange(c: httpx.AsyncClient, code: str) -> str:
    r = await c.post(DISCORD_TOKEN, data={"client_id": _cid("discord"), "client_secret": _csecret("discord"),
                                          "grant_type": "authorization_code", "code": code,
                                          "redirect_uri": redirect_uri("discord")})
    hook = (r.json() if r.status_code < 400 else {}).get("webhook") or {}
    if not hook.get("url"):
        raise OAuthError(f"Discord did not accept the sign-in (HTTP {r.status_code})")
    name = hook.get("name") or "Discord channel"
    userconfig.save_oauth("discord", {"webhook_url": hook["url"], "name": f"#{hook.get('channel_id', '')} · {name}".strip(" ·#")})
    return "Connected Discord — agents will post in the channel you picked."


# ---------------- HubSpot (short-lived access tokens + refresh token) ----------------

def _hubspot_auth(ru: str, state: str) -> str:
    return _url("https://app.hubspot.com/oauth/authorize", {
        "client_id": _cid("hubspot"), "redirect_uri": ru, "scope": HUBSPOT_SCOPES, "state": state})


async def _hubspot_exchange(c: httpx.AsyncClient, code: str) -> str:
    r = await c.post(HUBSPOT_TOKEN, data={"grant_type": "authorization_code", "client_id": _cid("hubspot"),
                                          "client_secret": _csecret("hubspot"), "redirect_uri": redirect_uri("hubspot"),
                                          "code": code})
    if r.status_code >= 400:
        raise OAuthError(f"HubSpot did not accept the sign-in (HTTP {r.status_code})")
    tok = r.json()
    name = "HubSpot portal"
    info = await c.get(f"https://api.hubapi.com/oauth/v1/access-tokens/{tok['access_token']}")
    if info.status_code < 400:
        i = info.json()
        name = i.get("hub_domain") or i.get("user") or name
    userconfig.save_oauth("hubspot", {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", ""),
                                      "expires_at": time.time() + int(tok.get("expires_in", 1800)) - 60, "name": name})
    return f"Connected HubSpot ({name}) — agents can create and update contacts."


async def hubspot_token() -> str:
    """A valid HubSpot token: the one from .env/the form, or the signed-in one (refreshed when it expires)."""
    st = userconfig.oauth_state().get("hubspot")
    if not st:
        return settings.hubspot_token
    if time.time() < st.get("expires_at", 0):
        return st["access_token"]
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(HUBSPOT_TOKEN, data={"grant_type": "refresh_token", "client_id": _cid("hubspot"),
                                              "client_secret": _csecret("hubspot"), "refresh_token": st["refresh_token"]})
    if r.status_code >= 400:
        raise RuntimeError("HubSpot access expired — reconnect HubSpot in Connectors")
    tok = r.json()
    userconfig.save_oauth("hubspot", {**st, "access_token": tok["access_token"],
                                      "refresh_token": tok.get("refresh_token", st["refresh_token"]),
                                      "expires_at": time.time() + int(tok.get("expires_in", 1800)) - 60})
    return tok["access_token"]


PROVIDERS: dict[str, Provider] = {p.key: p for p in [
    Provider("google", "Google", "google_oauth_client_id", "google_oauth_client_secret",
             ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"), _google_auth, _google_exchange),
    Provider("stripe", "Stripe", "stripe_connect_client_id", "stripe_connect_secret_key",
             ("STRIPE_CONNECT_CLIENT_ID", "STRIPE_CONNECT_SECRET_KEY"), _stripe_auth, _stripe_exchange),
    Provider("notion", "Notion", "notion_oauth_client_id", "notion_oauth_client_secret",
             ("NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET"), _notion_auth, _notion_exchange),
    Provider("slack", "Slack", "slack_client_id", "slack_client_secret",
             ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"), _slack_auth, _slack_exchange),
    Provider("discord", "Discord", "discord_client_id", "discord_client_secret",
             ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET"), _discord_auth, _discord_exchange),
    Provider("hubspot", "HubSpot", "hubspot_client_id", "hubspot_client_secret",
             ("HUBSPOT_CLIENT_ID", "HUBSPOT_CLIENT_SECRET"), _hubspot_auth, _hubspot_exchange),
]}

# which sign-in each connector uses
CONNECTOR_SIGNIN = {"sheets": "google", "email": "google", "stripe": "stripe", "notion": "notion",
                    "slack": "slack", "discord": "discord", "hubspot": "hubspot", "telegram": "telegram"}


def available() -> dict:
    """Which sign-in popups the admin has enabled."""
    out = {k: bool(getattr(settings, p.id_attr) and getattr(settings, p.secret_attr)) for k, p in PROVIDERS.items()}
    out["telegram"] = bool(telegram_bot_token())
    return out


def missing_env(provider: str) -> str:
    if provider == "telegram":
        return "TELEGRAM_BOT_TOKEN"
    p = PROVIDERS.get(provider)
    return " and ".join(p.env) if p else ""


# ---------------- the popup flow ----------------

_pending: dict[str, dict] = {}   # state -> {provider, ts}
_STATE_TTL = 600


def start(provider: str) -> str:
    p = PROVIDERS.get(provider)
    if not p:
        raise OAuthError("unknown sign-in provider")
    if not available()[provider]:
        raise OAuthError(f"one-click sign-in is not enabled yet — the admin sets {missing_env(provider)} once in .env")
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["ts"] > _STATE_TTL]:
        _pending.pop(k, None)
    state = secrets.token_urlsafe(24)
    _pending[state] = {"provider": provider, "ts": now}
    return p.authorize(redirect_uri(provider), state)


def _take_state(provider: str, state: str) -> None:
    item = _pending.pop(state or "", None)
    if not item or item["provider"] != provider or time.time() - item["ts"] > _STATE_TTL:
        raise OAuthError("this sign-in link expired — click Connect again")


async def finish(provider: str, code: str, state: str) -> str:
    """Exchange the code, store the token. Returns a short message for the user."""
    if provider not in PROVIDERS:
        raise OAuthError("unknown sign-in provider")
    _take_state(provider, state)
    if not code:
        raise OAuthError("no access was granted")
    async with httpx.AsyncClient(timeout=20) as c:
        return await PROVIDERS[provider].exchange(c, code)


async def disconnect(provider: str) -> None:
    """Forget the sign-in; where the service supports it, also revoke OrgTwin's access."""
    st = userconfig.oauth_state().get(provider) or {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            if provider == "google" and st.get("refresh_token"):
                await c.post("https://oauth2.googleapis.com/revoke", data={"token": st["refresh_token"]})
            if provider == "stripe" and st.get("account") and available()["stripe"]:
                await c.post("https://connect.stripe.com/oauth/deauthorize",
                             data={"client_id": _cid("stripe"), "stripe_user_id": st["account"]},
                             auth=(_csecret("stripe"), ""))
            if provider == "hubspot" and st.get("refresh_token"):
                await c.delete(f"https://api.hubapi.com/oauth/v1/refresh-tokens/{st['refresh_token']}")
            if provider == "notion" and st.get("access_token") and available()["notion"]:
                await c.post("https://api.notion.com/v1/oauth/revoke", auth=(_cid("notion"), _csecret("notion")),
                             json={"token": st["access_token"]})
            if provider == "discord" and st.get("webhook_url"):
                await c.delete(st["webhook_url"])  # a Discord webhook can delete itself
    except httpx.HTTPError:
        pass  # forget it locally even if the provider is unreachable
    _cache.pop(provider, None)
    userconfig.clear_oauth(provider)


def _jwt_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


# ---------------- Google access tokens, Sheets credentials, Gmail ----------------

_cache: dict[str, tuple[str, float]] = {}


async def google_token() -> str:
    tok, exp = _cache.get("google", ("", 0))
    if tok and time.time() < exp:
        return tok
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(GOOGLE_TOKEN, data={"client_id": _cid("google"), "client_secret": _csecret("google"),
                                             "refresh_token": settings.google_refresh_token, "grant_type": "refresh_token"})
    if r.status_code >= 400:
        raise RuntimeError("Google access was revoked or expired — reconnect Google in Connectors")
    j = r.json()
    _cache["google"] = (j["access_token"], time.time() + int(j.get("expires_in", 3600)) - 60)
    return j["access_token"]


def google_credentials():
    """google-auth user credentials for gspread (refreshes itself)."""
    from google.oauth2.credentials import Credentials
    return Credentials(token=None, refresh_token=settings.google_refresh_token, token_uri=GOOGLE_TOKEN,
                       client_id=_cid("google"), client_secret=_csecret("google"), scopes=GOOGLE_SCOPES[2:3])


async def gmail_send(msg: EmailMessage) -> None:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                         headers={"Authorization": f"Bearer {await google_token()}"}, json={"raw": raw})
    if r.status_code >= 400:
        raise RuntimeError(f"Gmail refused the email (HTTP {r.status_code}): {r.text[:200]}")


async def create_sample_sheet(rows: list[dict], columns: list[str]) -> str:
    """Make a ready-to-use CRM sheet in the user's Drive (Sheets scope can create sheets). Returns its id."""
    h = {"Authorization": f"Bearer {await google_token()}"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://sheets.googleapis.com/v4/spreadsheets", headers=h,
                         json={"properties": {"title": "OrgTwin CRM (sample leads)"}})
        if r.status_code >= 400:
            raise RuntimeError(f"could not create a sheet (HTTP {r.status_code})")
        sid = r.json()["spreadsheetId"]
        values = [columns] + [[row.get(k, "") for k in columns] for row in rows]
        r = await c.put(f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/A1",
                        params={"valueInputOption": "RAW"}, headers=h, json={"values": values})
        if r.status_code >= 400:
            raise RuntimeError(f"sheet created but rows could not be written (HTTP {r.status_code})")
    return sid


# ---------------- Notion: pick or create the database ----------------

def _notion_h() -> dict:
    return {"Authorization": f"Bearer {settings.notion_token}", "Notion-Version": "2022-06-28"}


def _title(obj: dict) -> str:
    t = obj.get("title")
    if isinstance(t, list):
        return "".join(x.get("plain_text", "") for x in t) or "Untitled"
    for prop in (obj.get("properties") or {}).values():
        if prop.get("type") == "title":
            return "".join(x.get("plain_text", "") for x in prop.get("title", [])) or "Untitled"
    return "Untitled"


async def _notion_search(kind: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.notion.com/v1/search", headers=_notion_h(),
                         json={"filter": {"property": "object", "value": kind}, "page_size": 50})
    if r.status_code >= 400:
        raise RuntimeError(f"Notion search failed (HTTP {r.status_code})")
    return [{"id": x["id"], "title": _title(x), "url": x.get("url", "")} for x in r.json().get("results", [])]


async def notion_databases() -> list[dict]:
    return await _notion_search("database")


async def notion_pages() -> list[dict]:
    return await _notion_search("page")


async def notion_create_database(page_id: str) -> dict:
    """Create an "OrgTwin" database (Name + Created by) inside a page the user shared."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.notion.com/v1/databases", headers=_notion_h(), json={
            "parent": {"type": "page_id", "page_id": page_id},
            "title": [{"type": "text", "text": {"content": "OrgTwin"}}],
            "properties": {"Name": {"title": {}}, "Created": {"created_time": {}}}})
    if r.status_code >= 400:
        raise RuntimeError(f"could not create the database (HTTP {r.status_code})")
    j = r.json()
    return {"id": j["id"], "title": "OrgTwin", "url": j.get("url", "")}


# ---------------- Telegram: deep link to OrgTwin's bot ----------------

_tg_pending: dict[str, float] = {}
_tg_offset = 0


def telegram_bot_token() -> str:
    """The admin's bot (TELEGRAM_BOT_TOKEN in .env, or one pasted in the form)."""
    return settings.telegram_bot_token


async def telegram_start() -> dict:
    tok = telegram_bot_token()
    if not tok:
        raise OAuthError("Telegram one-tap connect is not enabled yet — the admin sets TELEGRAM_BOT_TOKEN once in .env")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"https://api.telegram.org/bot{tok}/getMe")
    if r.status_code >= 400:
        raise OAuthError("the Telegram bot token is not valid")
    bot = r.json()["result"]["username"]
    code = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
    _tg_pending[code] = time.time()
    return {"code": code, "bot": bot, "url": f"https://t.me/{bot}?startgroup={code}",
            "dm_url": f"https://t.me/{bot}?start={code}"}


async def telegram_check(code: str) -> dict:
    """Look for "/start <code>" in the bot's updates; when found, that chat is where agents post."""
    global _tg_offset
    if time.time() - _tg_pending.get(code, 0) > _STATE_TTL:
        raise OAuthError("this link expired — click Connect again")
    tok = telegram_bot_token()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"https://api.telegram.org/bot{tok}/getUpdates",
                        params={"offset": _tg_offset, "timeout": 0, "allowed_updates": json.dumps(["message"])})
        if r.status_code >= 400:
            raise RuntimeError(f"Telegram getUpdates failed (HTTP {r.status_code})")
        for u in r.json().get("result", []):
            _tg_offset = max(_tg_offset, u["update_id"] + 1)
            m = u.get("message") or {}
            text = (m.get("text") or "").strip()
            if text.startswith("/start") and text.split()[-1].split("@")[0] == code:
                chat = m["chat"]
                name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or "chat"
                _tg_pending.pop(code, None)
                userconfig.save_oauth("telegram", {"chat_id": str(chat["id"]), "name": name})
                await c.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                             json={"chat_id": chat["id"], "text": "✅ OrgTwin is connected — agents will post here."})
                return {"connected": True, "account": name}
    return {"connected": False}


# ---------------- the page the popup lands on ----------------

def popup_page(provider: str, ok: bool, message: str) -> str:
    payload = json.dumps({"source": "orgtwin-oauth", "provider": provider, "ok": ok, "message": message})
    color = "#16a34a" if ok else "#dc2626"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>OrgTwin · {'Connected' if ok else 'Not connected'}</title>
<style>body{{font:16px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;background:#f6f7fb;color:#1b1f2a}}
main{{max-width:360px;padding:24px;text-align:center}}b{{color:{color};font-size:40px;display:block}}</style></head>
<body><main><b>{'✓' if ok else '✕'}</b><p>{html.escape(message)}</p><p><small>You can close this window.</small></p></main>
<script>try{{window.opener&&window.opener.postMessage({payload},"*")}}catch(e){{}}setTimeout(function(){{window.close()}},{900 if ok else 4000});</script>
</body></html>"""
