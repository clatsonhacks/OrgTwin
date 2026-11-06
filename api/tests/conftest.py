"""Tests run in demo mode (mock LLM + mock connectors, local runner), whatever the repo-root .env holds.

Settings are read once at import, so this has to happen before any test module imports the app.
"""
import os
import tempfile

os.environ["ORGTWIN_NO_DOTENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["RUNNER"] = os.environ.get("TEST_RUNNER", "local")
os.environ["STEP_DELAY_S"] = "0"
for k in ("VULTR_INFERENCE_API_KEY", "STRIPE_SECRET_KEY", "SMTP_HOST", "GOOGLE_SHEET_ID", "SLACK_WEBHOOK_URL",
          "DISCORD_WEBHOOK_URL", "TEAMS_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.pop(k, None)
