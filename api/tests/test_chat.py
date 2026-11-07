import asyncio

import httpx

from app.config import settings
from app.connectors import slack


def _configure(monkeypatch, handler):
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.test/s")
    monkeypatch.setattr(settings, "discord_webhook_url", "https://discord.test/w")
    monkeypatch.setattr(settings, "teams_webhook_url", "https://teams.test/w")
    monkeypatch.setattr(settings, "telegram_bot_token", "SECRET")
    monkeypatch.setattr(settings, "telegram_chat_id", "42")
    real = httpx.AsyncClient
    monkeypatch.setattr(slack.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_posts_to_every_configured_channel(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen[req.url.host] = req.read().decode()
        return httpx.Response(200)

    _configure(monkeypatch, handler)
    rec = asyncio.run(slack.post("hello"))
    assert rec["channels"] == ["slack", "discord", "teams", "telegram"] and "errors" not in rec
    assert '"text":"hello"' in seen["hooks.slack.test"].replace(" ", "")
    assert '"content":"hello"' in seen["discord.test"].replace(" ", "")
    assert "AdaptiveCard" in seen["teams.test"]
    assert '"chat_id":"42"' in seen["api.telegram.org"].replace(" ", "")


def test_one_failing_channel_does_not_fail_the_step(monkeypatch):
    _configure(monkeypatch, lambda req: httpx.Response(404 if req.url.host == "api.telegram.org" else 200))
    rec = asyncio.run(slack.post("hi"))
    assert rec["errors"] == ["telegram: HTTP 404"]  # the bot token in the URL is never echoed


def test_named_channel_is_not_posted_twice(monkeypatch):
    # "Telegram" already has its own step, so the Slack broadcast must not reach Telegram again
    from app import workflows
    seen = []
    _configure(monkeypatch, lambda req: seen.append(req.url.host) or httpx.Response(200))
    steps = workflows.plan_from_text("tell the team on slack and telegram", ["x"])
    chat = [s for s in steps if s["tool"] in ("slack", "telegram")]
    assert [s["tool"] for s in chat] == ["telegram", "slack"]
    rec = asyncio.run(slack.post("done", only=chat[-1]["params"]["channels"]))
    assert rec["channels"] == ["slack"] and seen == ["hooks.slack.test"]
