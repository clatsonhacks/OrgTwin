"""Executable workflow format shared by text-designed drafts and recorded skills.

A step:
  {"title": str, "tool": str, "action": str, "params": {...}, "per_row": bool,
   "needs_approval": bool, "description": str}

Executable step library (run by engine.run_workflow):
  sheets.read_rows   params {"where": {"status": "New"}, "limit": 10}   -> rows for later per-row steps
  sheets.update_row  params {"column": "status", "value": "Contacted"}  (per row)
  llm.write          params {"instruction": "...", "save_as": "summary"}
  email.send         params {"to": "{{row.email}}", "subject": "...", "instruction": "..."}  (body written by AI)
  approval.request   params {"title": "..."}
  slack.post         params {"text": "..."}
Templates: {{row.<column>}}, {{count}}, {{<save_as name>}}.
Other tools (sandbox, stripe, a2a, mcp, http) can be designed but are not run by the generic engine yet.
"""
import json
import re

from . import llm
from .connectors import registry

CORE_EXEC: dict[str, list[str]] = {
    "sheets": ["read_rows", "update_row"],
    "llm": ["write"],
    "email": ["send"],
    "approval": ["request"],
    "slack": ["post"],
    "a2a": ["handoff"],  # pass the work (and everything gathered so far) to another department's agent
}
# Connector tools (Discord, Teams, Telegram, Notion, HubSpot, Stripe, webhook, web) come from the registry.
EXEC: dict[str, list[str]] = {**CORE_EXEC, **{k: list(c.actions) for k, c in registry.CONNECTORS.items()}}
TOOLS = list(EXEC) + ["sandbox", "mcp"]
DEFAULT_ACTION = {"sheets": "read_rows", "llm": "write", "email": "send", "approval": "request", "slack": "post",
                  "a2a": "handoff", **{k: next(iter(c.actions)) for k, c in registry.CONNECTORS.items()}}
TOOL_ALIASES = {
    "handoff": "a2a", "hand_off": "a2a", "agent": "a2a", "delegate": "a2a",
    "search": "web", "web_search": "web", "websearch": "web", "google": "web", "browser": "web", "internet": "web",
    "excel": "sheets", "spreadsheet": "sheets", "google_sheets": "sheets", "googlesheets": "sheets",
    "sheet": "sheets", "csv": "sheets", "crm": "sheets",
    "gmail": "email", "mail": "email", "smtp": "email", "outlook": "email",
    "ai": "llm", "gpt": "llm", "model": "llm", "write": "llm",
    "human": "approval", "approve": "approval", "review": "approval",
    "msteams": "teams", "microsoft_teams": "teams", "ms_teams": "teams",
    "http": "webhook", "zapier": "webhook", "make": "webhook", "n8n": "webhook", "api": "webhook",
    "invoice": "stripe", "billing": "stripe", "stripe_invoice": "stripe",
    "hubspot_crm": "hubspot", "notion_page": "notion", "tg": "telegram",
}
# Minutes a person would spend on each step (per row for per-row steps) — used for "hours saved".
MANUAL_MIN = {"read_rows": 2, "update_row": 1, "write": 5, "send": 3, "request": 0, "post": 2, "handoff": 10}


def _manual_min(tool: str, action: str) -> float:
    a = registry.spec(tool, action)
    return a.manual_min if a else MANUAL_MIN.get(action, 2)

_SCHEMA = """Return ONLY a JSON object: {"name": "<short name for this workflow>", "steps": [<2-10 steps>]}.
Each step:
{"title": str, "tool": "sheets"|"llm"|"email"|"approval"|"slack"|CONNECTOR_TOOLS, "action": str, "params": object,
 "per_row": bool, "needs_approval": bool, "description": strTEAM_FIELD}
Allowed tool/action/params:
- sheets/read_rows: {"where": {"<column>": "<value>"}, "limit": int}  (read spreadsheet rows; later steps with per_row=true run once per row)
- sheets/update_row: {"column": str, "value": str}  (per_row=true)
- llm/write: {"instruction": str, "save_as": str}  (the agent thinks/writes; the text is saved as {{<save_as>}})
- email/send: {"to": "{{row.email}}", "subject": str, "instruction": str}  (the AI writes the body from the instruction)
- approval/request: {"title": str}
- slack/post: {"text": str}
HANDOFF_LINECONNECTOR_LINES
Templates: {{row.<column>}} inside per-row steps, {{count}} = rows read, {{<save_as>}} = text saved by an earlier
llm/write or web step. Columns available in the sheet: COLUMNS.
Rules:
- Only read the spreadsheet when the task is about rows in it (leads, invoices, customers...). Never invent sheet steps.
- If the person looks something up on the internet (Google, a website), use web/search with the query they used,
  or web/read_page for a specific URL, then an llm/write step that uses {{search}} / {{page}}.
- Do not add steps the task does not need. Use the exact values (statuses, columns, recipients) from the task.
- Set needs_approval=true on steps that send anything outside the company."""


def schema_prompt(columns: list[str], teams: list[dict] | None = None) -> str:
    """Step-library instructions for the model. With `teams`, steps carry a team and may hand off between teams."""
    s = (_SCHEMA.replace("CONNECTOR_TOOLS", "|".join(f'"{k}"' for k in registry.CONNECTORS) + ('|"a2a"' if teams else ""))
         .replace("CONNECTOR_LINES", registry.schema_lines())
         .replace("COLUMNS", ", ".join(columns) or "(none)"))
    if not teams:
        return s.replace("TEAM_FIELD", "").replace("HANDOFF_LINE", "")
    keys = "|".join(f'"{t["key"]}"' for t in teams)
    return (s.replace("TEAM_FIELD", f', "team": {keys}  (the department whose agent does this step)')
             .replace("HANDOFF_LINE", '- a2a/handoff: {"to": "<team key>", "message": str}  (pass the work and everything '
                                      "gathered so far to another department's agent; put one wherever the work moves "
                                      "to a different team; its own \"team\" is the sender)\n"))


SCHEMA_PROMPT = schema_prompt(["COLUMNS"])  # kept for callers that substitute COLUMNS themselves


def _bool(v) -> bool:
    return v is True or str(v).lower() in ("true", "1", "yes")


def normalize_step(s: dict) -> dict:
    tool = str(s.get("tool", "llm")).lower().strip()
    tool = TOOL_ALIASES.get(tool, tool)
    if tool not in TOOLS:
        tool = "llm"
    action = str(s.get("action") or DEFAULT_ACTION.get(tool, "")).lower().strip()
    if tool in EXEC and action not in EXEC[tool]:
        action = DEFAULT_ACTION[tool]
    params = s.get("params") if isinstance(s.get("params"), dict) else {}
    if tool == "sheets" and action == "read_rows":
        where = params.get("where") if isinstance(params.get("where"), dict) else {}
        params = {"where": {str(k): str(v) for k, v in where.items()}, "limit": int(_num(params.get("limit"), 10))}
    elif tool == "sheets" and action == "update_row":
        params = {"column": str(params.get("column") or "status"), "value": str(params.get("value") or "Done")}
    elif tool == "email":
        params = {"to": str(params.get("to") or "{{row.email}}"), "subject": str(params.get("subject") or "Quick follow-up"),
                  "instruction": str(params.get("instruction") or params.get("body") or s.get("description") or
                                     "Write a short, friendly follow-up email.")}
    elif tool == "llm":
        params = {"instruction": str(params.get("instruction") or s.get("description") or s.get("title") or ""),
                  "save_as": re.sub(r"\W+", "_", str(params.get("save_as") or "note")).strip("_") or "note"}
    elif tool == "approval":
        params = {"title": str(params.get("title") or s.get("title") or "Please approve")}
    elif tool == "slack":
        params = {"text": str(params.get("text") or s.get("description") or s.get("title") or "")}
    elif tool == "a2a":
        params = {"to": str(params.get("to") or s.get("to") or "").strip().lower(),
                  "message": str(params.get("message") or params.get("text") or s.get("description") or s.get("title") or "")}
    elif tool == "web":
        if action == "search" and not params.get("query"):
            params = {**params, "query": params.get("q") or params.get("text") or s.get("description") or s.get("title") or ""}
        params = registry.normalize_params(tool, action, params)
    elif tool in registry.CONNECTORS:
        if "text" in params or not params:  # models often put the message in description/title
            params = {"text": params.get("text") or s.get("description") or s.get("title") or "", **params}
        params = registry.normalize_params(tool, action, params)
    per_row = (_bool(s.get("per_row")) and not (tool == "sheets" and action == "read_rows")
               and tool != "a2a")
    spec = registry.spec(tool, action)
    approval = _bool(s.get("needs_approval")) if "needs_approval" in s else bool(spec and spec.external)
    out = {"title": str(s.get("title") or action or tool)[:100], "tool": tool, "action": action,
           "params": params, "per_row": per_row, "needs_approval": approval,
           "description": str(s.get("description") or "")[:300]}
    if tool == "a2a":
        out["needs_approval"] = False
    if isinstance(s.get("team"), str) and s["team"].strip():
        out["team"] = s["team"].strip().lower()
    fs = s.get("from_skill")
    if isinstance(fs, dict) and isinstance(fs.get("id"), int):
        out["from_skill"] = {"id": fs["id"], "name": str(fs.get("name") or "")[:120]}
    return out


def normalize(steps) -> list[dict]:
    if isinstance(steps, dict):
        steps = steps.get("steps") or steps.get("workflow") or []
    return [normalize_step(s) for s in (steps or []) if isinstance(s, dict)][:16]


def is_executable(steps: list[dict]) -> bool:
    if not steps:
        return False
    has_rows = False
    for s in steps:
        if s.get("tool") not in EXEC or s.get("action") not in EXEC[s["tool"]]:
            return False
        if s["tool"] == "sheets" and s["action"] == "read_rows":
            has_rows = True
        if s.get("per_row") and not has_rows:
            return False
    return True


def _num(v, d: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ---------------- deterministic planner (demo mode / fallback) ----------------

_STATUS_WORDS = ["new", "pending", "overdue", "open", "unpaid", "won", "contacted", "late", "inactive"]


def plan_from_text(text: str, role_tasks: list[str] | None = None) -> list[dict]:
    """Rule-based plan from narration or a goal. Used when no model is configured."""
    t = (text or "").lower()
    steps: list[dict] = []
    uses_rows = any(w in t for w in ("sheet", "excel", "spreadsheet", "rows", "row ", "column", "csv", "crm", "list", "leads", "invoices", "customers"))
    if uses_rows:
        where = {}
        m = re.search(r"(?:status|marked|where|that are|which are|are|with)\s+(?:is\s+|=\s*|as\s+)?[\"']?(" + "|".join(_STATUS_WORDS) + r")\b", t)
        m2 = re.search(r"\b(" + "|".join(_STATUS_WORDS) + r")\s+(?:leads|invoices|rows|customers|orders|tickets|deals|accounts)\b", t)
        if m:
            where = {"status": m.group(1).capitalize()}
        elif m2:
            where = {"status": m2.group(1).capitalize()}
        steps.append({"title": "Read rows from the spreadsheet" + (f" where status = {where['status']}" if where else ""),
                      "tool": "sheets", "action": "read_rows", "params": {"where": where, "limit": 10}})
    if re.search(r"\b(search|google(?!\s*(?:sheets?|docs|drive|calendar|forms|slides|workspace|meet))|look(?:ing)? up|"
                 r"research|browse|web|website|online|internet)\b", t):
        m = re.search(r"(?:search(?:es|ed|ing)?|google[sd]?|look(?:s|ed|ing)? up|research(?:es|ed|ing)?)\s+(?:for\s+|about\s+|on\s+)?"
                      r"[\"“']?([^\"”'.,;!?\n]{3,80})", text or "", flags=re.I)
        query = m.group(1).strip() if m and m.group(1).strip().lower() not in ("the web", "it", "online") else ""
        if uses_rows:
            query = query or "{{row.company}}"
        steps.append({"title": f"Search the web{f' for {query}' if query else ''}", "tool": "web", "action": "search",
                      "per_row": uses_rows, "params": {"query": query or (role_tasks or ["latest industry news"])[0],
                                                       "max_results": "5", "save_as": "search"}})
        steps.append({"title": "Summarise what the search found", "tool": "llm", "action": "write", "per_row": uses_rows,
                      "params": {"instruction": "Summarise the useful findings in {{search}} in 3-5 bullet points.",
                                 "save_as": "summary"}})
    if any(w in t for w in ("email", "e-mail", "mail", "follow up", "follow-up", "outreach", "reply", "send",
                            "chase", "remind")) or re.search(r"\bcontact\b", t):
        topic = "a short, friendly follow-up"
        if "invoice" in t or "payment" in t or "overdue" in t:
            topic = "a polite payment reminder"
        elif "welcome" in t or "onboard" in t:
            topic = "a warm welcome / onboarding note"
        steps.append({"title": "Write and send the email" + (" to each row" if uses_rows else ""),
                      "tool": "email", "action": "send", "per_row": uses_rows, "needs_approval": True,
                      "params": {"to": "{{row.email}}" if uses_rows else "", "subject":
                                 "Following up — {{row.company}}" if uses_rows else "Quick follow-up",
                                 "instruction": f"Write {topic} to {{{{row.name}}}} at {{{{row.company}}}}. "
                                                "Use their notes for context. Max 90 words. Sign as the team."}})
    if uses_rows and any(w in t for w in ("mark", "update", "set", "change", "status", "contacted", "done")):
        m = re.search(r"(?:mark(?:ed)?|set|update|change)\b[^.]*?\b(?:as|to)\s+[\"']?([a-z]+(?: up| done| sent)?)\b", t)
        if not m:
            m = re.search(r"\bmark(?:ed|s)?\s+(?:it|them|each(?: one| row)?|the rows?|rows?|the lead|leads?)?\s*(?:as\s+)?([a-z]{3,20}(?: up)?)\b", t)
        value = (m.group(1).strip().title() if m and m.group(1) not in ("the", "each", "them", "it") else "Contacted")
        steps.append({"title": f"Mark the row as {value}", "tool": "sheets", "action": "update_row", "per_row": True,
                      "params": {"column": "status", "value": value}})
    if any(w in t for w in ("summary", "summarize", "summarise", "report")):
        steps.append({"title": "Write a short summary", "tool": "llm", "action": "write",
                      "params": {"instruction": "Summarise what was done in 3 bullet points.", "save_as": "summary"}})
    if uses_rows and any(w in t for w in ("hubspot", "crm contact", "sync contacts", "to the crm")):
        steps.append({"title": "Create / update the contact in HubSpot", "tool": "hubspot", "action": "upsert_contact",
                      "per_row": True, "params": {}})
    if uses_rows and ("invoice" in t and any(w in t for w in ("send invoice", "create invoice", "bill", "stripe", "invoice each", "invoice them"))):
        steps.append({"title": "Create and send the Stripe invoice", "tool": "stripe", "action": "invoice",
                      "per_row": True, "needs_approval": True, "params": {}})
    if "notion" in t:
        steps.append({"title": "Save it as a Notion page", "tool": "notion", "action": "create_page",
                      "params": {"title": "Report: {{count}} rows", "content": "{{summary}}" if "summary" in t else
                                 "Processed {{count}} rows."}})
    done = "Done: processed {{count}} rows." + (" {{summary}}" if "summary" in t else "")
    chats = [("discord", "post", "Discord"), ("teams", "post", "Microsoft Teams"), ("telegram", "send", "Telegram")]
    said = False
    for key, action, label in chats:
        if key in t or (key == "teams" and "microsoft teams" in t):
            steps.append({"title": f"Tell the team on {label}", "tool": key, "action": action, "needs_approval": False,
                          "params": {"text": done}})
            said = True
    if any(w in t for w in ("webhook", "zapier", "n8n", "make.com")):
        m = re.search(r"https://[^\s\"')]+", text or "")
        steps.append({"title": "Call the webhook", "tool": "webhook", "action": "request", "needs_approval": False,
                      "params": {"url": m.group(0) if m else "https://", "body": '{"count": "{{count}}", "summary": "{{summary}}"}'}})
    if "slack" in t or (not said and ("notify" in t or "tell the team" in t)):
        steps.append({"title": "Tell the team on Slack", "tool": "slack", "action": "post",
                      "params": {"text": done}})
    if not steps:
        for task in (role_tasks or ["Do the task"])[:3]:
            steps.append({"title": task, "tool": "llm", "action": "write",
                          "params": {"instruction": f"Do this and report the result: {task}", "save_as": "note"}})
        steps.append({"title": "Ask a human to confirm", "tool": "approval", "action": "request",
                      "params": {"title": "Confirm the result"}})
    return normalize(steps)


# ---------------- reusing existing skills ----------------

def skill_steps(skill: dict) -> list[dict]:
    """A recorded skill's steps, copied and tagged with where they came from."""
    tag = {"id": skill["id"], "name": skill["name"]}
    return [{**st, "team": st.get("team") or skill.get("team_key"), "from_skill": tag}
            for st in normalize(skill.get("steps") or [])]


def _sig(st: dict) -> tuple:
    return (st["tool"], st["action"], st["per_row"])


def compose(planned: list[dict], skills: list[dict]) -> list[dict]:
    """Start from the reused skills' steps, then add only the planned steps they don't already cover."""
    out: list[dict] = []
    for sk in skills:
        out += [st for st in skill_steps(sk) if _sig(st) not in {_sig(x) for x in out}]
    have = {_sig(x) for x in out}
    for st in planned:
        if _sig(st) in have:
            continue
        if st["tool"] == "sheets" and st["action"] == "read_rows" and any(x["action"] == "read_rows" for x in out):
            continue
        out.append(st)
        have.add(_sig(st))
    # rows must be read before any per-row step
    reads = [x for x in out if x["tool"] == "sheets" and x["action"] == "read_rows"][:1]
    return (reads + [x for x in out if x not in reads])[:12]


def expand_skill_refs(raw, skills: dict[int, dict]) -> tuple[list[dict], list[int]]:
    """Replace model-emitted {"tool": "skill", "skill_id": N} steps with that skill's steps."""
    if isinstance(raw, dict):
        raw = raw.get("steps") or raw.get("workflow") or []
    out, used = [], []
    for st in raw or []:
        if not isinstance(st, dict):
            continue
        sid = st.get("skill_id") if str(st.get("tool", "")).lower() == "skill" else None
        try:
            sid = int(sid) if sid is not None else None
        except (TypeError, ValueError):
            sid = None
        if sid is not None and sid in skills:
            out += skill_steps(skills[sid])
            used.append(sid)
        elif str(st.get("tool", "")).lower() != "skill":
            out.append(st)
    return normalize(out), used


async def generate(team: dict, role: dict, goal: str, columns: list[str],
                   skills: list[dict] | None = None) -> tuple[list[dict], bool, list[int]]:
    """Design a workflow for a goal. `skills` are relevant existing skills (best first) that may be reused.
    Returns (steps, mocked, ids of reused skills)."""
    skills = skills or []
    by_id = {s["id"]: s for s in skills}

    def offline() -> tuple[list[dict], list[int]]:
        planned = plan_from_text(goal, role.get("tasks"))
        if not skills:
            return planned, []
        best = skills[:1]  # relevant_skills already ranked and thresholded them
        return compose(planned, best), [b["id"] for b in best]

    reuse = ""
    if skills:
        lines = [f"- skill_id {s['id']}: \"{s['name']}\" — steps: " +
                 "; ".join(f"{x.get('tool')}/{x.get('action')} {x.get('title')}" for x in s.get("steps") or [])
                 for s in skills]
        reuse = ("\nThe company already has these recorded skills. When one covers part of the goal, REUSE it by "
                 "emitting a single step {\"tool\": \"skill\", \"skill_id\": <id>} in its place instead of "
                 "re-describing its steps:\n" + "\n".join(lines))
    data, res, problem = await llm.complete_json(
        "You design automations for one role in a company, run by AI agents.\n" + schema_prompt(columns) + reuse,
        f"Team: {team['name']}\nRole: {role['name']}\nRole tasks: {role.get('tasks')}\nGoal: {goal}",
        fallback=lambda: None)
    if problem:
        steps, used = offline()
        return steps, True, used
    steps, used = expand_skill_refs(data, by_id)
    if not steps:
        steps, used = offline()
        return steps, True, used
    for st in steps:  # one team: every step is this team's agent
        st.pop("team", None)
    return steps, res.mocked, used


# ---------------- cross-team workflows ----------------

def _team_resolver(teams: list[dict]):
    by_key = {t["key"]: t["key"] for t in teams}
    by_name = {t["name"].lower(): t["key"] for t in teams}

    def resolve(v) -> str | None:
        v = str(v or "").strip().lower()
        if not v:
            return None
        if v in by_key:
            return by_key[v]
        if v in by_name:
            return by_name[v]
        return next((k for n, k in by_name.items() if v in n or n in v), None)
    return resolve


def link_teams(steps: list[dict], teams: list[dict], default: str | None = None) -> list[dict]:
    """Give every step a valid team and make sure a handoff step sits wherever the work changes team.
    Handoffs that point nowhere or to the same team are dropped."""
    resolve = _team_resolver(teams)
    names = {t["key"]: t["name"] for t in teams}
    cur = resolve(default) or (teams[0]["key"] if teams else "")
    first = next((resolve(s.get("team")) for s in steps if s.get("tool") != "a2a" and resolve(s.get("team"))), None)
    cur = first or cur
    out: list[dict] = []
    for i, s in enumerate(steps):
        s = dict(s)
        if s["tool"] == "a2a":
            # a handoff goes to whoever does the next step (if there is one), whatever it said before
            nxt = next((resolve(x.get("team")) for x in steps[i + 1:] if x.get("tool") != "a2a"), None)
            to = nxt or resolve(s["params"].get("to"))
            if not to or to == cur:
                continue
            s["team"], s["params"] = cur, {**s["params"], "to": to}
            if not s["params"].get("message"):
                s["params"]["message"] = s["title"]
            out.append(s)
            cur = to
            continue
        t = resolve(s.get("team")) or cur
        if t != cur:
            out.append(normalize_step({"title": f"Hand off to {names.get(t, t)}", "tool": "a2a", "action": "handoff",
                                       "team": cur, "params": {"to": t, "message": s["title"]}}))
            cur = t
        s["team"] = t
        out.append(s)
    return out


# keyword → words that appear in a department's key or name
_TEAM_HINTS = [
    (("invoice", "payment", "refund", "billing", "stripe", "pay ", "budget", "expense", "finance"), ("financ", "account", "billing")),
    (("lead", "deal", "outreach", "prospect", "hubspot", "quote", "sales"), ("sales", "business dev", "revenue")),
    (("onboard", "ticket", "support", "welcome", "customer success", "churn", "complaint"), ("support", "success", "customer", "service")),
    (("bug", "deploy", "incident", "code", "release", "engineer", "outage"), ("engineer", "tech", "product", "dev")),
    (("campaign", "newsletter", "social", "content", "seo", "marketing", "brand"), ("market", "growth", "brand")),
    (("hire", "candidate", "interview", "employee", "payroll", "recruit"), ("people", "hr", "talent", "recruit")),
    (("contract", "legal", "compliance", "policy"), ("legal", "compliance")),
    (("shipment", "inventory", "warehouse", "order", "supplier", "logistics"), ("operation", "ops", "logistic", "supply")),
]


def _guess_team(text: str, teams: list[dict]) -> str | None:
    t = (text or "").lower()
    for t_ in teams:  # a department named in the text wins
        if t_["name"].lower() in t:
            return t_["key"]
    for words, team_words in _TEAM_HINTS:
        if any(w in t for w in words):
            hit = next((x["key"] for x in teams if any(tw in (x["key"] + " " + x["name"]).lower() for tw in team_words)), None)
            if hit:
                return hit
    return None


def plan_flow_offline(goal: str, teams: list[dict]) -> list[dict]:
    """Rule-based cross-team plan: split the goal into clauses, plan each, and give each a department."""
    parts = [p.strip() for p in re.split(r"\bthen\b|;|\.\s|,\s*(?:and\s+)?(?=then|after|finally|next)", goal or "", flags=re.I) if p.strip()]
    steps: list[dict] = []
    team = _guess_team(goal, teams) or (teams[0]["key"] if teams else "")
    for p in parts or [goal]:
        team = _guess_team(p, teams) or team
        planned = plan_from_text(p)
        if not any(x["tool"] != "llm" for x in planned) and len(planned) > 1:  # nothing concrete: one AI step
            planned = [normalize_step({"title": p[:90], "tool": "llm", "action": "write",
                                       "params": {"instruction": p, "save_as": "note"}})]
        for x in planned:
            if x["tool"] == "approval" and x["params"].get("title") == "Confirm the result":
                continue
            if x["tool"] == "sheets" and x["action"] == "read_rows" and any(s["action"] == "read_rows" for s in steps):
                continue
            steps.append({**x, "team": team})
    return link_teams(normalize(steps)[:12] if steps else [], teams, team)


def _org_lines(teams: list[dict]) -> str:
    return "\n".join(f'- key "{t["key"]}": {t["name"]} — roles: ' +
                     "; ".join(f'{r["name"]} ({", ".join(r.get("tasks", [])[:3])})' for r in t.get("roles", []))
                     for t in teams)


async def design_flow(org: dict, goal: str, columns: list[str], skills: list[dict] | None = None) -> dict:
    """Design a workflow that runs across departments. Returns {name, steps, mocked, used, problem}."""
    teams, skills = org["teams"], skills or []
    by_id = {s["id"]: s for s in skills}
    reuse = ""
    if skills:
        reuse = ("\nRecorded skills you may REUSE by emitting {\"tool\": \"skill\", \"skill_id\": <id>} in their place:\n" +
                 "\n".join(f'- skill_id {s["id"]} ({s["team_key"]}): "{s["name"]}" — ' +
                           "; ".join(f"{x.get('tool')}/{x.get('action')}" for x in s.get("steps") or []) for s in skills))
    system = ("You design automations that run across a company's departments. Each department has its own AI "
              "agent; work moves between them with a2a/handoff steps. Give every step the team that does it, and "
              "only involve the departments the goal needs.\n" + schema_prompt(columns, teams) + reuse)
    user = f"Company: {org.get('company')}\nDepartments:\n{_org_lines(teams)}\n\nGoal: {goal}"
    data, res, problem = await llm.complete_json(system, user, fallback=lambda: None)
    name = goal.strip()[:80]
    steps: list[dict] = []
    used: list[int] = []
    if not problem:
        if isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"].strip():
            name = data["name"].strip()[:120]
        steps, used = expand_skill_refs(data, by_id)
        if not steps:
            problem = "the AI returned no usable steps"
    if problem:
        steps, used = plan_flow_offline(goal, teams), []
        return {"name": name, "steps": steps, "mocked": True, "used": used, "problem": problem}
    return {"name": name, "steps": link_teams(steps, teams), "mocked": res.mocked, "used": used, "problem": None,
            "model": res.model}


def _offline_ideas(teams: list[dict]) -> list[dict]:
    ideas = []
    keys = {t["key"] for t in teams}
    guess = lambda word: _guess_team(word, teams)  # noqa: E731
    fin, sales, sup = guess("invoice"), guess("lead"), guess("onboard")
    if sales and fin:
        ideas.append({"name": "Quote to invoice", "goal": "Sales reads the Won deals in the sheet, then Finance sends "
                      "each one a Stripe invoice and marks it Invoiced, then posts a summary to Slack."})
    if sup and fin:
        ideas.append({"name": "Refund request", "goal": "Customer Success writes up a refund request, Finance asks a "
                      "person to approve it and emails the customer, then Customer Success posts to Slack."})
    if sales:
        ideas.append({"name": "Prospect research", "goal": "Sales searches the web for each New lead's company, writes "
                      "a short brief, then emails the lead a tailored intro and marks the row Contacted."})
    ideas.append({"name": "Weekly market brief", "goal": "Search the web for news about our industry, write a 5-bullet "
                  "brief, hand it to every department lead and post it to Slack."})
    return [i for i in ideas if keys][:4]


async def suggest_flows(org: dict) -> tuple[list[dict], bool]:
    """A few cross-team workflow ideas for this company: [{name, goal}], and whether they are offline ideas."""
    teams = org["teams"]
    data, res, problem = await llm.complete_json(
        "You suggest automations that run across several departments of a company, each department run by an "
        'AI agent. Return ONLY {"ideas": [{"name": str (max 5 words), "goal": str (one or two sentences naming '
        'which department does what, in order)}]} with 4 ideas. Prefer ideas that use a spreadsheet of '
        "customers/leads, email, approvals, Slack, Stripe invoices or web search.",
        f"Company: {org.get('company')} — {org.get('summary', '')}\nDepartments:\n{_org_lines(teams)}",
        fallback=lambda: None, temperature=0.6, max_tokens=2000)
    ideas = data.get("ideas") if isinstance(data, dict) else data if isinstance(data, list) else None
    clean = [{"name": str(i.get("name", ""))[:60], "goal": str(i.get("goal", ""))[:400]}
             for i in (ideas or []) if isinstance(i, dict) and i.get("goal")]
    if problem or not clean:
        return _offline_ideas(teams), True
    return clean[:6], False


def manual_minutes(steps: list[dict], rows: int) -> float:
    total = 0.0
    for s in steps:
        m = _manual_min(s.get("tool", ""), s.get("action", ""))
        total += m * (rows if s.get("per_row") else 1)
    return total
