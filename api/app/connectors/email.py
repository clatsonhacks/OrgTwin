"""Email via SMTP (e.g. Gmail with an app password). Demo mode keeps an in-memory outbox."""
import asyncio
import smtplib
from email.message import EmailMessage

from ..config import settings

outbox: list[dict] = []


def mode() -> str:
    return settings.modes()["email"]


def _send_smtp(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
        s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


async def send(to: str, subject: str, body: str) -> dict:
    # Safety for a public demo: every email can be redirected to one inbox you control.
    actual_to = settings.email_redirect_to or to
    record = {"intended_to": to, "to": actual_to, "subject": subject, "body": body, "mode": mode()}
    if mode() == "smtp":
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, actual_to, subject
        if actual_to != to:
            body = f"[OrgTwin demo — originally addressed to {to}]\n\n{body}"
        msg.set_content(body)
        await asyncio.to_thread(_send_smtp, msg)
    outbox.append(record)
    return record
