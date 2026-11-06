# OrgTwin: pending tasks

Everything still to do, in order. Tick a box when it's done.
The empty settings are already in your `.env`: fill in a value, save, and restart the API.

## Where things stand (6 Nov 2026)

| Area | Status |
|---|---|
| App running locally | ✅ http://localhost:3000 (API on :8000) |
| Vultr AI (text + vision) | ✅ `qwen3.8-27b`, `mimo-v2.6-pro` |
| Cross-team workflows, web search, walking office | ✅ on `main` |
| One-click **Connect** buttons | ⏳ code done, **each service needs a one-time registration** (section 1) |
| Gmail / Sheets / Stripe / Slack / Discord / HubSpot / Notion | ⏳ demo mode until connected |
| Deployed on Vultr | ⏳ not yet (section 2) |

---

## 1. Easy integrations: one-time registration per service

**Why this step exists.** Claude's "Connect Gmail" popup works because Anthropic registered *Claude for Gmail* with
Google once. OrgTwin needs the same: register **OrgTwin** once with each service, and you get a client ID and
secret. After that, everyone who uses the app just clicks **Connect → choose account → Allow**. Nobody copies
keys or passwords.

Every return address ("redirect URI") has the same form:

| Where the app runs | Redirect URI |
|---|---|
| Your PC (now) | `http://localhost:8000/api/oauth/<service>/callback` |
| Vultr with a domain (later) | `https://<your-domain>/api/oauth/<service>/callback` |

You can add both addresses to the same registration.

### 1.1 Google: Gmail + Google Sheets (do this first) · ~10 min · works locally ✅
- [ ] Go to https://console.cloud.google.com → **New project** → name it `OrgTwin`.
- [ ] **APIs & Services → Library**: enable **Gmail API** and **Google Sheets API**.
- [ ] **APIs & Services → OAuth consent screen**:
  - User type **External**; app name `OrgTwin`; support and developer email: yours.
  - Scopes: `…/auth/spreadsheets` and `…/auth/gmail.send`.
  - **Test users**: add `clatsonhacks@gmail.com`, plus anyone else who will connect (max 100 while in Testing).
- [ ] **Credentials → Create credentials → OAuth client ID** → type **Web application**.
  - Authorized redirect URI: `http://localhost:8000/api/oauth/google/callback`
- [ ] In `.env`: `GOOGLE_OAUTH_CLIENT_ID=` and `GOOGLE_OAUTH_CLIENT_SECRET=`
- [ ] Restart the API → 🔌 Connectors → Google Sheets or Gmail → **Connect** → choose account → **Allow**.
- [ ] Optional: in the same popup, click **Create a sample sheet**. It makes a ready-made leads sheet in your Drive.

> You don't need a Gmail **app password** for this. The "not available for your account" page only matters for the
> old SMTP route, and it needs 2-Step Verification turned on.
> Until Google reviews the app (section 3) you'll see **"Google hasn't verified this app"**: click
> **Advanced → Go to OrgTwin**. This is normal for apps in Testing mode.

### 1.2 Discord · ~3 min · works locally ✅
- [ ] https://discord.com/developers/applications → **New Application** → `OrgTwin`.
- [ ] **OAuth2**: copy the **Client ID**; click **Reset Secret** and copy the **Client Secret**.
- [ ] **OAuth2 → Redirects**: add `http://localhost:8000/api/oauth/discord/callback`.
- [ ] In `.env`: `DISCORD_CLIENT_ID=` and `DISCORD_CLIENT_SECRET=`.
- [ ] Restart the API → Discord card → **Connect** → pick a server and channel → **Authorize**.

### 1.3 HubSpot · ~5 min · works locally ✅
- [ ] https://developers.hubspot.com → **Apps → Create app**.
- [ ] **Auth**: scopes `crm.objects.contacts.read` and `crm.objects.contacts.write`.
- [ ] Redirect URL: `http://localhost:8000/api/oauth/hubspot/callback`
- [ ] In `.env`: `HUBSPOT_CLIENT_ID=` and `HUBSPOT_CLIENT_SECRET=`.

### 1.4 Stripe (test mode) · ~5 min · works locally ✅
- [ ] Stripe dashboard, switch to **Test mode** → **Settings → Connect** → enable OAuth.
- [ ] Redirect URI: `http://localhost:8000/api/oauth/stripe/callback`
- [ ] In `.env`: `STRIPE_CONNECT_CLIENT_ID=ca_…` and `STRIPE_CONNECT_SECRET_KEY=sk_test_…`.
  OrgTwin refuses live keys and live-mode connections.

### 1.5 Telegram · ~2 min · works locally ✅ (no registration, just a bot)
- [ ] In Telegram, message **@BotFather** → `/newbot` → name it `OrgTwin` → copy the token.
- [ ] In `.env`: `TELEGRAM_BOT_TOKEN=`
- [ ] Users connect by tapping **Add to a Telegram group** and pressing **Start**. No chat ID needed.

### 1.6 Notion · ~5 min · may need the https domain ⚠️
- [ ] https://www.notion.so/profile/integrations → **New integration** → type **Public**.
- [ ] Redirect URI: try `http://localhost:8000/api/oauth/notion/callback`; if Notion refuses it, use the https one after section 2.
- [ ] In `.env`: `NOTION_OAUTH_CLIENT_ID=` and `NOTION_OAUTH_CLIENT_SECRET=`.

### 1.7 Slack · ~5 min · needs the https domain (after section 2) ⚠️
- [ ] https://api.slack.com/apps → **Create New App** → From scratch → `OrgTwin`.
- [ ] **OAuth & Permissions**: add the scope `incoming-webhook`; redirect URL `https://<your-domain>/api/oauth/slack/callback`.
  Slack only accepts https redirect addresses.
- [ ] **Manage Distribution** → make it public if people outside your workspace will connect.
- [ ] **Basic Information** → in `.env`: `SLACK_CLIENT_ID=` and `SLACK_CLIENT_SECRET=`.
- [ ] Users then see Slack's own **"Sign in to your workspace"** page, pick a channel, and click Allow.
- Until then: paste an Incoming Webhook URL in the Slack card.

### 1.8 Microsoft Teams: stays a pasted webhook
One-click for Teams needs a Microsoft Graph app with admin consent in every company's tenant. Use
Teams → channel → **Workflows → "Post to a channel when a webhook request is received"** and paste the URL.

---

## 2. Deploy on Vultr

- [ ] **Fix the Vultr account API key.** `VULTR_API_KEY` in your Windows user environment is only 1 character long:
      the paste failed. Set it again (paste with Ctrl+Shift+V), and allow your IP under Vultr → Account → API →
      **Access Control**. Only needed if Claude should create the server for you.
- [ ] Claim the hackathon credits (Billing → gift code).
- [ ] Create the VM: Ubuntu 24.04, 2 vCPU / 4 GB.
- [ ] **Get a domain** and point its A record at the VM's IP. A domain is needed for HTTPS, and Slack, Google
      (outside localhost) and possibly Notion only return to https addresses.
- [ ] On the VM: `git clone … /opt/orgtwin` → `bash deploy/vultr-bootstrap.sh` → edit `/opt/orgtwin/.env` → run the script again.
- [ ] Server `.env` must include:
  - `SITE_ADDRESS=<your-domain>` (gives automatic HTTPS)
  - `PUBLIC_APP_URL=https://<your-domain>` (yours still says `http://YOUR_VM_IP`)
  - `PUBLIC_API_URL=https://<your-domain>`
  - `CORS_ORIGINS=https://<your-domain>`
  - `VULTR_INFERENCE_API_KEY`, `VULTR_INFERENCE_MODEL=qwen3.8-27b`, `VULTR_VISION_MODEL=mimo-v2.6-pro`
  - `APP_ACCESS_TOKEN=<something long>` if the demo is public
  - every client ID and secret from section 1
- [ ] Add the https redirect URIs to each registration from section 1. Keep the localhost ones too.
- [ ] Do 1.7 Slack, and 1.6 Notion if it refused localhost.

## 3. Going public (only if strangers will use it)

- [ ] **Google verification**, to remove the "unverified app" warning and the 100-test-user limit. Gmail send is a
      sensitive scope. You need a homepage and privacy policy on your domain, domain verification in Search
      Console, then OAuth consent screen → **Publish app** → submit for review. This takes days to weeks.
- [ ] Slack: **Manage Distribution → Activate public distribution**.
- [ ] HubSpot: fine for your own portals; listing in the marketplace is a separate review.

## 4. Optional keys

- [ ] Web search: `TAVILY_API_KEY` (tavily.com) or `BRAVE_SEARCH_API_KEY` (brave.com/search/api) for full web results.
      Without a key it uses DuckDuckGo instant answers + Wikipedia only.
- [ ] Email safety for demos: `EMAIL_REDIRECT_TO=<your inbox>` sends every email to you instead of the real recipient.
- [ ] Start **Docker Desktop** locally to run the isolated sandbox and its 3 skipped tests (`RUNNER=docker`).

## 5. Code follow-ups (known gaps)

- [ ] Onboarding email body repeats a "Subject:" line (prompt fix).
- [ ] Office workers walk through each other (no collision avoidance).
- [ ] `sandbox` and `mcp` steps can be designed but not run by the generic engine.
- [ ] Approvals happen in the app only; Slack just notifies (no approve button in Slack).
- [ ] "Deal won" is simulated in lead-to-cash (should wait for the buyer's reply or signed order).
- [ ] The API container uses the host Docker socket for the sandbox. Fine for a demo VM; production would use a socket proxy or a separate runner VM.
