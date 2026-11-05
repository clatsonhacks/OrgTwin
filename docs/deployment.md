# Deployment and operations

## Run locally
```bash
# API
cd api && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
RUNNER=local uvicorn app.main:app --port 8000      # RUNNER=docker for the isolated sandbox
# Web (second terminal)
cd web && npm install && NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
```
Without keys everything runs in demo mode (mocks), and SQLite is used unless `DATABASE_URL` is set.

## Test
```bash
cd api && pytest -q        # sandbox tests need a running Docker daemon, otherwise they are skipped
cd web && npx tsc --noEmit && npm run build
```

## Full stack with Docker Compose
```bash
cp .env.example .env       # fill in keys, or leave empty for demo mode
docker compose up -d --build
open http://localhost
```
Services: `caddy` (80/443), `web` (Next.js standalone, :3000), `api` (FastAPI, :8000), `db` (Postgres 16).

## Deploy on Vultr
1. **Credits:** claim the hackathon credits (Billing → gift code).
2. **Inference:** Console → Products → Serverless → Inference → *Add Serverless Inference*. Copy the
   subscription's API key (not the account API key).
3. **VM:** Cloud Compute, Ubuntu 24.04, 2 vCPU / 4 GB. Optionally point a domain's A record at its IP.
4. **Bootstrap** (as root):
   ```bash
   git clone https://github.com/clatsonhacks/Orgtwin.git /opt/orgtwin
   bash /opt/orgtwin/deploy/vultr-bootstrap.sh   # installs Docker, opens 22/80/443, creates .env
   nano /opt/orgtwin/.env
   bash /opt/orgtwin/deploy/vultr-bootstrap.sh   # builds and starts everything
   ```
5. Open `http://<VM IP>`, or set `SITE_ADDRESS=<domain>` for automatic HTTPS via Caddy.

The bootstrap script honours `REPO_URL`, `BRANCH` and `APP_DIR` overrides.

## Configuration (`.env`, never committed)
| Variable | Purpose |
|---|---|
| `SITE_ADDRESS`, `PUBLIC_APP_URL` | `:80` for plain HTTP on the IP, or a domain for HTTPS |
| `POSTGRES_PASSWORD` | Database password |
| `APP_ACCESS_TOKEN` | If set, write endpoints require `X-Access-Token` |
| `VULTR_INFERENCE_API_KEY`, `VULTR_INFERENCE_BASE_URL`, `VULTR_INFERENCE_MODEL` | Serverless Inference; empty model = first from `/models` |
| `VULTR_VISION_MODEL` | Vision model for the recorder; empty = auto-pick; `none` = narration only |
| `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEET_ID` | CRM sheet (import `sample-data/leads.csv`, share with the service account) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Outbound email |
| `EMAIL_REDIRECT_TO` | Send every email to this inbox (demo safety) |
| `STRIPE_SECRET_KEY` | `sk_test_…` only; live keys are refused |
| `SLACK_WEBHOOK_URL` | Incoming webhook |
| `SANDBOX_IMAGE`, `SANDBOX_TIMEOUT_S`, `SANDBOX_MEMORY` | Sandbox container settings |
| `STEP_DELAY_S` | Pause between steps so the demo is watchable |

Put the Google service-account file in `secrets/google-sa.json` (mounted read-only at `/secrets`).

## Operations
- Logs: `docker compose logs -f api`
- Status: `GET /api/status` shows real vs mock per integration and the vision model in use.
- Reset demo data: `POST /api/demo/reset`.
- Update: `git pull && docker compose up -d --build`.
