"""Stripe invoices — TEST MODE ONLY. Live keys are refused."""
import asyncio
import uuid

from ..config import settings

CURRENCY = "usd"


def mode() -> str:
    return settings.modes()["stripe"]


def _stripe():
    import stripe
    if not settings.stripe_secret_key.startswith("sk_test_"):
        raise RuntimeError("Refusing to use a non-test Stripe key")
    stripe.api_key = settings.stripe_secret_key
    # Retry dropped connections; the SDK sends an idempotency key, so a retried create can't duplicate.
    stripe.max_network_retries = 3
    return stripe


def _create_draft(email: str, name: str, amount_cents: int, description: str) -> dict:
    stripe = _stripe()
    found = stripe.Customer.search(query=f"email:'{email}'", limit=1)
    customer = found.data[0] if found.data else stripe.Customer.create(email=email, name=name)
    # Set the currency explicitly: otherwise the invoice takes the account's default (e.g. SGD), and
    # Stripe refuses to mix it with the USD item. Amounts across the app are USD.
    inv = stripe.Invoice.create(customer=customer.id, collection_method="send_invoice", currency=CURRENCY,
                                days_until_due=14, auto_advance=False)
    stripe.InvoiceItem.create(customer=customer.id, invoice=inv.id, amount=amount_cents,
                              currency=CURRENCY, description=description)
    inv = stripe.Invoice.retrieve(inv.id)
    return {"invoice_id": inv.id, "amount_due": inv.amount_due, "status": inv.status}


def _finalize(invoice_id: str) -> dict:
    stripe = _stripe()
    inv = stripe.Invoice.finalize_invoice(invoice_id)
    return {"invoice_id": inv.id, "status": inv.status, "hosted_invoice_url": inv.hosted_invoice_url,
            "amount_due": inv.amount_due}


async def create_draft_invoice(email: str, name: str, amount_cents: int, description: str) -> dict:
    if mode() == "mock":
        return {"invoice_id": f"in_mock_{uuid.uuid4().hex[:10]}", "amount_due": amount_cents,
                "status": "draft", "mode": "mock"}
    out = await asyncio.to_thread(_create_draft, email, name, amount_cents, description)
    return {**out, "mode": "stripe-test"}


async def finalize_invoice(invoice_id: str, amount_cents: int) -> dict:
    if mode() == "mock":
        return {"invoice_id": invoice_id, "status": "open", "amount_due": amount_cents,
                "hosted_invoice_url": None, "mode": "mock"}
    out = await asyncio.to_thread(_finalize, invoice_id)
    return {**out, "mode": "stripe-test"}
