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
    mode_fn: Callable[[], str] | None = None  # connectors that work without keys report their provider

    def mode(self) -> str:
        if self.mode_fn:
            return self.mode_fn()
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


# ---------------- web: search + read a page ----------------

def _web_mode() -> str:
    return "tavily" if settings.tavily_api_key else "brave" if settings.brave_search_api_key else "basic"


def _strip_html(raw: str) -> str:
    import html as _html
    import re as _re
    raw = _re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", raw)
    raw = _re.sub(r"(?s)<[^>]+>", " ", raw)
    return _re.sub(r"\s+", " ", _html.unescape(raw)).strip()


def _results_text(query: str, results: list[dict]) -> str:
    if not results:
        return f"No web results for “{query}”."
    lines = [f"Web results for “{query}”:"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']} — {r['url']}\n   {r['snippet']}")
    return "\n".join(lines)


async def _search(query: str, n: int) -> tuple[list[dict], str]:
    ua = {"User-Agent": settings.web_user_agent}
    async with httpx.AsyncClient(timeout=20, headers=ua) as c:
        if settings.tavily_api_key:
            r = await c.post("https://api.tavily.com/search",
                             json={"api_key": settings.tavily_api_key, "query": query, "max_results": n})
            _raise(r, "Tavily")
            return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": (x.get("content") or "")[:400]}
                    for x in r.json().get("results", [])[:n]], "tavily"
        if settings.brave_search_api_key:
            r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": n},
                            headers={"X-Subscription-Token": settings.brave_search_api_key, "Accept": "application/json"})
            _raise(r, "Brave Search")
            return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": _strip_html(x.get("description") or "")[:400]}
                    for x in r.json().get("web", {}).get("results", [])[:n]], "brave"
        # keyless: DuckDuckGo instant answer + Wikipedia search (both official, free APIs)
        out: list[dict] = []
        try:
            r = await c.get("https://api.duckduckgo.com/", params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
            d = r.json() if r.status_code == 200 else {}
            if d.get("AbstractText"):
                out.append({"title": d.get("Heading") or query, "url": d.get("AbstractURL", ""), "snippet": d["AbstractText"][:400]})
            for t in d.get("RelatedTopics", []):
                if len(out) >= n:
                    break
                if t.get("Text") and t.get("FirstURL"):
                    out.append({"title": t["Text"].split(" - ")[0][:120], "url": t["FirstURL"], "snippet": t["Text"][:400]})
        except (httpx.HTTPError, ValueError):
            pass
        if len(out) < n:
            try:
                r = await c.get("https://en.wikipedia.org/w/api.php",
                                params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": n})
                for x in (r.json().get("query", {}).get("search", []) if r.status_code == 200 else []):
                    if len(out) >= n:
                        break
                    title = x.get("title", "")
                    out.append({"title": f"{title} (Wikipedia)", "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                                "snippet": _strip_html(x.get("snippet") or "")[:400]})
            except (httpx.HTTPError, ValueError):
                pass
        return out, "basic"


async def _read_page(url: str) -> str:
    """GET a public page (SSRF-checked on every redirect hop) and return its visible text."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers={"User-Agent": settings.web_user_agent}) as c:
        for _ in range(4):
            await check_url(url)
            r = await c.get(url)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = str(r.next_request.url) if r.next_request else r.headers["location"]
                continue
            _raise(r, "Web page")
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                raise RuntimeError(f"{url} is not a web page ({ctype or 'unknown type'})")
            return _strip_html(r.text[:400_000])[:6000]
    raise RuntimeError("too many redirects")


async def _web(action: str, p: dict) -> dict:
    save_as = (p.get("save_as") or ("search" if action == "search" else "page")).strip() or "search"
    if action == "search":
        query = p["query"].strip()
        if not query:
            raise RuntimeError("web search needs a query")
        try:
            n = max(1, min(10, int(float(p.get("max_results") or 5))))
        except ValueError:
            n = 5
        results, provider = await _search(query, n)
        text = _results_text(query, results)
        rec = _record("web", action, provider, f"searched “{query}” → {len(results)} result(s)",
                      {"query": query, "results": results})
        return {**rec, "save_as": save_as, "text": text}
    url = p["url"].strip()
    text = await _read_page(url)
    rec = _record("web", action, "live", f"read {url} ({len(text)} chars)", {"url": url})
    return {**rec, "save_as": save_as, "text": text}


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
    Connector("web", "Web search", "🔎", "research", ["TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY"],
              {"search": Action("search", "search the web",
                                [Param("query", "", "search for"), Param("max_results", "5", "results"),
                                 Param("save_as", "search", "save as")],
                                "search the web; results are saved as {{<save_as>}} for later steps", manual_min=5,
                                external=False),
               "read_page": Action("read_page", "read a web page",
                                   [Param("url", "https://", "URL"), Param("save_as", "page", "save as")],
                                   "fetch a public web page's text, saved as {{<save_as>}}", manual_min=4, external=False)},
              lambda: True, _web,
              "Works without a key (DuckDuckGo instant answers + Wikipedia). For full web results add "
              "TAVILY_API_KEY (tavily.com) or BRAVE_SEARCH_API_KEY (brave.com/search/api).",
              mode_fn=_web_mode),
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
    if key == "web":
        res = await _web("search", {"query": "Vultr cloud computing", "max_results": "3"})
        n = len(res["detail"]["results"])
        return {"ok": n > 0, "mode": res["mode"],
                "message": f"Search works ({res['mode']}): {n} result(s)." if n else "Search returned nothing."}
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
