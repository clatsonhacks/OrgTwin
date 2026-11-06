# People, departments, requests and skill tools

## People and access

Until someone becomes admin, OrgTwin is one shared workspace (optionally behind `APP_ACCESS_TOKEN`).
Click the **👑 Set up admin** chip in the top bar (or **👥 People**) and enter your name: you are the admin.
From then on every change needs a signed-in person.

| | Admin | Member |
|---|---|---|
| See every department, skill and run | ✓ | ✓ (visitors too) |
| Run any skill, send requests to any team | ✓ | ✓ |
| Create / edit skills and give them tools | every department | only the departments ticked for them |
| Approve a step | every department | their departments |
| Build the company, connect apps, add MCP servers, invite people | ✓ | – |

**Inviting someone.** People → *Invite someone*: name, optional email, and tick the departments they look after
(one or more, shown with green ticks). You get a personal link (`/?invite=ot_…`). Send it privately: it signs
them in like a password, and OrgTwin keeps only its SHA-256. *New invite link* replaces it. Ticks can be changed
later by clicking the department chips on their row.

`APP_ACCESS_TOKEN`, when set, still works as an admin key for scripts (header `X-Access-Token`) and is needed to
claim the first admin.

## Requests: "I need an analysis of …"

**📥 Ask a team** (top bar). Someone in Sales writes what they need. OrgTwin:

1. picks the skill that fits best in **any** department. It asks the AI to choose; with no AI or no clear
   answer it falls back to keyword matching. You can also pick the skill yourself.
2. runs it with the request as input: steps can use `{{request}}`, `{{requested_by}}` and `{{requester_team}}`.
3. posts to **Slack** and/or **Discord** for the people who look after that department, e.g.
   `📥 Request from Sam (Sales): “…” ✅ Your skill “Churn analysis” handled it. Result: …`. Each person
   chooses Slack, Discord or both under People → *How should OrgTwin tell me*. With their Slack member ID or
   Discord user ID they get a real @mention; the requester is cc'd the same way.
4. shows the result to the person who asked, in the request list.

If no skill fits, the most likely department is pinged to record one. Runs happen one at a time (the world shows
one run), so a request waits its turn. Approval steps in the skill go to the owning department.

## Skill tools and MCP

Every skill has **🧰 Tools**. Tick the apps (GitHub, Notion, Slack, web search, …) and MCP servers, or single MCP
tools, that it may use.

- Once a skill has a tools list, its steps can only use those tools. Rows, AI writing, approvals and handoffs
  always work. A run that needs anything else stops with "this skill has no access to: …".
- The **🧰 AI agent with tools** step (`agent/run {instruction, save_as}`) gives the AI the skill's tools.
  It calls them one at a time and sees each result until the task is done (at most 8 calls). Any call that
  sends or changes something first shows an approval card to the owning department: connector actions marked
  external, and MCP tools that are not `readOnlyHint`. Each such tool asks once per run.
- **MCP servers** are added once by an admin under 🔌 Connectors → *MCP servers*: name, Streamable-HTTP URL
  and an optional bearer token. The token is never sent back to the browser. OrgTwin lists the server's tools
  (`tools/list`) and calls them with `tools/call`. Private or LAN addresses are refused unless
  `MCP_ALLOW_PRIVATE=true` (for a server on your own machine).

## API

- `GET /api/me`, `POST /api/people/claim`, `GET/POST /api/people`, `PUT/DELETE /api/people/{id}`,
  `POST /api/people/{id}/link`, `POST /api/people/join` (header `X-User-Token` for everything else).
- `POST /api/requests {text, workflow_id?}`, `GET /api/requests`.
- `GET /api/tools`, `PUT /api/workflows/{id}/tools {tools: ["github", "mcp:linear", "mcp:linear/create_issue"]}`.
- `GET/POST /api/mcp/servers`, `POST /api/mcp/servers/{id}/refresh`, `DELETE /api/mcp/servers/{id}`.
- `GET /api/quests`: the setup checklist with its green ticks.
