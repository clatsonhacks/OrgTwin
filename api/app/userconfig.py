"""Connector credentials people connect from the app instead of `.env`.

Vultr Serverless Inference (the AI) stays operator-configured in `.env`. Connectors are connected
by the user: Google (Sheets + Gmail) and Stripe with a sign-in popup that asks them to allow access
(OAuth, see oauth.py), as are Notion, Slack, Discord and HubSpot; Telegram with a deep link to the
bot. Teams, webhooks, web search (and any of these when the admin has not registered an OAuth app)
use a short form. Values live in the `app_settings` table and are applied on top of the env-based
`settings` object, so code that reads `settings.x` picks them up without a restart. Secrets are
never returned in full; the API only shows a masked hint.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from .config import Settings, settings
from .db import AppSetting, SessionLocal


# ---------------- connector fields ----------------

@dataclass
class FieldSpec:
    env: str
    attr: str
    label: str
    type: str = "text"        # text | secret | textarea | toggle | number
    placeholder: str = ""
    optional: bool = False


@dataclass
class ConnectorSetup:
    fields: list[FieldSpec]
    steps: list[str]
    link: str = ""
    link_label: str = ""


CONNECTOR_SETUP: dict[str, ConnectorSetup] = {
    "slack": ConnectorSetup(
        [FieldSpec("SLACK_WEBHOOK_URL", "slack_webhook_url", "Webhook URL", "secret", "https://hooks.slack.com/services/…")],
        ["Open Slack's Incoming Webhooks page and pick a channel.", "Copy the webhook URL and paste it here."],
        "https://api.slack.com/messaging/webhooks", "Create a Slack webhook"),
    "email": ConnectorSetup(
        [FieldSpec("SMTP_HOST", "smtp_host", "SMTP server", placeholder="smtp.gmail.com"),
         FieldSpec("SMTP_PORT", "smtp_port", "Port", "number", "587", optional=True),
         FieldSpec("SMTP_USER", "smtp_user", "Username", placeholder="you@gmail.com"),
         FieldSpec("SMTP_PASSWORD", "smtp_password", "Password / app password", "secret"),
         FieldSpec("SMTP_FROM", "smtp_from", "Send from", placeholder="OrgTwin <you@gmail.com>"),
         FieldSpec("EMAIL_REDIRECT_TO", "email_redirect_to", "Send every email to (safety, optional)",
                   placeholder="me@example.com", optional=True)],
        ["Gmail: turn on 2-step verification, then create an app password.", "Use smtp.gmail.com, port 587."],
        "https://myaccount.google.com/apppasswords", "Create a Gmail app password"),
    "sheets": ConnectorSetup(
        [FieldSpec("GOOGLE_SHEET_ID", "sheet_id", "Sheet ID or link", placeholder="https://docs.google.com/spreadsheets/d/…"),
         FieldSpec("GOOGLE_SERVICE_ACCOUNT_JSON", "google_sa_json", "Service account key (JSON)", "textarea",
                   '{"type": "service_account", …}')],
        ["Create a service account and download its JSON key.", "Share your sheet with the service account's email."],
        "https://console.cloud.google.com/iam-admin/serviceaccounts", "Open Google Cloud"),
    "discord": ConnectorSetup(
        [FieldSpec("DISCORD_WEBHOOK_URL", "discord_webhook_url", "Webhook URL", "secret", "https://discord.com/api/webhooks/…")],
        ["Channel settings → Integrations → Webhooks → New webhook.", "Copy the webhook URL."],
        "https://support.discord.com/hc/en-us/articles/228383668", "How to make a Discord webhook"),
    "teams": ConnectorSetup(
        [FieldSpec("TEAMS_WEBHOOK_URL", "teams_webhook_url", "Webhook URL", "secret", "https://…webhook.office.com/…")],
        ["In the channel, add the Workflows app → “Post to a channel when a webhook request is received”.",
         "Copy the URL it gives you."],
        "https://support.microsoft.com/office/create-incoming-webhooks-with-workflows-for-microsoft-teams-8ae491c7-0394-4861-ba59-055e33f75498",
        "Teams webhook guide"),
    "telegram": ConnectorSetup(
        [FieldSpec("TELEGRAM_BOT_TOKEN", "telegram_bot_token", "Bot token", "secret", "123456:ABC…"),
         FieldSpec("TELEGRAM_CHAT_ID", "telegram_chat_id", "Chat ID", placeholder="-100…")],
        ["Message @BotFather, send /newbot and copy the token.", "Add the bot to your chat and paste the chat ID."],
        "https://t.me/BotFather", "Open BotFather"),
    "notion": ConnectorSetup(
        [FieldSpec("NOTION_TOKEN", "notion_token", "Integration secret", "secret", "ntn_…"),
         FieldSpec("NOTION_DATABASE_ID", "notion_database_id", "Database ID or link", placeholder="https://www.notion.so/…")],
        ["Create an internal integration and copy its secret.", "Open your database → ••• → Connections → add it."],
        "https://www.notion.so/profile/integrations", "Create a Notion integration"),
    "hubspot": ConnectorSetup(
        [FieldSpec("HUBSPOT_TOKEN", "hubspot_token", "Private app token", "secret", "pat-…")],
        ["Settings → Integrations → Private apps → Create.", "Give it CRM contacts read/write and copy the token."],
        "https://app.hubspot.com/private-apps/", "Open HubSpot private apps"),
    "stripe": ConnectorSetup(
        [FieldSpec("STRIPE_SECRET_KEY", "stripe_secret_key", "Test secret key", "secret", "sk_test_…")],
        ["Open the Stripe dashboard in test mode.", "Copy the secret key that starts with sk_test_."],
        "https://dashboard.stripe.com/test/apikeys", "Open Stripe API keys"),
    "web": ConnectorSetup(
        [FieldSpec("TAVILY_API_KEY", "tavily_api_key", "Tavily API key (optional)", "secret", "tvly-…", optional=True),
         FieldSpec("BRAVE_SEARCH_API_KEY", "brave_search_api_key", "Brave Search key (optional)", "secret", optional=True)],
        ["Works without a key (DuckDuckGo + Wikipedia).", "Add a Tavily or Brave key for better results."],
        "https://app.tavily.com/home", "Get a free Tavily key"),
    "webhook": ConnectorSetup(
        [FieldSpec("WEBHOOK_ENABLED", "webhook_enabled", "Allow workflows to call webhooks", "toggle"),
         FieldSpec("WEBHOOK_ALLOWED_HOSTS", "webhook_allowed_hosts", "Only these hosts (optional)",
                   placeholder="hooks.zapier.com, hook.eu1.make.com", optional=True)],
        ["Turn it on, and optionally limit it to the hosts you use (Zapier, Make, n8n…)."]),
}

_ATTR = {f.env: f for s in CONNECTOR_SETUP.values() for f in s.fields}


# ---------------- storage ----------------

def _get(key: str, default=None):
    try:
        with SessionLocal() as s:
            row = s.get(AppSetting, key)
            return row.value if row else default
    except SQLAlchemyError:  # table not created yet (first start, tests)
        return default


def _put(key: str, value) -> None:
    with SessionLocal() as s:
        row = s.get(AppSetting, key)
        if value is None:
            if row:
                s.delete(row)
        elif row:
            row.value = value
        else:
            s.add(AppSetting(key=key, value=value))
        s.commit()


def mask(v: str) -> str:
    v = str(v or "")
    if not v:
        return ""
    return "••••" + v[-4:] if len(v) > 8 else "••••"


# One-click sign-ins: {"google": {"refresh_token", "email", "scopes"}, "stripe": {"access_token", "account", "name"}}

def oauth_state() -> dict:
    return _get("oauth", None) or {}


def save_oauth(provider: str, data: dict) -> None:
    st = oauth_state()
    st[provider] = data
    _put("oauth", st)
    apply_connectors()


def clear_oauth(provider: str) -> None:
    st = oauth_state()
    st.pop(provider, None)
    _put("oauth", st)
    apply_connectors()


# Connector values: {"DISCORD_WEBHOOK_URL": "...", ...} applied on top of the env.

_ENV_DEFAULTS: dict[str, object] = {}


def _coerce(f: FieldSpec, v):
    if f.type == "toggle":
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
    if f.type == "number":
        return int(v) if str(v).strip() else int(getattr(Settings, f.attr))
    return str(v).strip()


def _normalise(env: str, v: str) -> str:
    # people paste links; keep the id
    if env == "GOOGLE_SHEET_ID" and "/d/" in v:
        v = v.split("/d/", 1)[1].split("/", 1)[0]
    if env == "NOTION_DATABASE_ID" and ("notion.so" in v or "notion.site" in v):
        tail = v.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        v = tail.rsplit("-", 1)[-1]
    return v


def connector_values() -> dict:
    return _get("connectors", None) or {}


_OAUTH_ATTRS = ("google_refresh_token", "google_account", "stripe_secret_key", "stripe_account", "notion_token",
                "slack_webhook_url", "discord_webhook_url", "hubspot_token", "telegram_chat_id")


def apply_connectors() -> None:
    """Put stored connector values and sign-ins onto `settings` (env values stay where nothing was stored)."""
    vals = connector_values()
    for attr in [f.attr for f in _ATTR.values()] + list(_OAUTH_ATTRS):
        if attr not in _ENV_DEFAULTS:
            _ENV_DEFAULTS[attr] = getattr(settings, attr, "")
        setattr(settings, attr, _ENV_DEFAULTS[attr])
    for env, f in _ATTR.items():
        if env in vals:
            setattr(settings, f.attr, _coerce(f, vals[env]))
    oa = oauth_state()
    if g := oa.get("google"):
        settings.google_refresh_token = g.get("refresh_token", "")
        settings.google_account = g.get("email", "")
    if st := oa.get("stripe"):
        settings.stripe_secret_key = st.get("access_token", "")
        settings.stripe_account = st.get("name") or st.get("account", "")
    if st := oa.get("notion"):
        settings.notion_token = st.get("access_token", "")
    if st := oa.get("slack"):
        settings.slack_webhook_url = st.get("webhook_url", "")
    if st := oa.get("discord"):
        settings.discord_webhook_url = st.get("webhook_url", "")
    if st := oa.get("hubspot"):
        settings.hubspot_token = st.get("access_token", "")  # refreshed by oauth.hubspot_token() before use
    if st := oa.get("telegram"):
        settings.telegram_chat_id = st.get("chat_id", "")


def save_connector(key: str, values: dict) -> None:
    spec = CONNECTOR_SETUP[key]
    vals = connector_values()
    for f in spec.fields:
        if f.env not in values:
            continue  # untouched (e.g. a saved secret the UI only shows masked)
        v = values[f.env]
        if f.type != "toggle":
            v = _normalise(f.env, str(v or "").strip())
        if v in ("", None) and f.type != "toggle":
            vals.pop(f.env, None)
        else:
            vals[f.env] = v
    _put("connectors", vals)
    apply_connectors()


def clear_connector(key: str) -> None:
    vals = connector_values()
    for f in CONNECTOR_SETUP[key].fields:
        vals.pop(f.env, None)
    _put("connectors", vals)
    apply_connectors()


def connector_form(key: str) -> dict | None:
    spec = CONNECTOR_SETUP.get(key)
    if not spec:
        return None
    vals = connector_values()
    fields = []
    for f in spec.fields:
        cur = getattr(settings, f.attr)
        has = bool(cur) if f.type != "number" else True
        shown = (mask(cur) if f.type in ("secret", "textarea") else cur) if has else ""
        fields.append({"env": f.env, "label": f.label, "type": f.type, "placeholder": f.placeholder,
                       "optional": f.optional, "set": bool(cur) if f.type != "number" else bool(vals.get(f.env)),
                       "value": shown if f.type not in ("secret", "textarea") else "", "hint": shown if f.type in ("secret", "textarea") else "",
                       "source": "app" if f.env in vals else ("env" if has and f.type != "number" else "")})
    return {"fields": fields, "steps": spec.steps, "link": spec.link, "link_label": spec.link_label,
            "connected_in_app": any(f.env in vals for f in spec.fields)}


def load() -> None:
    """Called once at startup, after the tables exist."""
    apply_connectors()
