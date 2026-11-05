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

TOOLS = ["sheets", "llm", "email", "approval", "slack", "sandbox", "stripe", "a2a", "mcp", "http"]
EXEC: dict[str, list[str]] = {
    "sheets": ["read_rows", "update_row"],
    "llm": ["write"],
    "email": ["send"],
    "approval": ["request"],
    "slack": ["post"],
}
DEFAULT_ACTION = {"sheets": "read_rows", "llm": "write", "email": "send", "approval": "request", "slack": "post"}
TOOL_ALIASES = {
    "excel": "sheets", "spreadsheet": "sheets", "google_sheets": "sheets", "googlesheets": "sheets",
    "sheet": "sheets", "csv": "sheets", "crm": "sheets",
    "gmail": "email", "mail": "email", "smtp": "email", "outlook": "email",
    "ai": "llm", "gpt": "llm", "model": "llm", "write": "llm",
    "human": "approval", "approve": "approval", "review": "approval",
}
# Minutes a person would spend on each step (per row for per-row steps) — used for "hours saved".
MANUAL_MIN = {"read_rows": 2, "update_row": 1, "write": 5, "send": 3, "request": 0, "post": 2}

SCHEMA_PROMPT = """Return ONLY a JSON array of 2-8 steps. Each step:
{"title": str, "tool": "sheets"|"llm"|"email"|"approval"|"slack", "action": str, "params": object,
 "per_row": bool, "needs_approval": bool, "description": str}
Allowed tool/action/params:
- sheets/read_rows: {"where": {"<column>": "<value>"}, "limit": int}  (read spreadsheet rows; later steps with per_row=true run once per row)
- sheets/update_row: {"column": str, "value": str}  (per_row=true)
- llm/write: {"instruction": str, "save_as": str}
- email/send: {"to": "{{row.email}}", "subject": str, "instruction": str}  (the AI writes the body from the instruction)
- approval/request: {"title": str}
- slack/post: {"text": str}
Use templates like {{row.name}}, {{row.company}}, {{count}}. Columns available in the sheet: COLUMNS.
Set needs_approval=true on steps that send anything outside the company."""


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
    per_row = _bool(s.get("per_row")) and not (tool == "sheets" and action == "read_rows")
    return {"title": str(s.get("title") or action or tool)[:100], "tool": tool, "action": action,
            "params": params, "per_row": per_row, "needs_approval": _bool(s.get("needs_approval")),
            "description": str(s.get("description") or "")[:300]}


def normalize(steps) -> list[dict]:
    if isinstance(steps, dict):
        steps = steps.get("steps") or steps.get("workflow") or []
    return [normalize_step(s) for s in (steps or []) if isinstance(s, dict)][:12]


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
    if "slack" in t or "notify" in t or "tell the team" in t:
        steps.append({"title": "Tell the team on Slack", "tool": "slack", "action": "post",
                      "params": {"text": "Done: processed {{count}} rows." + (" {{summary}}" if "summary" in t else "")}})
    if not steps:
        for task in (role_tasks or ["Do the task"])[:3]:
            steps.append({"title": task, "tool": "llm", "action": "write",
                          "params": {"instruction": f"Do this and report the result: {task}", "save_as": "note"}})
        steps.append({"title": "Ask a human to confirm", "tool": "approval", "action": "request",
                      "params": {"title": "Confirm the result"}})
    return normalize(steps)


async def generate(team: dict, role: dict, goal: str, columns: list[str]) -> tuple[list[dict], bool]:
    res = await llm.complete(
        "You design automations for one role in a company, run by AI agents.\n" +
        SCHEMA_PROMPT.replace("COLUMNS", ", ".join(columns)),
        f"Team: {team['name']}\nRole: {role['name']}\nRole tasks: {role.get('tasks')}\nGoal: {goal}",
        fallback=lambda: json.dumps(plan_from_text(goal, role.get("tasks"))))
    try:
        steps = normalize(llm.extract_json(res.text))
    except Exception:
        steps, res.mocked = plan_from_text(goal, role.get("tasks")), True
    return steps or plan_from_text(goal, role.get("tasks")), res.mocked


def manual_minutes(steps: list[dict], rows: int) -> float:
    total = 0.0
    for s in steps:
        m = MANUAL_MIN.get(s.get("action", ""), 2)
        total += m * (rows if s.get("per_row") else 1)
    return total
