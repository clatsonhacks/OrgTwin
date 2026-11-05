# 0004 — Real connectors for the demo

- Date: 2026-11-05
- Status: accepted

## Decision
Four connectors, all used in lead-to-cash (0002):

| Connector | Used by | Step |
|---|---|---|
| Google Sheets (as the CRM) | Sales | Read leads, update deal status |
| SMTP email (e.g. Gmail) | Sales, Finance, Customer Success | Outreach, invoice, onboarding emails |
| Stripe (test mode) | Finance | Create the invoice |
| Slack | All | Notifications and run summaries |

Priority if time is short: Sheets → email → Stripe → Slack. Approvals live in the app; Slack only notifies.
Each connector falls back to a mock when unconfigured.

## Consequences
- Secrets only in `.env` on the VM, never committed.
- Stripe test mode only; live keys are refused.
- More connectors (MCP, marketplace) stay out of scope (0001).
