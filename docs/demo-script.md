# Pitch and demo script

**One-liner:** OrgTwin is a living twin of your company where every role has an AI agent, and your best
people teach those agents by doing the task once on screen.

## Demo (≤ 3 min)
1. **0:00** Paste a company description → the world builds itself. Point at the heat beacons.
2. **0:30** Click Finance → AR Specialist → **Record a skill**. Record 20 s: open the invoices sheet,
   filter Overdue, write one reminder email, send, mark Reminded. Talk while doing it.
3. **1:10** Stop → "What I picked up" + the workflow it wrote. Save → a book appears over the worker.
4. **1:35** Skill library → Run. The approval card lists every recipient → Approve. Emails go out row by
   row in the hotbar; the sheet updates live.
5. **2:15** Run lead-to-cash: crate flies Sales → Finance, approve the invoice, onboarding email.
6. **2:45** Close on hours saved: "every skill your team shows once becomes something every agent can do."

Backup: if live recording misbehaves, upload `sample-data/skill-demo.webm`.

## Talking points
- Problem: agent builders still need someone to *describe* every workflow.
- Solution: build the twin → teach by showing → agents do it, people approve → see the work.
- Everything runs on Vultr: one Cloud Compute VM (web, orchestrator, Postgres, Caddy, sandboxes) and
  Serverless Inference for all reasoning, including vision on recording keyframes.
- Trust: sandbox with no network and no secrets, human approval before anything leaves, audit log.

## Words to use / avoid
- Use: "learns from a demonstration", "turns a screen recording into an editable workflow",
  "an org-level skill library", "human approval before anything leaves".
- Avoid: "trains the agent/model", "fully autonomous", "first ever" (Scribe, Tango, UiPath Task Capture
  and Power Automate task mining exist; our difference is the org-shaped skill library run by agents with
  approvals).
