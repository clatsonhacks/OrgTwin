"""Runtime configuration, read from environment variables (see ../.env.example)."""
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Settings:
    database_url = _env("DATABASE_URL", "sqlite:///./orgtwin.db")

    # Vultr Serverless Inference (OpenAI-compatible)
    inference_base_url = _env("VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1")
    inference_api_key = _env("VULTR_INFERENCE_API_KEY")
    inference_model = _env("VULTR_INFERENCE_MODEL")  # empty -> first model from /models
    # Vision model for the Skill Recorder; empty -> auto-detect a vision-capable model from /models
    vision_model = _env("VULTR_VISION_MODEL")
    # Reasoning models can spend the whole max_tokens budget thinking and return no answer.
    # "none" turns thinking off; set to "" to omit the parameter.
    reasoning_effort = _env("VULTR_REASONING_EFFORT", "none")

    # Sandbox runner: "docker" (isolated) or "local" (dev only, NOT isolated)
    runner = _env("RUNNER", "docker")
    sandbox_image = _env("SANDBOX_IMAGE", "python:3.12-alpine")
    sandbox_timeout_s = int(_env("SANDBOX_TIMEOUT_S", "20"))
    sandbox_memory = _env("SANDBOX_MEMORY", "128m")

    # Connectors
    google_sa_json = _env("GOOGLE_SERVICE_ACCOUNT_JSON")  # path to file or raw JSON
    sheet_id = _env("GOOGLE_SHEET_ID")
    smtp_host = _env("SMTP_HOST")
    smtp_port = int(_env("SMTP_PORT", "587"))
    smtp_user = _env("SMTP_USER")
    smtp_password = _env("SMTP_PASSWORD")
    smtp_from = _env("SMTP_FROM")
    email_redirect_to = _env("EMAIL_REDIRECT_TO")  # safety: send every demo email here
    stripe_secret_key = _env("STRIPE_SECRET_KEY")
    slack_webhook_url = _env("SLACK_WEBHOOK_URL")
    discord_webhook_url = _env("DISCORD_WEBHOOK_URL")
    teams_webhook_url = _env("TEAMS_WEBHOOK_URL")
    telegram_bot_token = _env("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = _env("TELEGRAM_CHAT_ID")
    notion_token = _env("NOTION_TOKEN")
    notion_database_id = _env("NOTION_DATABASE_ID")
    hubspot_token = _env("HUBSPOT_TOKEN")
    # Generic webhook step: off (mock) unless enabled; optionally restricted to these hosts
    webhook_enabled = _env("WEBHOOK_ENABLED").lower() in ("1", "true", "yes")
    webhook_allowed_hosts = _env("WEBHOOK_ALLOWED_HOSTS")
    webhook_allow_http = _env("WEBHOOK_ALLOW_HTTP").lower() in ("1", "true", "yes")
    # Web search step: Tavily or Brave with a key; without one, DuckDuckGo instant answers + Wikipedia
    tavily_api_key = _env("TAVILY_API_KEY")
    brave_search_api_key = _env("BRAVE_SEARCH_API_KEY")
    web_user_agent = _env("WEB_USER_AGENT", "OrgTwin/0.1 (+https://github.com/clatsonhacks/Orgtwin)")

    # Optional access token for mutating endpoints on a public demo
    app_access_token = _env("APP_ACCESS_TOKEN")
    public_app_url = _env("PUBLIC_APP_URL", "http://localhost:3000")
    cors_origins = [o for o in _env("CORS_ORIGINS", "http://localhost:3000").split(",") if o]

    # Execution pacing (seconds between steps) so the 3D view can animate
    step_delay_s = float(_env("STEP_DELAY_S", "1.2"))

    def modes(self) -> dict:
        return {
            "llm": "vultr" if self.inference_api_key else "mock",
            "sheets": "google" if (self.google_sa_json and self.sheet_id) else "mock",
            "email": "smtp" if (self.smtp_host and self.smtp_from) else "mock",
            "stripe": "stripe-test" if self.stripe_secret_key.startswith("sk_test_") else "mock",
            "slack": "webhook" if self.slack_webhook_url else "mock",
            "runner": self.runner,
        }


settings = Settings()
