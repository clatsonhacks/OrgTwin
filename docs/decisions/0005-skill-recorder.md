# 0005 — Add the Skill Recorder

- Date: 2026-11-05
- Status: accepted

## Context
OrgTwin's weak spot: someone still has to describe every workflow. We want agents to learn a workflow by
watching a person do it once (open a spreadsheet, read columns, send emails) and to present this as an
org-level skill library.

## Decision
1. **Capture** in the browser: screen recording or uploaded video, plus spoken or typed narration.
   Keyframes are extracted client-side; the raw video never leaves the browser.
2. **Understand:** a vision model on Vultr Serverless Inference describes keyframes (when available), and
   an LLM turns observations + narration into an executable workflow. Without a vision model it uses the
   narration only and says so.
3. **Review:** the person edits and saves the steps as a skill on a role; the skill shows on that worker
   and in the org-wide skill library.
4. **Run:** a generic engine executes a small step library (read rows, per-row loop, AI write, send email,
   update row, approval, Slack post). Risky batches need one approval.

Wording: "learns from a demonstration into an editable workflow"; never "trains a model".

## Consequences
- "Design workflow" drafts use the same executable schema, so they're runnable too.
- Privacy: only keyframes (in memory) and text observations reach the API; frames are not stored.
- Prior art exists (Scribe, Tango, UiPath Task Capture, Power Automate task mining); our angle is skills
  attached to roles in the twin and run by agents with approvals.
