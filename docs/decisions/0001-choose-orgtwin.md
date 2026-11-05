# 0001 — Build OrgTwin for Challenge 2 (Future of Work)

- Date: 2026-11-05
- Status: accepted

## Context
Vultr: Agent Rush has two challenges with one shared prize pool. Early public submissions all targeted
Challenge 1 (Blast Radius Zero) with similar ideas. The first concept was a 3D org chart with a generic
workflow builder; it scored 13/20 on Fit / Wow / Feasible / Prize. The refined concept, OrgTwin, scored 17.

## Options
1. 3D org + generic workflow builder (Challenge 2), 13/20
2. OrgTwin: a live 3D operations twin with an agent per role, an automation heatmap, and one real
   cross-team workflow (Challenge 2), 17/20
3. A Challenge 1 sandboxed agent: crowded, hard to stand out on originality

## Decision
Build **OrgTwin** for **Challenge 2 · Powering the Future of Work**.
- 3D is for navigating and showing live operations; editing happens in 2D panels.
- Depth over breadth: one hero cross-team workflow that runs for real, end to end.
- Departments hand work to each other via A2A messages, with human approvals and an audit log.
- Vultr is the central backend: a VM for the orchestrator and agent runtime, Serverless Inference for all
  reasoning, isolated containers for runs.

## Consequences
- Hero workflow, stack and connectors each get their own record (0002–0004).
- Generic builder features (MCP/skills marketplace, many connectors) are out of scope unless they serve the
  hero workflow.
- Needs a public demo URL on Vultr, a demo video, slides and a README covering the Vultr architecture.
