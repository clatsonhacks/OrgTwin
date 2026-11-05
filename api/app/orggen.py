"""Turn a company description / website / CSV into an org model.

Org shape:
{ "company": str, "summary": str,
  "ceo": {"title": str},
  "teams": [ {"key", "name", "headcount", "automatable_hours", "roles": [
      {"key", "name", "headcount", "tasks": [str], "automatable_hours": float,
       "suggested_workflows": [str]} ] } ] }
"""
import csv
import io
import re

import httpx

from . import llm

HERO_TEAMS = ("sales", "finance", "support")

# Deterministic catalog used by the mock generator and to fill gaps in model output.
CATALOG: dict[str, dict] = {
    "sales": {"name": "Sales", "roles": [
        ("SDR", ["Research inbound leads", "Qualify leads", "Send first-touch outreach", "Update CRM"], 9,
         ["Lead-to-cash", "Lead qualification", "Follow-up sequences"]),
        ("Account Executive", ["Run demos", "Write proposals", "Negotiate", "Close deals"], 5,
         ["Proposal drafting", "Deal desk handoff"]),
        ("Sales Ops", ["Maintain CRM hygiene", "Pipeline reporting", "Territory planning"], 7,
         ["Weekly pipeline report", "CRM dedupe"]),
    ]},
    "finance": {"name": "Finance", "roles": [
        ("AR Specialist", ["Create invoices", "Chase overdue payments", "Apply payments"], 8,
         ["Invoice on deal won", "Dunning follow-ups"]),
        ("Accountant", ["Reconcile transactions", "Month-end close", "Flag anomalies"], 6,
         ["Bank reconciliation", "Anomaly alerts"]),
    ]},
    "support": {"name": "Customer Success", "roles": [
        ("Onboarding Specialist", ["Kick-off new customers", "Send onboarding material", "Track activation"], 6,
         ["Welcome & onboarding", "Activation nudges"]),
        ("Support Agent", ["Triage tickets", "Resolve issues", "Escalate bugs"], 10,
         ["Ticket triage", "Refund approval"]),
    ]},
    "tech": {"name": "Engineering", "roles": [
        ("Software Engineer", ["Build features", "Fix bugs", "Code review"], 4, ["Bug triage from support"]),
        ("DevOps", ["Manage infrastructure", "On-call", "Deployments"], 5, ["Incident summary"]),
    ]},
    "marketing": {"name": "Marketing", "roles": [
        ("Content Marketer", ["Write blog posts", "Social media", "Newsletters"], 7, ["Content repurposing"]),
        ("Growth Marketer", ["Run campaigns", "Analyse funnels", "Lead scoring"], 6, ["Campaign report"]),
    ]},
    "hr": {"name": "People & HR", "roles": [
        ("Recruiter", ["Screen candidates", "Schedule interviews", "Send offers"], 8, ["Hire-to-onboard"]),
        ("People Ops", ["Onboard employees", "Payroll changes", "Policy questions"], 6, ["Employee onboarding"]),
    ]},
    "procurement": {"name": "Procurement", "roles": [
        ("Buyer", ["Compare vendors", "Request quotes", "Track orders"], 7, ["Quote comparison"]),
    ]},
}

KEYWORDS = {
    "sales": ["sales", "sell", "revenue", "lead", "crm", "deal"],
    "finance": ["finance", "invoice", "billing", "accounting", "payment"],
    "support": ["support", "customer success", "onboarding", "ticket", "help desk"],
    "tech": ["tech", "engineering", "developer", "software", "product"],
    "marketing": ["marketing", "brand", "content", "growth", "seo"],
    "hr": ["hr", "people", "recruit", "hiring", "talent"],
    "procurement": ["procurement", "purchasing", "vendor", "supply"],
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "item"


def _team_key_for(name: str) -> str:
    n = name.lower()
    for key, words in KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", n) for w in words):
            return key
    return slug(name)


def _catalog_team(key: str) -> dict:
    c = CATALOG[key]
    roles = [{"key": slug(r[0]), "name": r[0], "headcount": 1 + (len(r[1]) % 3), "tasks": r[1],
              "automatable_hours": float(r[2]), "suggested_workflows": r[3]} for r in c["roles"]]
    return {"key": key, "name": c["name"], "roles": roles}


def parse_csv(text: str) -> list[dict]:
    """CSV columns: team, role[, headcount][, tasks (semicolon separated)][, hours_per_week]."""
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    teams: dict[str, dict] = {}
    for row in rows:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        team, role = row.get("team") or row.get("department"), row.get("role") or row.get("title")
        if not team or not role:
            continue
        key = _team_key_for(team)
        t = teams.setdefault(key, {"key": key, "name": team, "roles": []})
        tasks = [x.strip() for x in (row.get("tasks") or "").split(";") if x.strip()]
        try:
            head = int(row.get("headcount") or 1)
        except ValueError:
            head = 1
        try:
            hours = float(row.get("hours_per_week") or row.get("automatable_hours") or 0)
        except ValueError:
            hours = 0.0
        existing = next((r for r in t["roles"] if r["name"].lower() == role.lower()), None)
        if existing:
            existing["headcount"] += head
            continue
        t["roles"].append({"key": slug(role), "name": role, "headcount": head, "tasks": tasks,
                           "automatable_hours": hours, "suggested_workflows": []})
    return list(teams.values())


def mock_org(description: str, csv_teams: list[dict]) -> dict:
    name = _guess_company(description)
    if csv_teams:
        teams = csv_teams
    else:
        d = description.lower()
        keys = [k for k, words in KEYWORDS.items()
                if any(re.search(rf"\b{re.escape(w)}\b", d) for w in words)]
        teams = [_catalog_team(k) for k in (keys or ["sales", "finance", "support", "tech", "marketing"])]
    return {"company": name, "summary": description.strip()[:400], "ceo": {"title": "CEO"}, "teams": teams}


def _guess_company(description: str) -> str:
    m = re.search(r"\b(?i:we are|i run|company(?: is)?(?: called)?|at)\s+([A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*){0,3})",
                  description)
    if m:
        return m.group(1).strip().rstrip(".,")
    m = re.match(r"\s*([A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*){0,3})\s*[:,(-]", description)
    if m:
        return m.group(1).strip().rstrip(".,")
    return "Your Company"


async def fetch_site_text(url: str) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "OrgTwin/0.1"})
            html = r.text
    except Exception:
        return ""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text)[:6000]


SYSTEM = """You design the organization chart of a company for an AI-operations platform.
Return ONLY JSON with this shape:
{"company": str, "summary": str, "ceo": {"title": str},
 "teams": [{"name": str, "roles": [{"name": str, "headcount": int, "tasks": [str],
   "automatable_hours": number, "suggested_workflows": [str]}]}]}
Rules: 4-7 teams, 1-4 roles per team, 3-5 concrete recurring tasks per role.
automatable_hours = realistic hours per week per person that AI agents could take over (0-20).
Always include Sales, Finance and Customer Support/Success teams. Keep names short."""


async def generate(description: str, url: str = "", csv_text: str = "") -> tuple[dict, str]:
    csv_teams = parse_csv(csv_text) if csv_text else []
    site = await fetch_site_text(url)
    user = f"Company description:\n{description}\n"
    if site:
        user += f"\nWebsite text (truncated):\n{site}\n"
    if csv_teams:
        user += ("\nThe company provided its real org structure; keep these teams and roles exactly, "
                 f"only fill in tasks/hours/workflows:\n{csv_teams}\n")
    res = await llm.complete(SYSTEM, user, fallback=lambda: "{}")
    if res.mocked:
        org = mock_org(description or site, csv_teams)
    else:
        try:
            org = llm.extract_json(res.text)
        except Exception:
            org = mock_org(description or site, csv_teams)
            res.mocked = True
    return normalize(org), ("mock" if res.mocked else "vultr")


def normalize(org: dict) -> dict:
    teams_out, seen = [], set()
    for t in org.get("teams") or []:
        key = t.get("key") or _team_key_for(t.get("name", "team"))
        if key in seen:
            key = f"{key}-{len(seen)}"
        seen.add(key)
        roles, rseen = [], set()
        for r in t.get("roles") or []:
            rk = r.get("key") or slug(r.get("name", "role"))
            while rk in rseen:
                rk += "-2"
            rseen.add(rk)
            roles.append({
                "key": rk, "name": str(r.get("name", "Role"))[:60],
                "headcount": max(1, int(_num(r.get("headcount"), 1))),
                "tasks": [str(x)[:120] for x in (r.get("tasks") or [])][:6],
                "automatable_hours": round(min(40.0, max(0.0, _num(r.get("automatable_hours"), 0))), 1),
                "suggested_workflows": [str(x)[:80] for x in (r.get("suggested_workflows") or [])][:4],
            })
        if not roles and key in CATALOG:
            roles = _catalog_team(key)["roles"]
        teams_out.append({"key": key, "name": str(t.get("name") or CATALOG.get(key, {}).get("name", key))[:40],
                          "roles": roles})
    # The hero lead-to-cash workflow needs these three teams.
    for k in HERO_TEAMS:
        if k not in {t["key"] for t in teams_out}:
            team = _catalog_team(k)
            team["added_for"] = "lead-to-cash"
            teams_out.append(team)
    for t in teams_out:
        t["headcount"] = sum(r["headcount"] for r in t["roles"])
        t["automatable_hours"] = round(sum(r["automatable_hours"] * r["headcount"] for r in t["roles"]), 1)
    return {"company": str(org.get("company") or "Your Company")[:80],
            "summary": str(org.get("summary") or "")[:600],
            "ceo": {"title": str((org.get("ceo") or {}).get("title") or "CEO")[:40]},
            "teams": teams_out}


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)
