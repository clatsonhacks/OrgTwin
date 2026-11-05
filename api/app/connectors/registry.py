"""Connector registry: messaging, CRM, docs, billing and generic webhooks.

Each connector declares its actions and their parameters once. That single spec drives
  * workflow design (the LLM schema prompt + step normalisation in workflows.py),
  * the generic engine (`run`),
  * the API (`GET /api/connectors`) and the step editor in the web UI.

Every connector works in two modes. **live** is used when its env vars are set. **mock**
records what *would* have been sent, so demos and tests never need credentials. Every result
says which mode ran.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlparse

import httpx

from ..config import settings
from . import stripe_c

# Everything a connector "sent" (live or mock), newest last — shown in the Activity panel.
sent: list[dict] = []


@dataclass
class Param:
    name: str
    default: str = ""
    label: str = ""
    long: bool = False  # textarea in the editor


@dataclass
class Action:
    key: str
    label: str
    params: list[Param]
    doc: str
    manual_min: float = 2
    external: bool = True  # sends something outside the company → approval by default


@dataclass
class Connector:
    key: str
    name: str
    icon: str
    category: str
    env: list[str]
    actions: dict[str, Action]
    configured: Callable[[], bool]
    handler: Callable[[str, dict], Awaitable[dict]]
    setup: str = ""
    extra: dict = field(default_factory=dict)

    def mode(self) -> str:
        return "live" if self.configured() else "mock"


def _record(tool: str, action: str, mode: str, summary: str, detail: dict) -> dict:
    rec = {"tool": tool, "action": action, "mode": mode, "summary": summary[:300], "detail": detail, "ts": time.time()}
    sent.append(rec)
    del sent[:-200]
    return rec


# ---------------- SSRF guard for user-supplied URLs ----------------

class UnsafeURL(ValueError):
    pass


def _host_allowed(host: str) -> bool:
    allowed = [h.strip().lower() for h in settings.webhook_allowed_hosts.split(",") if h.strip()]
    return not allowed or any(host == h or host.endswith("." + h) for h in allowed)


async def check_url(url: str) -> str:
    """Only http(s) to public addresses (and to WEBHOOK_ALLOWED_HOSTS when that is set)."""
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise UnsafeURL("URL must be http(s)://host/…")
    if u.scheme == "http" and not settings.webhook_allow_http:
        raise UnsafeURL("plain http is disabled (set WEBHOOK_ALLOW_HTTP=true to allow it)")
    host = u.hostname.lower()
    if not _host_allowed(host):
        raise UnsafeURL(f"{host} is not in WEBHOOK_ALLOWED_HOSTS")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror as e:
        raise UnsafeURL(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeURL(f"{host} resolves to a private or reserved address ({ip})")
    return url


async def _post_json(url: str, body: dict, headers: dict | None = None, method: str = "POST") -> httpx.Response:
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
        r = await c.request(method, url, json=body, headers=headers or {})
    return r


def _raise(r: httpx.Response, what: str) -> None:
    if r.status_code >= 400:
        raise RuntimeError(f"{what} returned HTTP {r.status_code}: {r.text[:200]}")


# ---------------- chat: Discord, Microsoft Teams, Telegram ----------------

async def _discord(action: str, p: dict) -> dict:
    text = p["text"][:2000]
    if settings.discord_webhook_url:
        r = await _post_json(settings.discord_webhook_url, {"content": text, "username": "OrgTwin"})
        _raise(r, "Discord")
        return _record("discord", action, "live", text, {})
    return _record("discord", action, "mock", text, {})


def _teams_payload(text: str) -> dict:
    # Works for both classic Office 365 connectors (reads "text") and Workflows webhooks (Adaptive Card).
    return {"text": text, "type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                    "version": "1.4", "body": [{"type": "TextBlock", "text": text, "wrap": True}]}}]}


async def _teams(action: str, p: dict) -> dict:
    text = p["text"][:4000]
    if settings.teams_webhook_url:
        r = await _post_json(settings.teams_webhook_url, _teams_payload(text))
        _raise(r, "Microsoft Teams")
        return _record("teams", action, "live", text, {})
    return _record("teams", action, "mock", text, {})


async def _telegram(action: str, p: dict) -> dict:
    text = p["text"][:4000]
    chat = p.get("chat_id") or settings.telegram_chat_id
    if settings.telegram_bot_token and chat:
        r = await _post_json(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                             {"chat_id": chat, "text": text})
        _raise(r, "Telegram")
        return _record("telegram", action, "live", text, {"chat_id": chat})
    return _record("telegram", action, "mock", text, {"chat_id": chat or "(not set)"})


# ---------------- docs: Notion ----------------

_notion_title_prop: dict[str, str] = {}
NOTION_VERSION = "2022-06-28"


def _notion_headers() -> dict:
    return {"Authorization": f"Bearer {settings.notion_token}", "Notion-Version": NOTION_VERSION}


async def _notion(action: str, p: dict) -> dict:
    title, content = p["title"][:200] or "Untitled", p["content"]
    if not (settings.notion_token and settings.notion_database_id):
        return _record("notion", action, "mock", f"{title}: {content}", {"title": title})
    db = settings.notion_database_id
    async with httpx.AsyncClient(timeout=20) as c:
        if db not in _notion_title_prop:  # every database has exactly one title property; find its name
            r = await c.get(f"https://api.notion.com/v1/databases/{db}", headers=_notion_headers())
            _raise(r, "Notion")
            props = r.json().get("properties", {})
            _notion_title_prop[db] = next((k for k, v in props.items() if v.get("type") == "title"), "Name")
        chunks = [content[i:i + 1900] for i in range(0, min(len(content), 19000), 1900)] or [""]
        body = {"parent": {"database_id": db},
                "properties": {_notion_title_prop[db]: {"title": [{"text": {"content": title}}]}},
                "children": [{"object": "block", "type": "paragraph",
                              "paragraph": {"rich_text": [{"type": "text", "text": {"content": ch}}]}} for ch in chunks]}
        r = await c.post("https://api.notion.com/v1/pages", headers=_notion_headers(), json=body)
        _raise(r, "Notion")
        page = r.json()
    return _record("notion", action, "live", title, {"page_id": page.get("id"), "url": page.get("url")})


# ---------------- CRM: HubSpot ----------------

async def _hubspot(action: str, p: dict) -> dict:
    email = p["email"].strip()
    if not email or "@" not in email:
        raise RuntimeError("HubSpot needs a contact email (e.g. {{row.email}})")
    props = {k: v for k, v in {"email": email, "firstname": p.get("firstname", ""), "lastname": p.get("lastname", ""),
                               "company": p.get("company", ""), "lifecyclestage": p.get("lifecyclestage", "")}.items() if v}
    if not settings.hubspot_token:
        return _record("hubspot", action, "mock", f"upsert contact {email}", {"properties": props})
    h = {"Authorization": f"Bearer {settings.hubspot_token}"}
    base = "https://api.hubapi.com/crm/v3/objects/contacts"
    r = await _post_json(base, {"properties": props}, h)
    if r.status_code == 409:  # already exists → update by email
        r = await _post_json(f"{base}/{email}?idProperty=email", {"properties": props}, h, method="PATCH")
        verb = "updated"
    else:
        verb = "created"
    _raise(r, "HubSpot")
    return _record("hubspot", action, "live", f"{verb} contact {email}", {"id": r.json().get("id"), "properties": props})


# ---------------- billing: Stripe invoices (test mode only) ----------------

def _amount_cents(v: str) -> int:
    try:
        return max(0, round(float(str(v).replace("$", "").replace(",", "").strip()) * 100))
    except ValueError as e:
        raise RuntimeError(f"invoice amount {v!r} is not a number") from e


async def _stripe(action: str, p: dict) -> dict:
    email = p["email"].strip()
    if not email:
        raise RuntimeError("Stripe invoice needs a customer email (e.g. {{row.email}})")
    cents = _amount_cents(p["amount"])
    draft = await stripe_c.create_draft_invoice(email, p.get("name") or email, cents, p["description"])
    inv = await stripe_c.finalize_invoice(draft["invoice_id"], cents)
    mode = "live" if stripe_c.mode() != "mock" else "mock"
    return _record("stripe", action, mode, f"invoice ${cents / 100:,.2f} to {email}", inv)


# ---------------- generic webhook ----------------

async def _webhook(action: str, p: dict) -> dict:
    url, method = p["url"].strip(), (p.get("method") or "POST").upper()
    if method not in ("POST", "PUT", "PATCH", "GET"):
        raise RuntimeError("webhook method must be POST, PUT, PATCH or GET")
    raw = p.get("body") or ""
    try:
        body = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        body = {"text": raw}
    await check_url(url)  # validate even in mock mode so the designer learns about bad URLs early
    if not settings.webhook_enabled:
        return _record("webhook", action, "mock", f"{method} {url}", {"body": body})
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
        r = await c.request(method, url, json=body if method != "GET" else None)
    _raise(r, "Webhook")
    return _record("webhook", action, "live", f"{method} {url} → {r.status_code}", {"status": r.status_code, "response": r.text[:300]})


# ---------------- the registry ----------------

TEXT = [Param("text", "{{summary}}", "message", long=True)]

CONNECTORS: dict[str, Connector] = {c.key: c for c in [
    Connector("discord", "Discord", "🎮", "chat", ["DISCORD_WEBHOOK_URL"],
              {"post": Action("post", "post message", TEXT, "post a message to a Discord channel")},
              lambda: bool(settings.discord_webhook_url), _discord,
              "Channel → Edit → Integrations → Webhooks → New webhook → copy URL."),
    Connector("teams", "Microsoft Teams", "🟪", "chat", ["TEAMS_WEBHOOK_URL"],
              {"post": Action("post", "post message", TEXT, "post a message to a Microsoft Teams channel")},
              lambda: bool(settings.teams_webhook_url), _teams,
              "Channel → Workflows → 'Post to a channel when a webhook request is received' → copy URL."),
    Connector("telegram", "Telegram", "✈️", "chat", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
              {"send": Action("send", "send message", TEXT + [Param("chat_id", "", "chat id (optional)")],
                              "send a Telegram message from your bot")},
              lambda: bool(settings.telegram_bot_token and settings.telegram_chat_id), _telegram,
              "Create a bot with @BotFather, add it to a chat, and set the chat id."),
    Connector("notion", "Notion", "📝", "docs", ["NOTION_TOKEN", "NOTION_DATABASE_ID"],
              {"create_page": Action("create_page", "create page",
                                     [Param("title", "Report {{count}} rows", "title"),
                                      Param("content", "{{summary}}", "content", long=True)],
                                     "create a page in a Notion database", manual_min=6, external=False)},
              lambda: bool(settings.notion_token and settings.notion_database_id), _notion,
              "Create an internal integration, share the database with it, copy the token and database id."),
    Connector("hubspot", "HubSpot CRM", "🧲", "crm", ["HUBSPOT_TOKEN"],
              {"upsert_contact": Action("upsert_contact", "create / update contact",
                                        [Param("email", "{{row.email}}", "email"), Param("firstname", "{{row.name}}", "first name"),
                                         Param("lastname", "", "last name"), Param("company", "{{row.company}}", "company"),
                                         Param("lifecyclestage", "", "lifecycle stage")],
                                        "create or update a HubSpot contact", manual_min=3, external=False)},
              lambda: bool(settings.hubspot_token), _hubspot,
              "Settings → Integrations → Private apps → scopes crm.objects.contacts.read/write."),
    Connector("stripe", "Stripe invoices", "🧾", "billing", ["STRIPE_SECRET_KEY"],
              {"invoice": Action("invoice", "create & send invoice",
                                 [Param("email", "{{row.email}}", "customer email"), Param("name", "{{row.name}}", "customer name"),
                                  Param("amount", "{{row.amount}}", "amount (USD)"), Param("description", "Services", "line item")],
                                 "create and send a Stripe invoice (test mode only)", manual_min=8)},
              lambda: stripe_c.mode() != "mock", _stripe,
              "Use a TEST secret key (sk_test_…). Live keys are refused."),
    Connector("webhook", "Webhook / HTTP", "🌐", "developer", ["WEBHOOK_ENABLED", "WEBHOOK_ALLOWED_HOSTS"],
              {"request": Action("request", "call URL",
                                 [Param("url", "https://", "URL"), Param("method", "POST", "method"),
                                  Param("body", '{"text": "{{summary}}"}', "JSON body", long=True)],
                                 "call any HTTPS endpoint (Zapier, Make, n8n, your own API)")},
              lambda: settings.webhook_enabled, _webhook,
              "Set WEBHOOK_ENABLED=true. Optionally limit targets with WEBHOOK_ALLOWED_HOSTS=hooks.zapier.com,…"),
]}


def spec(tool: str, action: str) -> Action | None:
    c = CONNECTORS.get(tool)
    return c.actions.get(action) if c else None


def normalize_params(tool: str, action: str, params: dict) -> dict:
    a = spec(tool, action)
    if not a:
        return {}
    return {p.name: str(params.get(p.name) if params.get(p.name) not in (None, "") else p.default) for p in a.params}


async def run(tool: str, action: str, params: dict) -> dict:
    c = CONNECTORS[tool]
    if action not in c.actions:
        raise RuntimeError(f"{c.name} has no action {action!r}")
    return await c.handler(action, params)


def catalog() -> list[dict]:
    return [{"key": c.key, "name": c.name, "icon": c.icon, "category": c.category, "mode": c.mode(),
             "env": c.env, "setup": c.setup,
             "actions": [{"key": a.key, "label": a.label, "doc": a.doc, "external": a.external,
                          "params": [{"name": p.name, "default": p.default, "label": p.label or p.name, "long": p.long}
                                     for p in a.params]} for a in c.actions.values()]}
            for c in CONNECTORS.values()]


def schema_lines() -> str:
    """Extra lines for the workflow-design prompt."""
    out = []
    for c in CONNECTORS.values():
        for a in c.actions.values():
            ps = ", ".join(f'"{p.name}": str' for p in a.params)
            out.append(f"- {c.key}/{a.key}: {{{ps}}}  ({a.doc})")
    return "\n".join(out)


# ---------------- connection test ----------------

async def test(key: str, url: str = "") -> dict:
    """Prove a connector works: chat tools send a short message, the rest make a read-only call."""
    c = CONNECTORS[key]
    msg = "✅ OrgTwin is connected — agents can post here now."
    if key == "webhook":
        if not url:
            raise RuntimeError("give a URL to test")
        res = await _webhook("request", {"url": url, "method": "POST", "body": json.dumps({"text": msg})})
        return {"ok": True, "mode": res["mode"], "message": res["summary"]}
    if c.mode() == "mock":
        return {"ok": True, "mode": "mock", "message": f"Demo mode — set {', '.join(c.env)} to go live. Nothing was sent."}
    if key in ("discord", "teams", "telegram"):
        await c.handler(next(iter(c.actions)), {"text": msg})
        return {"ok": True, "mode": "live", "message": "Test message sent."}
    if key == "notion":
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get(f"https://api.notion.com/v1/databases/{settings.notion_database_id}", headers=_notion_headers())
        _raise(r, "Notion")
        title = "".join(t.get("plain_text", "") for t in r.json().get("title", [])) or "database"
        return {"ok": True, "mode": "live", "message": f"Can write to Notion “{title}”."}
    if key == "hubspot":
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get("https://api.hubapi.com/crm/v3/objects/contacts?limit=1",
                             headers={"Authorization": f"Bearer {settings.hubspot_token}"})
        _raise(r, "HubSpot")
        return {"ok": True, "mode": "live", "message": "HubSpot token works (contacts readable)."}
    if key == "stripe":
        bal = await asyncio.to_thread(lambda: stripe_c._stripe().Balance.retrieve())
        return {"ok": True, "mode": "live", "message": f"Stripe test mode OK (livemode={bal.get('livemode')})."}
    return {"ok": True, "mode": c.mode(), "message": "OK"}
