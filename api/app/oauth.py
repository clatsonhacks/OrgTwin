"""One-click "Connect" for Google (Sheets + Gmail) and Stripe: a sign-in popup that asks for access.

Flow (OAuth 2.0 authorization code):
  1. The app calls POST /api/oauth/{provider}/start and opens the returned URL in a popup.
  2. The user signs in at Google / Stripe and clicks Allow.
  3. The provider redirects the popup to GET /api/oauth/{provider}/callback; we swap the code for a
     token, store it (userconfig) and the popup tells the app "connected" and closes itself.

The admin registers OrgTwin once as an OAuth app (GOOGLE_OAUTH_CLIENT_ID/SECRET, STRIPE_CONNECT_*);
no user ever copies a key. Stripe stays test-mode only, like the rest of OrgTwin.
"""
from __future__ import annotations

import base64
import html
import json
import secrets
import time
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx

from . import userconfig
from .config import settings

GOOGLE_SCOPES = ["openid", "email",
                 "https://www.googleapis.com/auth/spreadsheets",
                 "https://www.googleapis.com/auth/gmail.send"]
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
STRIPE_AUTH = "https://connect.stripe.com/oauth/authorize"
STRIPE_TOKEN = "https://connect.stripe.com/oauth/token"

_pending: dict[str, dict] = {}   # state -> {provider, ts}
_STATE_TTL = 600


class OAuthError(RuntimeError):
    pass


def available() -> dict:
    """Which sign-in popups the admin has enabled."""
    return {"google": bool(settings.google_oauth_client_id and settings.google_oauth_client_secret),
            "stripe": bool(settings.stripe_connect_client_id and settings.stripe_connect_secret_key)}


def redirect_uri(provider: str) -> str:
    return f"{settings.public_api_url.rstrip('/')}/api/oauth/{provider}/callback"


def start(provider: str) -> str:
    if provider not in ("google", "stripe"):
        raise OAuthError("unknown sign-in provider")
    if not available()[provider]:
        need = ("GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET" if provider == "google"
                else "STRIPE_CONNECT_CLIENT_ID and STRIPE_CONNECT_SECRET_KEY")
        raise OAuthError(f"one-click sign-in is not enabled yet — the admin sets {need} once in .env")
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["ts"] > _STATE_TTL]:
        _pending.pop(k, None)
    state = secrets.token_urlsafe(24)
    _pending[state] = {"provider": provider, "ts": now}
    if provider == "google":
        q = {"client_id": settings.google_oauth_client_id, "redirect_uri": redirect_uri("google"),
             "response_type": "code", "scope": " ".join(GOOGLE_SCOPES), "access_type": "offline",
             "prompt": "consent", "include_granted_scopes": "true", "state": state}
        return f"{GOOGLE_AUTH}?{urlencode(q)}"
    q = {"response_type": "code", "client_id": settings.stripe_connect_client_id, "scope": "read_write",
         "redirect_uri": redirect_uri("stripe"), "state": state}
    return f"{STRIPE_AUTH}?{urlencode(q)}"


def _take_state(provider: str, state: str) -> None:
    item = _pending.pop(state or "", None)
    if not item or item["provider"] != provider or time.time() - item["ts"] > _STATE_TTL:
        raise OAuthError("this sign-in link expired — click Connect again")


async def finish(provider: str, code: str, state: str) -> str:
    """Exchange the code, store the token. Returns a short message for the user."""
    _take_state(provider, state)
    if not code:
        raise OAuthError("no access was granted")
    async with httpx.AsyncClient(timeout=20) as c:
        if provider == "google":
            r = await c.post(GOOGLE_TOKEN, data={
                "code": code, "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": redirect_uri("google"), "grant_type": "authorization_code"})
            if r.status_code >= 400:
                raise OAuthError(f"Google did not accept the sign-in (HTTP {r.status_code})")
            tok = r.json()
            refresh = tok.get("refresh_token") or userconfig.oauth_state().get("google", {}).get("refresh_token", "")
            if not refresh:
                raise OAuthError("Google did not give offline access — remove OrgTwin at myaccount.google.com/permissions and try again")
            email = ""
            if tok.get("id_token"):
                email = _jwt_claims(tok["id_token"]).get("email", "")
            granted = tok.get("scope", "")
            userconfig.save_oauth("google", {"refresh_token": refresh, "email": email, "scopes": granted})
            _cache["google"] = (tok.get("access_token", ""), time.time() + int(tok.get("expires_in", 0)) - 60)
            missing = [n for s, n in ((GOOGLE_SCOPES[2], "Sheets"), (GOOGLE_SCOPES[3], "Gmail")) if s not in granted]
            if missing:
                return f"Connected {email or 'Google'}, but {' and '.join(missing)} access was not allowed."
            return f"Connected {email or 'your Google account'} — Sheets and Gmail are ready."
        # Stripe Connect
        r = await c.post(STRIPE_TOKEN, data={"grant_type": "authorization_code", "code": code},
                         auth=(settings.stripe_connect_secret_key, ""))
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
        userconfig.save_oauth("stripe", {"access_token": tok["access_token"], "account": tok.get("stripe_user_id", ""),
                                         "name": name})
        return f"Connected Stripe ({name}, test mode) — agents can send invoices now."


async def disconnect(provider: str) -> None:
    st = userconfig.oauth_state().get(provider) or {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            if provider == "google" and st.get("refresh_token"):
                await c.post("https://oauth2.googleapis.com/revoke", data={"token": st["refresh_token"]})
            if provider == "stripe" and st.get("account") and available()["stripe"]:
                await c.post("https://connect.stripe.com/oauth/deauthorize",
                             data={"client_id": settings.stripe_connect_client_id, "stripe_user_id": st["account"]},
                             auth=(settings.stripe_connect_secret_key, ""))
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
        r = await c.post(GOOGLE_TOKEN, data={"client_id": settings.google_oauth_client_id,
                                             "client_secret": settings.google_oauth_client_secret,
                                             "refresh_token": settings.google_refresh_token,
                                             "grant_type": "refresh_token"})
    if r.status_code >= 400:
        raise RuntimeError("Google access was revoked or expired — reconnect Google in Connectors")
    j = r.json()
    _cache["google"] = (j["access_token"], time.time() + int(j.get("expires_in", 3600)) - 60)
    return j["access_token"]


def google_credentials():
    """google-auth user credentials for gspread (refreshes itself)."""
    from google.oauth2.credentials import Credentials
    return Credentials(token=None, refresh_token=settings.google_refresh_token, token_uri=GOOGLE_TOKEN,
                       client_id=settings.google_oauth_client_id, client_secret=settings.google_oauth_client_secret,
                       scopes=GOOGLE_SCOPES[2:3])


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
