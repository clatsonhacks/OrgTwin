"""Team chat notifications: Slack, Discord and Teams webhooks, and a Telegram bot.

One message goes to every channel that is configured; with none configured it is only recorded (mock).
"""
import asyncio

import httpx

from ..config import settings

posted: list[dict] = []


def channels() -> list[str]:
    return settings.chat_channels()


def mode() -> str:
    return settings.modes()["chat"]


def _teams_card(text: str) -> dict:
    # Teams "Workflows" webhooks expect an Adaptive Card wrapped in a message.
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                    "version": "1.4", "body": [{"type": "TextBlock", "text": text, "wrap": True}]}}]}


async def _send(c: httpx.AsyncClient, channel: str, text: str) -> None:
    if channel == "slack":
        r = await c.post(settings.slack_webhook_url, json={"text": text})
    elif channel == "discord":
        r = await c.post(settings.discord_webhook_url, json={"content": text[:2000]})
    elif channel == "teams":
        r = await c.post(settings.teams_webhook_url, json=_teams_card(text))
    else:  # telegram
        r = await c.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                         json={"chat_id": settings.telegram_chat_id, "text": text[:4096]})
    r.raise_for_status()


async def _try(c: httpx.AsyncClient, channel: str, text: str) -> str | None:
    try:
        await _send(c, channel, text)
        return None
    except httpx.HTTPStatusError as e:
        return f"{channel}: HTTP {e.response.status_code}"  # never echo the URL: it holds the secret
    except httpx.HTTPError as e:
        return f"{channel}: {type(e).__name__}"


async def post(text: str, only: list[str] | None = None) -> dict:
    """Post to every configured channel, or only to `only` when the plan already named some of them."""
    targets = [c for c in channels() if not only or c in only]
    rec = {"text": text, "mode": mode(), "channels": targets}
    if targets:
        async with httpx.AsyncClient(timeout=10) as c:
            errors = [e for e in await asyncio.gather(*(_try(c, ch, text) for ch in targets)) if e]
        if errors:
            rec["errors"] = errors
        if len(errors) == len(targets):
            raise RuntimeError("chat notification failed: " + "; ".join(errors))
    posted.append(rec)
    return rec
