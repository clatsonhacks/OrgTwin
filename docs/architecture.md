# Architecture

```
Browser (Next.js + React Three Fiber)
   │  HTTPS
   ▼
Caddy ── /api/* ─▶ FastAPI orchestrator ──▶ Vultr Serverless Inference (OpenAI-compatible)
   │                 │  ├─ org generation (description / URL / CSV)
   │                 │  ├─ workflow engine: lead-to-cash + generic skill engine
   │                 │  ├─ A2A handoffs, human approvals
   │                 │  ├─ connectors: Google Sheets · SMTP · Stripe (test) · Slack
   │                 │  ├─ sandbox runner ─▶ docker run --network none … (one container per run)
   │                 │  └─ audit log + live events (SSE)
   │                 ▼
   └── / ─▶ Next.js  Postgres (orgs, workflows, runs, approvals, events)
```

Everything runs on one Vultr Cloud Compute VM via Docker Compose: `caddy`, `web`, `api`, `db`.

## Components

### Web (`web/`)
- Next.js app router, standalone build; one page (`app/page.tsx`).
- `components/world/`: the isometric voxel world (React Three Fiber). Buildings per team, worker agents
  per role, heat beacons, crates for handoffs, "!" markers for pending approvals.
- `components/ui/`: `StartCard` (describe/URL/CSV), `Hud` (metrics, hotbar, activity, skills),
  `TeamDrawer` (roles, workflows), `StepEditor`, `SkillRecorder`.
- `lib/api.ts`: typed client; `lib/capture.ts`: screen capture, keyframe extraction, speech-to-text.
- Same-origin API in production (`NEXT_PUBLIC_API_BASE=""`; Caddy routes `/api/*`).

### API (`api/app/`)
| Module | Role |
|---|---|
| `main.py` | FastAPI routes, token guard, SSE stream |
| `config.py` | Settings from env; reports which integrations are real vs mock |
| `db.py` | SQLAlchemy models (Postgres in Compose, SQLite by default locally) |
| `llm.py` | Vultr Serverless Inference client (chat + vision), model auto-pick, mock fallback |
| `orggen.py` | Builds the org (teams, roles, automatable hours) from description / URL / CSV |
| `engine.py` | Lead-to-cash run, approvals, metrics |
| `workflows.py` | Executable workflow schema + "design workflow from text" |
| `skills.py` | Skill Recorder: observe keyframes, plan steps |
| `runner.py` | Sandbox runner (`local` or `docker`) |
| `a2a.py` | Agent cards and A2A task messages |
| `events.py` | Event bus → audit log + SSE |
| `connectors/` | `sheets`, `email`, `stripe_c`, `slack`, each with a mock |

## Data model
| Table | Key fields |
|---|---|
| `orgs` | `name`, `data` (teams/roles/hours JSON), `source` (`vultr`/`mock`) |
| `workflows` | `org_id`, `team_key`, `role_key`, `name`, `goal`, `kind` (`hero`/`draft`/`skill`), `steps`, `meta` |
| `runs` | `workflow_id`, `status`, `context`, `finished_at` |
| `approvals` | `run_id`, `team_key`, `title`, `payload`, `status` (`pending`/`approved`/`rejected`), `decided_by` |
| `events` | append-only audit log: `run_id`, `type`, `payload` (also streamed to the UI) |

## HTTP API
| Method & path | Purpose |
|---|---|
| `GET /api/health`, `GET /api/status` | Liveness; which integrations are real vs mock, vision model |
| `POST /api/org/generate`, `GET /api/org` | Build / read the org twin |
| `GET /api/agents` | Agent cards and skills per team |
| `GET /api/workflows`, `POST /api/workflows/generate`, `PUT /api/workflows/{id}` | Workflows: list, design from text, edit |
| `GET /api/workflows/{id}/preview`, `POST /api/workflows/{id}/run` | Preview rows a skill would touch; run it |
| `POST /api/skills/analyze`, `POST /api/skills`, `GET /api/skills` | Skill Recorder: learn from a recording; save; library |
| `GET /api/sheet/columns` | CRM columns for the step editor |
| `POST /api/runs`, `GET /api/runs`, `GET /api/runs/{id}` | Start / list / inspect lead-to-cash runs |
| `GET /api/approvals`, `POST /api/approvals/{id}` | Approval inbox; approve or reject |
| `GET /api/outbox`, `GET /api/audit`, `GET /api/metrics` | Sent messages, audit log, hours saved |
| `GET /api/events/stream` | Server-sent events for the live world |
| `POST /api/demo/reset` | Reset demo data |

Endpoints that change data require `X-Access-Token` when `APP_ACCESS_TOKEN` is set.

## Agents and A2A
Each team has an agent with an agent card (`GET /api/agents`). Handoffs between departments are
A2A-style task messages (`kind: task`, `from`/`to`, text + data parts). They are stored in the audit log
and animated in the world as crates travelling between buildings.

## Safety model
- **Sandbox:** code steps (lead scoring) run in a throwaway container per run: `--network none`,
  read-only filesystem, 128 MB memory, 0.5 CPU, 64 PIDs, all capabilities dropped, non-root, no secrets,
  killed after 20 s, removed after the run. Covered by `tests/test_sandbox.py`.
- **Secrets** stay in the orchestrator's environment; they never enter a sandbox container.
- **Human in the loop:** anything money-related or batch-sending needs approval in the app (Slack only
  notifies). Rejecting stops the run and marks the CRM row.
- **Demo guards:** Stripe test keys only (live keys refused); `EMAIL_REDIRECT_TO` sends every email to
  one inbox; `APP_ACCESS_TOKEN` locks write endpoints.
- **Demo mode:** a missing credential falls back to a mock; the top bar shows which.
- **Known trade-off:** the API mounts the host Docker socket to start sandboxes. Fine for a
  single-tenant VM; production would use a socket proxy or a separate runner VM.

## How Vultr powers it
| Vultr service | Use |
|---|---|
| Cloud Compute VM | Hosts the whole control plane: Caddy, web, orchestrator + agent runtime, Postgres, sandbox runs |
| Serverless Inference (`https://api.vultrinference.com/v1`) | All reasoning: org generation, workflow design, lead qualification, emails, vision on recording keyframes |

No GPUs are used.
