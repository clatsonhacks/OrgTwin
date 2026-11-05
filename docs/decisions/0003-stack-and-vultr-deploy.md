# 0003 — Tech stack, deployed on Vultr

- Date: 2026-11-05
- Status: accepted

## Context
The event requires a Vultr VM backend, a web app deployed on Vultr, and Vultr as the central backend.
No Vultr GPUs are available. Serverless Inference is optional for Challenge 2 but strengthens the use of
Vultr technology.

## Decision
- **Frontend:** Next.js + React Three Fiber (world) + 2D panels for editing.
- **Backend:** Python FastAPI orchestrator: org generation, workflow engine, agent runtime, A2A, approvals,
  audit log.
- **AI:** Vultr Serverless Inference (OpenAI-compatible, `https://api.vultrinference.com/v1`).
- **Data:** Postgres.
- **Runs:** code steps in a Docker container with time/memory caps; connector secrets stay in the
  orchestrator.
- **Deploy:** one Vultr Cloud Compute VM, Docker Compose (web, api, postgres) behind Caddy for HTTPS.
  Optional later: Vultr Managed Database.

## Consequences
- The README documents the architecture and how Vultr powers it.
- HTTPS needs a domain; otherwise serve HTTP on the VM IP.
