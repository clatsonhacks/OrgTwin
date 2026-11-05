# OrgTwin — a live 3D operations twin of your company, run by AI agents on Vultr

> Vultr: Agent Rush Hackathon · Challenge 02 · **Powering the Future of Work**

Describe your company (or upload an org CSV) and OrgTwin builds it as a **block-world island** in an
isometric view: every team is a building, every role is a worker agent standing outside it, and each
building's beacon shows how many hours per week AI could take off people's plates. Click a team to fly into it, pick a role, and have the agent design a
workflow in plain language.

![OrgTwin world: each department is a building, each role a worker agent, beacons show automatable hours](docs/images/world.png)

![Live run: the Sales agent hands an invoice task to Finance as a crate; the hotbar tracks the 12 steps](docs/images/live-run.png)

One workflow runs **for real, end to end, across three departments: lead-to-cash.**

```
Sales agent ──A2A──▶ Finance agent ──(human approval)──▶ Customer Success agent
 pick lead from CRM     draft Stripe invoice     ✋ approve     finalize + email invoice
 score it in sandbox                                            hand off (A2A)
 qualify + draft email                                          personalised onboarding email
 send email, update CRM                                         Slack summary · hours saved
```

In the world you watch it happen: the active team's workers get busy and its windows light up, each
handoff travels the stone paths as a crate, a pending approval floats a "!" over HQ and Finance, and a
game-style hotbar at the bottom fills in as each of the 12 steps completes.

## Skill Recorder: show it once, the agent learns it

![Skill Recorder review: what it picked up from the recording, and the workflow it wrote](docs/images/skill-review.png)

Writing workflows is the slow part of any agent platform. In OrgTwin you **do the task once** instead:

1. **Show it.** Click a worker → *Record a skill*. Record your screen (any window or tab) while talking
   through what you do, or upload a screen recording you already have. Narration is transcribed live
   in the browser (Web Speech API), or you can type it.
2. **Learn.** Keyframes are pulled out of the video *in the browser*. A new frame is kept only when the
   screen really changed, plus one at least every 6 s. Up to 24 keyframes and the narration go to
   **Vultr Serverless Inference**: a vision model describes what happens in each frame (app, sheet,
   columns, filters, recipient), then an LLM turns observations + narration into an **executable**
   workflow. The raw video never leaves the browser; frames are not stored, only the text observations.
3. **Review & save.** You see what it picked up and the steps it wrote, edit anything, and save it as a
   skill on that role. The worker in the world now carries a book; the org-wide **Skill library** lists
   every skill by department.
4. **Run.** The agent runs the skill on live data: e.g. read every `status = New` row, write and send
   each person an email, mark the row `Contacted`, post a Slack summary. One approval card covers the
   whole batch and lists every recipient before anything is sent.

Step library the engine can run today: read sheet rows (with a filter), update a row, AI write, send
email (body written by the AI), human approval, Slack post — plus the connectors **Discord, Microsoft
Teams, Telegram, Notion, HubSpot, Stripe invoices and generic webhooks** (see
[docs/connectors.md](docs/connectors.md)). Templates like `{{row.email}}` and
`{{row.company}}` fill in each row. Workflows designed from a text description use the same format, so
they run too.

Try it without accounts: `sample-data/practice-desk.html` is a tiny spreadsheet + mail page to record
yourself on, and `sample-data/skill-demo.webm` is a ready-made 24 s recording to upload.

What it is and isn't: it learns **from a demonstration into an editable workflow**; it does not train
or fine-tune a model. When no vision model is available on the account it says so and learns from the
narration only.

## How Vultr powers it

| Vultr service | What it does in OrgTwin |
|---|---|
| **Cloud Compute VM** | Hosts the whole platform: Caddy (HTTPS), the Next.js web app, the FastAPI orchestrator + agent runtime, and Postgres, via Docker Compose. It is the control plane, not static hosting. |
| **Serverless Inference** (`https://api.vultrinference.com/v1`, OpenAI-compatible) | All agent reasoning: building the org from a description/website/CSV, designing workflows, qualifying leads and drafting outreach, writing emails. For the Skill Recorder, a vision model reads the recorded keyframes and an LLM writes the workflow. |
| **Isolated execution on the VM** | Lead scoring runs in a throwaway Docker container per run: `--network none`, read-only filesystem, 128 MB memory, 0.5 CPU, 64 PIDs, all capabilities dropped, non-root user, no secrets, killed after 20 s, destroyed after every run. |

No Vultr GPUs are used (they aren't available for this event).

## Architecture

```
Browser (3D: React Three Fiber)
   │  HTTPS
   ▼
Caddy ── /api/* ─▶ FastAPI orchestrator ──▶ Vultr Serverless Inference
   │                 │  ├─ workflow engine (lead-to-cash), A2A handoffs, approvals
   │                 │  ├─ connectors: Google Sheets · SMTP · Stripe (test) · Slack
   │                 │  ├─ sandbox runner ─▶ docker run --network none … (per run)
   │                 │  └─ audit log + live events (SSE)
   │                 ▼
   └── / ─▶ Next.js  Postgres (orgs, workflows, runs, approvals, audit log)
```

- **Agents & A2A:** each team has an agent (`GET /api/agents` returns its agent card and skills). Handoffs
  between departments are A2A-style task messages (`kind: task`, `from`/`to`, text + data parts), stored
  in the audit log and animated in 3D.
- **Human in the loop:** sending money-related messages needs a person's approval (in the app; a Slack
  notification links to it). Rejecting stops the run and marks the CRM row.
- **Safety for a public demo:** Stripe accepts **test keys only**. `EMAIL_REDIRECT_TO` sends every email
  to one inbox you control. `APP_ACCESS_TOKEN` can lock every endpoint that changes data.
- **Demo mode:** any missing credential falls back to a mock, and the top bar says which. That lets the
  app run with zero keys; with keys it does the real thing.

## Run locally

```bash
# API
cd api && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
RUNNER=local uvicorn app.main:app --port 8000      # RUNNER=docker for the isolated sandbox
# Web
cd web && npm install && NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
```
Tests: `cd api && pytest -q` (the end-to-end lead-to-cash run in demo mode, approval/rejection, CSV
import, and sandbox containment: no network, read-only, no secrets, timeout kills the container).

## Deploy on Vultr

1. **Credits:** claim the $200 hackathon credits (Billing → gift code).
2. **Inference:** Console → Products → Serverless → Inference → *Add Serverless Inference*. Copy the
   subscription's API key (not the account API key).
3. **VM:** deploy a Cloud Compute instance (Ubuntu 24.04, 2 vCPU / 4 GB is enough). Optionally point a
   domain's A record at its IP.
4. **Bootstrap:** SSH in as root and run:
   ```bash
   git clone https://github.com/clatsonhacks/Orgtwin.git /opt/orgtwin
   bash /opt/orgtwin/deploy/vultr-bootstrap.sh   # creates .env on first run
   nano /opt/orgtwin/.env                        # add keys (see below)
   bash /opt/orgtwin/deploy/vultr-bootstrap.sh   # builds and starts everything
   ```
5. Open `http://<VM IP>`, or `https://<your domain>` with `SITE_ADDRESS=<your domain>`.

### Keys (`.env`, never committed)

| Variable | Where to get it |
|---|---|
| `VULTR_INFERENCE_API_KEY` (+ optional `VULTR_INFERENCE_MODEL`) | Serverless Inference subscription |
| `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEET_ID` | Google Cloud service account JSON → put it in `secrets/google-sa.json`. Import `sample-data/leads.csv` into a sheet and share it with the service account's email. |
| `SMTP_*`, `EMAIL_REDIRECT_TO` | e.g. Gmail: `smtp.gmail.com`, port 587, an app password |
| `STRIPE_SECRET_KEY` | Stripe dashboard → test mode → `sk_test_…` |
| `SLACK_WEBHOOK_URL` | Slack app → Incoming Webhooks |
| `DISCORD_WEBHOOK_URL`, `TEAMS_WEBHOOK_URL`, `TELEGRAM_*`, `NOTION_*`, `HUBSPOT_TOKEN`, `WEBHOOK_*` | Optional connectors — see [docs/connectors.md](docs/connectors.md). Each stays in demo mode until set. |

## Known limits (honest list)

- Recorded and designed workflows run when they only use the step library and connectors above;
  sandbox-code and MCP steps can be designed but not run yet.
- Live screen recording needs a desktop browser with screen sharing (Chrome, Edge, Firefox); live
  transcription needs Chrome or Edge. Elsewhere you can upload a video and type the narration.
- Vision model availability on Vultr Serverless Inference is not confirmed yet; without one the
  recorder learns from the narration only (and says so).
- "Deal won" is simulated in the demo (in production it waits for the buyer's reply or signed order).
- The API container uses the host Docker socket to start sandbox containers, which gives it host-level
  power. Fine for a single-tenant demo VM; a production setup would put a socket proxy or a separate
  runner VM in between.
- Approvals happen in the app; Slack only notifies.

## Docs

Plans, architecture, decisions and the demo script live in [`docs/`](docs/README.md).

## Licence

MIT
