# Connectors

Agents can use these connectors in any designed or recorded workflow. Every connector runs in **demo mode** until
it is connected. In demo mode it records what it *would* have sent, which shows up in **📜 Activity**.

## Connecting from the app

Open **🔌 Connectors** (or click **Demo mode** in the top bar) and press **Connect** on any connector.

- **Google Sheets + Gmail** and **Stripe** open a sign-in popup from Google or Stripe. The user signs in and
  clicks **Allow**, and the popup closes itself. One Google sign-in covers both Sheets and email: emails go out
  from the user's own Gmail. After signing in, Sheets asks which sheet to use. The user can paste a link, or
  click **Create a sample CRM sheet for me** and OrgTwin makes one with the right columns and 5 sample leads.
- Other connectors (Slack, Discord, Teams, Telegram, Notion, HubSpot, web search, webhooks) open a short form.
  It explains where to get the value, has a button that opens that page, and runs a connection check on save.
- **Disconnect** forgets the saved values. For Google and Stripe it also revokes OrgTwin's access.

What users connect is stored in the API's database (`app_settings` table) and applies right away, with no
restart. Saved secrets are never sent back to the browser; the form only shows `••••` and the last 4 characters.
When `APP_ACCESS_TOKEN` is set, connecting needs the access code, same as every other change. Values in `.env`
still work, and anything connected in the app takes priority over them.

### One-click sign-in: the admin's one-time setup

The popups need OrgTwin registered once as an OAuth app. Until that is done, the Connect popup for these three
falls back to the form (service-account JSON / SMTP / `sk_test_` key) and says what is missing.

**Google** (Sheets + Gmail)
1. Google Cloud console → create a project → enable **Google Sheets API** and **Gmail API**.
2. *OAuth consent screen*: External; add the scopes `…/auth/spreadsheets` and `…/auth/gmail.send`. While the app
   is in *Testing*, add each user's address under *Test users* (Gmail send is a sensitive scope, so publishing
   the app to everyone needs Google's verification).
3. *Credentials → Create OAuth client ID → Web application*, with the authorized redirect URI
   `<PUBLIC_API_URL>/api/oauth/google/callback` (e.g. `https://orgtwin.example.com/api/oauth/google/callback`,
   or `http://localhost:8000/api/oauth/google/callback` for local dev).
4. Put the ID and secret in `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`.

**Stripe** (test mode only, like the rest of OrgTwin)
1. Stripe dashboard in **test mode** → *Settings → Connect* → enable OAuth.
2. Add the redirect URI `<PUBLIC_API_URL>/api/oauth/stripe/callback`.
3. Put the `ca_…` client ID in `STRIPE_CONNECT_CLIENT_ID` and the platform's `sk_test_…` key in
   `STRIPE_CONNECT_SECRET_KEY`. Connections that come back in live mode are refused.

`PUBLIC_API_URL` defaults to `PUBLIC_APP_URL`, which is right when Caddy serves `/api` on the same origin.
Google only accepts `https://` redirect URIs (or `http://localhost`), so a public deployment needs a domain
(`SITE_ADDRESS=<your domain>` gives automatic HTTPS).

## Steps and settings

| Connector | Step (`tool/action`) | Env | Approval by default |
|---|---|---|---|
| Discord | `discord/post {text}` | `DISCORD_WEBHOOK_URL` | yes |
| Microsoft Teams | `teams/post {text}` | `TEAMS_WEBHOOK_URL` (Workflows or classic connector) | yes |
| Telegram | `telegram/send {text, chat_id?}` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | yes |
| Notion | `notion/create_page {title, content}` | `NOTION_TOKEN`, `NOTION_DATABASE_ID` | no |
| HubSpot | `hubspot/upsert_contact {email, firstname, lastname, company, lifecyclestage}` | `HUBSPOT_TOKEN` | no |
| Stripe | `stripe/invoice {email, name, amount, description}` (test keys only) | `STRIPE_SECRET_KEY` | yes |
| Webhook | `webhook/request {url, method, body}` | `WEBHOOK_ENABLED`, `WEBHOOK_ALLOWED_HOSTS` | yes |
| Web search | `web/search {query, max_results, save_as}` · `web/read_page {url, save_as}` | none needed; `TAVILY_API_KEY` or `BRAVE_SEARCH_API_KEY` for full web results | no |

Web search works without a key: it uses DuckDuckGo's instant-answer API and Wikipedia search (both official,
free APIs; general search-results pages are not scraped). Add a Tavily or Brave Search key for full web results.
Results are saved as `{{search}}` (or the step's `save_as`) and later AI steps see them automatically.
`read_page` uses the same private-address guard as webhooks, checked again on every redirect.

Every parameter takes templates: `{{row.email}}`, `{{row.company}}`, `{{count}}`, `{{summary}}` (or any `save_as`
name from an earlier AI step). The AI that designs workflows sees this same list, and the offline planner
recognises phrases such as "post it on Discord", "sync them to HubSpot", "save it to Notion" and
"invoice them with Stripe".

## Adding a connector

Add a `Connector(...)` entry to `api/app/connectors/registry.py` with its actions, parameters and an async
handler. Workflow design, step normalisation, the engine, `GET /api/connectors` and the web step editor all read
from that one spec.

## API

- `GET /api/connectors` lists every connector with its `mode` (`live` or `mock`), env vars, setup notes, actions
  and, once connected, the `account` it is connected to.
- `GET /api/setup/connectors/{key}`: what the Connect popup shows (sign-in available?, form fields with masked
  values, steps). `PUT` saves form values and runs the check; `DELETE` disconnects.
- `POST /api/oauth/{google|stripe}/start` → `{url}` for the popup; the provider redirects back to
  `GET /api/oauth/{provider}/callback`. `POST /api/oauth/google/sample-sheet` creates and selects a sample sheet.
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
