# Plan

## Goal
Ship OrgTwin as a production-style web app on Vultr for **Vultr: Agent Rush, Challenge 02 · Powering
the Future of Work** (see [hackathon.md](hackathon.md)), with a public demo URL, video, slides and a
public GitHub repo.

**Submission deadline: 2026-11-09 04:30 IST (= 2026-11-08 23:00 UTC = 16:00 MST).**

## Scope

### In
- Org generation from a description, website URL or CSV; automation-opportunity heatmap.
- Isometric 3D world (React Three Fiber) with a 2D team drawer, HUD, approvals and activity panels.
- Hero workflow **lead-to-cash** across Sales → Finance → Customer Success, running for real
  ([lead-to-cash.md](lead-to-cash.md)).
- A2A-style handoffs, human approvals, audit log, live events over SSE.
- Connectors: Google Sheets (CRM), SMTP email, Stripe (test mode), Slack webhook.
- Isolated Docker sandbox per run for code steps.
- **Skill Recorder**: record or upload → learn → review → save → run ([skill-recorder.md](skill-recorder.md)).
- Workflows designed from a text description using the same executable schema.
- Deploy on one Vultr VM with Docker Compose + Caddy.

### Out (for now)
- A full MCP / skills marketplace and many connectors.
- Executing designed steps outside the step library (MCP, arbitrary HTTP, Stripe outside lead-to-cash).
- Multi-tenant hosting and user accounts.
- Model training or fine-tuning (the recorder learns *into a workflow*, it doesn't train a model).

## Milestones
| # | Milestone | Status |
|---|---|---|
| 1 | Pick the idea and challenge ([ADR 0001](decisions/0001-choose-orgtwin.md)) | done |
| 2 | Hero workflow, stack, connectors ([0002](decisions/0002-hero-workflow-lead-to-cash.md), [0003](decisions/0003-stack-and-vultr-deploy.md), [0004](decisions/0004-connectors.md)) | done |
| 3 | API: org generation, lead-to-cash engine, sandbox, connectors, tests | done |
| 4 | Web app: 3D world, HUD, team drawer, approvals; Compose + Caddy deploy | done |
| 5 | Voxel world redesign | done |
| 6 | Skill Recorder backend: step library, generic engine, recording analysis ([0005](decisions/0005-skill-recorder.md)) | done |
| 7 | Skill Recorder UI: record/upload, learn, review, save, run | done |
| 8 | Deploy on Vultr with real keys; confirm a vision model on the live model list | to do |
| 9 | Demo video, slides, cover image | to do |
| 10 | Submit on lablab.ai | to do |

## Verification (current)
- `api`: `pytest -q` → 10 passed (lead-to-cash end to end in demo mode, approve/reject, CSV import,
  skills, sandbox containment with Docker: no network, read-only, no secrets, timeout).
- `web`: `tsc --noEmit` clean, `next build` succeeds.
- Docker Compose stack (Caddy, web, API, Postgres) starts; a lead-to-cash run through Caddy completes
  all 12 steps with one approval; the UI loads without console errors.

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| "Pretty but shallow" | One workflow does real actions end to end, visible in 3D |
| No vision model on Vultr Serverless Inference | Recorder falls back to narration only and says so |
| Wrong step extraction in a live demo | Review/edit screen before saving; practice page + prerecorded video in `sample-data/` |
| Recordings contain personal data | Video never leaves the browser; frames processed in memory only; only text observations kept |
| Real emails/payments during a public demo | `EMAIL_REDIRECT_TO`, Stripe test keys only, `APP_ACCESS_TOKEN` |
| Docker socket gives the API host-level power | Acceptable on a single-tenant demo VM; production would add a socket proxy or a separate runner VM |
| 3D performance / accessibility | 2D panels for all editing; department list in the HUD |

## Submission checklist
lablab form:
- [ ] Project title, short and long description
- [ ] Technology & category tags (incl. VULTR)
- [ ] Cover image
- [ ] Video presentation
- [ ] Slide presentation
- [x] Public GitHub repository (this repo)
- [ ] Demo application platform + public application URL running on Vultr
- [ ] Team members added (1–6)

README must explain:
- [x] Vultr services and infrastructure used
- [x] Backend/deployment architecture and how Vultr powers it
- [x] Agent use case and how it executes real tasks
- [x] Setup instructions
- [x] MIT licence, original work

Optional:
- [ ] Share on Vultr Community
- [ ] Attend on-site in Salt Lake City (raffle eligibility)
