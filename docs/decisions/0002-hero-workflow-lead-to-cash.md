# 0002 — Hero workflow: lead-to-cash

- Date: 2026-11-05
- Status: accepted

## Context
0001 says OrgTwin runs one cross-team workflow for real. Candidates: lead-to-cash, hire-to-onboard,
ticket-to-resolution.

## Decision
**Lead-to-cash** across three teams:
1. **Sales agent:** picks a new lead from the CRM, scores and qualifies it, drafts and sends outreach.
2. Deal won → **A2A handoff** to Finance (visible as a crate in the world).
3. **Finance agent:** creates the invoice → **human approval**.
4. Approved → invoice email → **A2A handoff** to Customer Success.
5. **Customer Success agent:** onboarding email and a summary to the team channel.
6. Metrics: run timeline, audit log, hours saved.

## Consequences
- The demo crosses three departments, so handoffs and approvals are visible.
- Other workflows appear as designed (not executed) unless they fit the step library.
