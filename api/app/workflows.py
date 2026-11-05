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
    out = {"title": str(s.get("title") or action or tool)[:100], "tool": tool, "action": action,
           "params": params, "per_row": per_row, "needs_approval": _bool(s.get("needs_approval")),
           "description": str(s.get("description") or "")[:300]}
    fs = s.get("from_skill")
    if isinstance(fs, dict) and isinstance(fs.get("id"), int):
        out["from_skill"] = {"id": fs["id"], "name": str(fs.get("name") or "")[:120]}
    return out


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


# ---------------- reusing existing skills ----------------

def skill_steps(skill: dict) -> list[dict]:
    """A recorded skill's steps, copied and tagged with where they came from."""
    tag = {"id": skill["id"], "name": skill["name"]}
    return [{**st, "from_skill": tag} for st in normalize(skill.get("steps") or [])]


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
    res = await llm.complete(
        "You design automations for one role in a company, run by AI agents.\n" +
        SCHEMA_PROMPT.replace("COLUMNS", ", ".join(columns)) + reuse,
        f"Team: {team['name']}\nRole: {role['name']}\nRole tasks: {role.get('tasks')}\nGoal: {goal}",
        fallback=lambda: "__offline__")
    if res.text == "__offline__":
        steps, used = offline()
        return steps, True, used
    try:
        steps, used = expand_skill_refs(llm.extract_json(res.text), by_id)
    except Exception:
        steps, used = offline()
        res.mocked = True
    if not steps:
        steps, used = offline()
    return steps, res.mocked, used


def manual_minutes(steps: list[dict], rows: int) -> float:
    total = 0.0
    for s in steps:
        m = MANUAL_MIN.get(s.get("action", ""), 2)
        total += m * (rows if s.get("per_row") else 1)
    return total
