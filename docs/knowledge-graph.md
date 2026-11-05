# Offices, people and the org knowledge graph

## Step inside a department

Click a building once to select it, then click it again (or press **🚪 Step inside**). The camera moves into a
voxel office. Everyone in the department sits at a desk, grouped in rows by role. Each role shows up to six
desks; extra headcount appears as `+N` on the role sign.

People have stable names and a desk id `<role_key>#<seat>`, for example `ar-specialist#0`. Click someone to see
their role, their tasks and what they own. From there you can:

- **Give them a skill.** Pick any recorded skill or workflow in the company and press *Assign*
  (`PUT /api/workflows/{id}/assign`).
- **Record a skill** or **design a workflow** for them. The result is saved to that person (`meta.person`).

A floating 📘 book marks everyone who owns a recorded skill. While a workflow runs, its owner types faster.

## Knowledge graph

`GET /api/graph` returns the org as nodes and edges:

| node | edge |
|---|---|
| `org`, `team:<team>`, `role:<team>/<role>` | `has_team`, `has_role` |
| `person:<team>/<role>#<seat>` | `seat` (role → person) |
| `wf:<id>` (`type` is `skill` or `workflow`) | `owns` (person → skill/workflow) |
| `tool:<tool>` | `uses_tool` |
|  | `reuses` (workflow → the skill it was built from) |

In the UI, open it with **🕸 Org knowledge graph** under the department list. Click a node to trace its owner
and everything that reuses it.

## New workflows reuse existing skills

When you design a workflow (`POST /api/workflows/generate`), OrgTwin first ranks the recorded skills against
the goal with `graph.relevant_skills`:

- **Text match:** the share of the skill's name/goal words that appear in the goal, plus overlap with its step text.
- **Graph distance:** same person 1.0 · same role 0.9 · same team 0.8 · another team 0.65.

Skills that score at least 0.34 are offered to the planner:

- **With a model:** the prompt lists them and the model can emit `{"tool": "skill", "skill_id": N}`.
  That step is then expanded into the skill's steps.
- **Demo mode (no model):** the best skill's steps come first. Only the planned steps it doesn't already
  cover are added after it.

Reused steps carry `from_skill: {id, name}`. The workflow records `meta.uses_skills`, which becomes a `reuses`
edge in the graph. `GET /api/graph/suggest?team=&role=&goal=` returns the same ranking, so the designer can show
"♻ Will reuse 📘 …" while you type.
