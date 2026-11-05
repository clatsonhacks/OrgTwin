# Connectors

Agents can use these connectors in any designed or recorded workflow. Every connector runs in **demo mode** until
its env vars are set. In demo mode it records what it *would* have sent, which shows up in **📜 Activity**. Open
**🔌 Connectors** in the app to see which ones are live, what each needs, and to run a connection check.

| Connector | Step (`tool/action`) | Env | Approval by default |
|---|---|---|---|
| Discord | `discord/post {text}` | `DISCORD_WEBHOOK_URL` | yes |
| Microsoft Teams | `teams/post {text}` | `TEAMS_WEBHOOK_URL` (Workflows or classic connector) | yes |
| Telegram | `telegram/send {text, chat_id?}` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | yes |
| Notion | `notion/create_page {title, content}` | `NOTION_TOKEN`, `NOTION_DATABASE_ID` | no |
| HubSpot | `hubspot/upsert_contact {email, firstname, lastname, company, lifecyclestage}` | `HUBSPOT_TOKEN` | no |
| Stripe | `stripe/invoice {email, name, amount, description}` (test keys only) | `STRIPE_SECRET_KEY` | yes |
| Webhook | `webhook/request {url, method, body}` | `WEBHOOK_ENABLED`, `WEBHOOK_ALLOWED_HOSTS` | yes |

Every parameter takes templates: `{{row.email}}`, `{{row.company}}`, `{{count}}`, `{{summary}}` (or any `save_as`
name from an earlier AI step). The AI that designs workflows sees this same list, and the offline planner
recognises phrases such as "post it on Discord", "sync them to HubSpot", "save it to Notion" and
"invoice them with Stripe".

## Adding a connector

Add a `Connector(...)` entry to `api/app/connectors/registry.py` with its actions, parameters and an async
handler. Workflow design, step normalisation, the engine, `GET /api/connectors` and the web step editor all read
from that one spec.

## API

- `GET /api/connectors` lists every connector with its `mode` (`live` or `mock`), env vars, setup notes and actions.
- `POST /api/connectors/{key}/test` (with `{"url": …}` for webhook) checks the connection. Chat connectors send a
  short test message; Notion, HubSpot and Stripe make a read-only call. In demo mode nothing is sent.

## Webhook safety

The webhook step calls URLs that come from workflow text, so:

- only `https` is allowed (`WEBHOOK_ALLOW_HTTP=true` permits `http`);
- the host must resolve to public addresses only (no loopback, private, link-local or cloud-metadata ranges);
- `WEBHOOK_ALLOWED_HOSTS=hooks.zapier.com,hook.eu1.make.com` restricts targets further;
- redirects are not followed;
- it stays in demo mode until `WEBHOOK_ENABLED=true`.

The resolve-then-connect check does not stop DNS rebinding. On a public deployment, set `WEBHOOK_ALLOWED_HOSTS`.
