"""Slack notifications through an incoming webhook."""
import httpx

from ..config import settings

posted: list[dict] = []


def mode() -> str:
    return settings.modes()["slack"]


async def post(text: str) -> dict:
    rec = {"text": text, "mode": mode()}
    if mode() == "webhook":
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(settings.slack_webhook_url, json={"text": text})
            r.raise_for_status()
    posted.append(rec)
    return rec
