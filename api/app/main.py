"""OrgTwin API."""
import asyncio
from contextlib import asynccontextmanager

import httpx

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from . import a2a, access, agent, engine, graph, llm, mcp, oauth, orggen, skills, userconfig, workflows
from .config import settings
from .connectors import email, registry, sheets, slack
from .db import Approval, Event, Org, Run, SessionLocal, User, Workflow, init_db
from .db import Request as WorkRequest
from .events import bus, sse_format

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    userconfig.load()
    yield


app = FastAPI(title="OrgTwin API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                   allow_headers=["*"])
_tasks: set[asyncio.Task] = set()


# Any change needs a signed-in person once an admin exists (see access.py); before that, APP_ACCESS_TOKEN.
require_token = access.actor
require_admin = access.admin


def _org_dict(org: Org | None) -> dict | None:
    if org is None:
        return None
    return {"id": org.id, "name": org.name, "source": org.source, **org.data}


def _latest_org(s) -> Org | None:
    return s.scalars(select(Org).order_by(desc(Org.id)).limit(1)).first()


def _wf_dict(w: Workflow) -> dict:
    teams = list(dict.fromkeys([s.get("team") or w.team_key for s in (w.steps or []) if s.get("tool") != "a2a"]
                               + [s["params"]["to"] for s in (w.steps or []) if s.get("tool") == "a2a"
                                  and s.get("params", {}).get("to")])) or [w.team_key]
    return {"id": w.id, "org_id": w.org_id, "team_key": w.team_key, "role_key": w.role_key, "name": w.name,
            "goal": w.goal, "kind": w.kind, "steps": w.steps, "meta": w.meta or {}, "teams": teams,
            "executable": w.kind == "hero" or workflows.is_executable(w.steps or [])}


# ---------- status ----------
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
async def status():
    modes = settings.modes()
    vision = await llm.vision_model()
    return {"modes": {**modes, "vision": vision or "none"},
            "demo_mode": any(v == "mock" for k, v in modes.items() if k != "runner"),
            "auth_required": bool(settings.app_access_token)}


@app.get("/api/auth/check", dependencies=[Depends(require_token)])
def auth_check():
    """200 when the X-Access-Token is right (or none is needed); used by the unlock popup."""
    return {"ok": True}


# ---------- people: admin, invites, departments ----------
class NotifyIn(BaseModel):
    slack: bool = True
    discord: bool = True
    slack_id: str = Field(default="", max_length=40)      # Slack member ID (U…) to @mention
    discord_id: str = Field(default="", max_length=40)    # Discord user ID to @mention


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(default="", max_length=200)
    role: str = Field(default="member", pattern="^(admin|member)$")
    departments: list[str] = Field(default_factory=list, max_length=50)
    notify: NotifyIn = Field(default_factory=NotifyIn)


class PersonUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, pattern="^(admin|member)$")
    departments: list[str] | None = Field(default=None, max_length=50)
    notify: NotifyIn | None = None


class ClaimIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(default="", max_length=200)


def _valid_departments(keys: list[str]) -> list[str]:
    with SessionLocal() as s:
        org = _latest_org(s)
    known = {t["key"] for t in (org.data["teams"] if org else [])}
    bad = [k for k in keys if k not in known]
    if bad:
        raise HTTPException(400, f"unknown department(s): {', '.join(bad)}")
    return list(dict.fromkeys(keys))


@app.get("/api/me")
def me(who: access.Actor | None = Depends(access.viewer)):
    """Who is signed in, and whether people mode is on."""
    on = access.enabled()
    return {"people_mode": on, "setup_needed": not on,
            "user": access.user_dict(who.user) if who and who.user else None,
            "admin": bool(who and who.admin), "can_edit": (["*"] if who and who.admin else who.departments() if who else [])}


@app.post("/api/people/claim")
def claim_admin(body: ClaimIn, x_access_token: str = Header(default="")):
    """The first person becomes the admin (needs APP_ACCESS_TOKEN when one is set). Returns their sign-in token."""
    if access.enabled():
        raise HTTPException(409, "there is already an admin — ask them for an invite link")
    if settings.app_access_token and x_access_token != settings.app_access_token:
        raise HTTPException(401, "enter the access code first")
    token, h = access.new_token()
    with SessionLocal() as s:
        u = User(name=body.name.strip(), email=body.email.strip(), role="admin", departments=[], token_hash=h, joined=True,
                 notify={"slack": True, "discord": True})
        s.add(u)
        s.commit()
        out = access.user_dict(u, token)
    bus.emit("people.changed", {"admin": out["name"]})
    return out


@app.get("/api/people")
def list_people(who: access.Actor | None = Depends(access.viewer)):
    if access.enabled() and who is None:
        raise HTTPException(401, "sign in to see your team")
    with SessionLocal() as s:
        return [access.user_dict(u) for u in s.scalars(select(User).order_by(User.id))]


@app.post("/api/people")
def invite_person(body: PersonIn, who: access.Actor = Depends(require_admin)):
    """Add a person and get their personal sign-in link (send it to them; it works like a password)."""
    deps = _valid_departments(body.departments)
    token, h = access.new_token()
    with SessionLocal() as s:
        u = User(name=body.name.strip(), email=body.email.strip(), role=body.role, departments=deps, token_hash=h,
                 notify=body.notify.model_dump())
        s.add(u)
        s.commit()
        out = access.user_dict(u, token)
    bus.emit("people.changed", {"invited": out["name"], "departments": deps})
    return out


@app.put("/api/people/{user_id}")
def update_person(user_id: int, body: PersonUpdateIn, who: access.Actor = Depends(require_token)):
    """Admins change anything; people can change their own name and notification settings."""
    own = who.user is not None and who.user.id == user_id
    if not who.admin and not (own and body.role is None and body.departments is None):
        raise HTTPException(403, "only an admin can change roles and departments")
    with SessionLocal() as s:
        u = s.get(User, user_id)
        if not u:
            raise HTTPException(404, "person not found")
        if body.role == "member" and u.role == "admin":
            admins = s.scalar(select(func.count()).select_from(User).where(User.role == "admin"))
            if admins <= 1:
                raise HTTPException(400, "keep at least one admin")
        if body.departments is not None:
            u.departments = _valid_departments(body.departments)
        for k in ("name", "email", "role"):
            if getattr(body, k) is not None:
                setattr(u, k, getattr(body, k).strip())
        if body.notify is not None:
            u.notify = body.notify.model_dump()
        s.commit()
        out = access.user_dict(u)
    bus.emit("people.changed", {"updated": out["name"]})
    return out


@app.post("/api/people/{user_id}/link")
def new_invite_link(user_id: int, who: access.Actor = Depends(require_admin)):
    """Make a fresh sign-in link (the old one stops working)."""
    token, h = access.new_token()
    with SessionLocal() as s:
        u = s.get(User, user_id)
        if not u:
            raise HTTPException(404, "person not found")
        u.token_hash, u.joined = h, False
        s.commit()
        return access.user_dict(u, token)


@app.delete("/api/people/{user_id}")
def remove_person(user_id: int, who: access.Actor = Depends(require_admin)):
    with SessionLocal() as s:
        u = s.get(User, user_id)
        if not u:
            raise HTTPException(404, "person not found")
        if u.role == "admin" and s.scalar(select(func.count()).select_from(User).where(User.role == "admin")) <= 1:
            raise HTTPException(400, "keep at least one admin")
        s.delete(u)
        s.commit()
    bus.emit("people.changed", {"removed": user_id})
    return {"ok": True}


class JoinIn(BaseModel):
    token: str = Field(min_length=8, max_length=100)


@app.post("/api/people/join")
def join(body: JoinIn):
    """Check an invite link's token and say who it belongs to."""
    u = access.user_by_token(body.token)
    if not u:
        raise HTTPException(401, "this invite link is not valid any more — ask your admin for a new one")
    bus.emit("people.changed", {"joined": u.name})
    return access.user_dict(u)


# ---------- org ----------
class OrgIn(BaseModel):
    description: str = Field(default="", max_length=4000)
    url: str = Field(default="", max_length=500)
    csv: str = Field(default="", max_length=200_000)


@app.post("/api/org/generate", dependencies=[Depends(require_admin)])
async def generate_org(body: OrgIn):
    if not (body.description.strip() or body.url.strip() or body.csv.strip()):
        raise HTTPException(400, "describe the company, give a URL, or upload a CSV")
    data, source = await orggen.generate(body.description, body.url, body.csv)
    with SessionLocal() as s:
        org = Org(name=data["company"], data=data, source=source)
        s.add(org)
        s.flush()
        s.add(Workflow(org_id=org.id, team_key="sales", role_key="sdr", name="Lead-to-cash", kind="hero",
                       goal="Turn a new lead into a paying, onboarded customer",
                       steps=engine.hero_workflow_steps()))
        s.commit()
        out = _org_dict(org)
    sheets.reset_mock()
    bus.emit("org.generated", {"org_id": out["id"], "company": out["company"], "source": source})
    return out


@app.get("/api/org")
def get_org():
    with SessionLocal() as s:
        return _org_dict(_latest_org(s))


@app.get("/api/agents")
def agents():
    with SessionLocal() as s:
        org = _latest_org(s)
        return [a2a.agent_card(t) for t in (org.data["teams"] if org else [])]


# ---------- workflows ----------
class WorkflowGenIn(BaseModel):
    team_key: str
    role_key: str
    goal: str = Field(min_length=3, max_length=500)
    person: str | None = Field(default=None, max_length=120)
    person_name: str | None = Field(default=None, max_length=120)


class AssignIn(BaseModel):
    team_key: str
    role_key: str
    person: str = Field(min_length=3, max_length=120)
    person_name: str = Field(default="", max_length=120)


def _check_person(role: dict, person: str | None) -> None:
    """A person id is `<role_key>#<seat>` with seat < the role's headcount."""
    if person is None:
        return
    key, _, seat = person.partition("#")
    if key != role["key"] or not seat.isdigit() or int(seat) >= max(1, int(role.get("headcount") or 1)):
        raise HTTPException(400, f"no person {person!r} in role {role['key']!r}")


class WorkflowUpdateIn(BaseModel):
    name: str | None = None
    steps: list[dict]


@app.get("/api/workflows")
def list_workflows(team: str | None = None):
    with SessionLocal() as s:
        org = _latest_org(s)
        if not org:
            return []
        q = select(Workflow).where(Workflow.org_id == org.id)
        if team:
            q = q.where(Workflow.team_key == team)
        return [_wf_dict(w) for w in s.scalars(q.order_by(Workflow.id))]


@app.post("/api/workflows/generate")
async def generate_workflow(body: WorkflowGenIn, who: access.Actor = Depends(require_token)):
    access.require_edit(who, body.team_key, "workflows")
    with SessionLocal() as s:
        org = _latest_org(s)
    if not org:
        raise HTTPException(400, "generate an org first")
    team = next((t for t in org.data["teams"] if t["key"] == body.team_key), None)
    role = next((r for r in (team or {}).get("roles", []) if r["key"] == body.role_key), None)
    if not team or not role:
        raise HTTPException(404, "team or role not found")
    _check_person(role, body.person)
    candidates = graph.relevant_skills(body.goal, team["key"], role["key"], body.person, _skills(org.id))
    full = {w["id"]: w for w in _skills(org.id)}
    steps, mocked, used = await workflows.generate(team, role, body.goal, await sheets.columns(),
                                                   [full[c["id"]] for c in candidates])
    meta = {"person": body.person, "person_name": body.person_name or ""} if body.person else {}
    if used:
        meta["uses_skills"] = used
    with SessionLocal() as s:
        w = Workflow(org_id=org.id, team_key=team["key"], role_key=role["key"], name=body.goal[:120],
                     goal=body.goal, kind="draft", steps=steps, meta=meta)
        s.add(w)
        s.commit()
        out = {**_wf_dict(w), "mocked": mocked}
    bus.emit("workflow.created", {"workflow_id": out["id"], "team": team["key"], "role": role["key"],
                                  "reused": [full[i]["name"] for i in used]})
    return out


class FlowIn(BaseModel):
    goal: str = Field(min_length=3, max_length=1500)
    name: str = Field(default="", max_length=120)


@app.post("/api/flows/design", dependencies=[Depends(require_token)])
async def design_flow(body: FlowIn):
    """Design a workflow that runs across departments (each step done by its team's agent, with handoffs)."""
    with SessionLocal() as s:
        org = _latest_org(s)
    if not org:
        raise HTTPException(400, "generate an org first")
    org_d = _org_dict(org)
    # skills anywhere in the org may be reused; rank them against the goal
    every = _skills(org.id)
    full = {w["id"]: w for w in every}
    cands = {c["id"]: c for t in org_d["teams"] for r in t["roles"][:1]
             for c in graph.relevant_skills(body.goal, t["key"], r["key"], None, every)}
    ranked = sorted(cands.values(), key=lambda c: -c["score"])[:5]
    out = await workflows.design_flow(org_d, body.goal, await sheets.columns(), [full[c["id"]] for c in ranked])
    steps = out["steps"]
    if not steps:
        raise HTTPException(502, "could not design a workflow for that goal; try describing the steps")
    start = next((st.get("team") for st in steps if st.get("team")), org_d["teams"][0]["key"])
    team = next(t for t in org_d["teams"] if t["key"] == start)
    meta = {"designed_by": out.get("model") or "rule-based fallback", "problem": out["problem"]}
    if out["used"]:
        meta["uses_skills"] = out["used"]
    with SessionLocal() as s:
        w = Workflow(org_id=org.id, team_key=team["key"], role_key=team["roles"][0]["key"],
                     name=(body.name or out["name"])[:120], goal=body.goal, kind="flow", steps=steps, meta=meta)
        s.add(w)
        s.commit()
        res = {**_wf_dict(w), "mocked": out["mocked"]}
    bus.emit("workflow.created", {"workflow_id": res["id"], "team": team["key"], "teams": res["teams"],
                                  "reused": [full[i]["name"] for i in out["used"] if i in full]})
    return res


@app.get("/api/flows/suggest")
async def suggest_flows():
    """A few cross-department workflow ideas for the current company."""
    with SessionLocal() as s:
        org = _latest_org(s)
    if not org:
        raise HTTPException(400, "generate an org first")
    ideas, offline = await workflows.suggest_flows(_org_dict(org))
    return {"ideas": ideas, "offline": offline}


def _skills(org_id: int) -> list[dict]:
    with SessionLocal() as s:
        q = select(Workflow).where(Workflow.org_id == org_id, Workflow.kind == "skill").order_by(Workflow.id)
        return [_wf_dict(w) for w in s.scalars(q)]


# ---------- knowledge graph ----------
@app.get("/api/graph")
def org_graph():
    """Org knowledge graph: teams, roles, people (desks), skills/workflows, tools and reuse links."""
    with SessionLocal() as s:
        org = _latest_org(s)
        if not org:
            return {"nodes": [], "edges": []}
        wfs = [_wf_dict(w) for w in s.scalars(select(Workflow).where(Workflow.org_id == org.id).order_by(Workflow.id))]
        return graph.build(_org_dict(org), wfs)


@app.get("/api/graph/suggest")
def suggest_skills(team: str, role: str, goal: str = "", person: str | None = None):
    """Existing skills a new workflow for this goal would reuse (best first)."""
    org, t, r = _team_role(team, role)
    return graph.relevant_skills(goal, t["key"], r["key"], person, _skills(org.id))


@app.put("/api/workflows/{wf_id}")
def update_workflow(wf_id: int, body: WorkflowUpdateIn, who: access.Actor = Depends(require_token)):
    with SessionLocal() as s:
        w = s.get(Workflow, wf_id)
        if not w:
            raise HTTPException(404, "workflow not found")
        if w.kind != "flow":  # skills and drafts belong to a department; cross-team flows to everyone
            access.require_edit(who, w.team_key)
        if w.kind == "hero":
            raise HTTPException(400, "the hero workflow is fixed in this version")
        steps = workflows.normalize(body.steps)
        if w.kind == "flow":  # keep teams valid and handoffs where the team changes
            org = s.get(Org, w.org_id)
            steps = workflows.link_teams(steps, org.data["teams"], w.team_key)
            w.team_key = next((x.get("team") for x in steps if x.get("team")), w.team_key)
        w.steps = steps
        if body.name:
            w.name = body.name[:120]
        s.commit()
        return _wf_dict(w)


@app.put("/api/workflows/{wf_id}/assign")
def assign_workflow(wf_id: int, body: AssignIn, who: access.Actor = Depends(require_token)):
    """Give a workflow / skill to one specific person (desk) inside a department."""
    _, team, role = _team_role(body.team_key, body.role_key)
    _check_person(role, body.person)
    with SessionLocal() as s:
        w = s.get(Workflow, wf_id)
        if not w:
            raise HTTPException(404, "workflow not found")
        access.require_edit(who, w.team_key)
        access.require_edit(who, team["key"])
        if w.kind == "hero" and (w.team_key, w.role_key) != (team["key"], role["key"]):
            raise HTTPException(400, "the hero workflow can only move between people in its own role")
        w.team_key, w.role_key = team["key"], role["key"]
        w.meta = {**(w.meta or {}), "person": body.person, "person_name": body.person_name}
        s.commit()
        out = _wf_dict(w)
    bus.emit("workflow.assigned", {"workflow_id": wf_id, "team": team["key"], "role": role["key"],
                                   "person": body.person, "person_name": body.person_name, "name": out["name"]})
    return out


def _team_role(team_key: str, role_key: str):
    with SessionLocal() as s:
        org = _latest_org(s)
    if not org:
        raise HTTPException(400, "generate an org first")
    team = next((t for t in org.data["teams"] if t["key"] == team_key), None)
    role = next((r for r in (team or {}).get("roles", []) if r["key"] == role_key), None)
    if not team or not role:
        raise HTTPException(404, "team or role not found")
    return org, team, role


# ---------- skill recorder ----------
class AnalyzeIn(BaseModel):
    team_key: str
    role_key: str
    transcript: str = Field(default="", max_length=20_000)
    frames: list[str] = Field(default_factory=list, max_length=60)
    title: str = Field(default="", max_length=120)


class SkillIn(BaseModel):
    team_key: str
    role_key: str
    name: str = Field(min_length=2, max_length=120)
    steps: list[dict]
    meta: dict = Field(default_factory=dict)


@app.post("/api/skills/analyze")
async def analyze_skill(body: AnalyzeIn, who: access.Actor = Depends(require_token)):
    access.require_edit(who, body.team_key)
    if any(len(f) > 1_500_000 for f in body.frames):
        raise HTTPException(413, "a frame is too large; send JPEG keyframes under ~1 MB")
    if not body.transcript.strip() and not body.frames:
        raise HTTPException(400, "send at least narration or some frames")
    _, team, role = _team_role(body.team_key, body.role_key)
    out = await skills.analyze(team, role, body.transcript, body.frames, await sheets.columns(), body.title)
    bus.emit("skill.analyzed", {"team": team["key"], "role": role["key"], "how": out["meta"]["how"],
                                "steps": len(out["steps"])})
    return out


@app.post("/api/skills")
def save_skill(body: SkillIn, who: access.Actor = Depends(require_token)):
    access.require_edit(who, body.team_key)
    org, team, role = _team_role(body.team_key, body.role_key)
    meta = {k: v for k, v in body.meta.items() if k not in ("frames", "tools")}  # never persist frames
    meta["created_by"] = who.name
    _check_person(role, meta.get("person"))
    with SessionLocal() as s:
        w = Workflow(org_id=org.id, team_key=team["key"], role_key=role["key"], name=body.name, goal=body.name,
                     kind="skill", steps=workflows.normalize(body.steps), meta=meta)
        s.add(w)
        s.commit()
        out = _wf_dict(w)
    bus.emit("skill.saved", {"workflow_id": out["id"], "team": team["key"], "role": role["key"], "name": out["name"]})
    return out


@app.get("/api/skills")
def list_skills():
    with SessionLocal() as s:
        org = _latest_org(s)
        if not org:
            return []
        q = select(Workflow).where(Workflow.org_id == org.id, Workflow.kind == "skill").order_by(Workflow.id)
        return [_wf_dict(w) for w in s.scalars(q)]


@app.get("/api/sheet/columns")
async def sheet_columns():
    return {"columns": await sheets.columns(), "mode": sheets.mode()}


@app.get("/api/workflows/{wf_id}/preview")
async def preview_workflow(wf_id: int):
    """Dry run of the first 'read rows' step: which rows the workflow would touch."""
    with SessionLocal() as s:
        w = s.get(Workflow, wf_id)
        if not w:
            raise HTTPException(404, "workflow not found")
        steps = w.steps or []
    read = next((x for x in steps if x.get("action") == "read_rows"), None)
    if not read:
        return {"rows": [], "note": "this workflow does not read rows"}
    rows = [r for r in await sheets.list_leads() if sheets.matches(r, read["params"].get("where", {}))]
    rows = rows[: int(read["params"].get("limit") or 10)]
    return {"rows": [{"label": engine._row_label(r), "email": r.get("email", ""), "status": r.get("status", "")}
                     for r in rows], "where": read["params"].get("where"), "mode": sheets.mode()}


@app.post("/api/workflows/{wf_id}/run", dependencies=[Depends(require_token)])
async def run_workflow(wf_id: int):
    with SessionLocal() as s:
        w = s.get(Workflow, wf_id)
        if not w:
            raise HTTPException(404, "workflow not found")
        org = s.get(Org, w.org_id)
        if w.kind == "hero":
            raise HTTPException(400, "use POST /api/runs for the lead-to-cash workflow")
        if not workflows.is_executable(w.steps or []):
            raise HTTPException(400, "this workflow has steps the engine cannot run yet")
        active = s.scalars(select(Run).where(Run.status.in_(["running", "waiting_approval"]))).first()
        if active:
            raise HTTPException(409, f"run {active.id} is still in progress")
        run = Run(org_id=w.org_id, workflow_id=w.id, status="running", context={})
        s.add(run)
        s.commit()
        run_id, wf = run.id, _wf_dict(w)
    team_names = {t["key"]: t["name"] for t in org.data["teams"]}
    t = asyncio.create_task(engine.run_workflow(run_id, wf, team_names))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return {"run_id": run_id}


# ---------- runs ----------
@app.post("/api/runs", dependencies=[Depends(require_token)])
async def start_run():
    with SessionLocal() as s:
        org = _latest_org(s)
        if not org:
            raise HTTPException(400, "generate an org first")
        wf = s.scalars(select(Workflow).where(Workflow.org_id == org.id, Workflow.kind == "hero")).first()
        active = s.scalars(select(Run).where(Run.status.in_(["running", "waiting_approval"]))).first()
        if active:
            raise HTTPException(409, f"run {active.id} is still in progress")
        run = Run(org_id=org.id, workflow_id=wf.id, status="running", context={})
        s.add(run)
        s.commit()
        run_id = run.id
    t = asyncio.create_task(engine.run_lead_to_cash(run_id))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return {"run_id": run_id}


def _run_dict(s, run: Run, with_events: bool = False) -> dict:
    out = {"id": run.id, "status": run.status, "context": run.context,
           "created_at": run.created_at, "finished_at": run.finished_at}
    if with_events:
        out["events"] = [{"id": e.id, "type": e.type, "payload": e.payload, "ts": e.created_at}
                         for e in s.scalars(select(Event).where(Event.run_id == run.id).order_by(Event.id))]
    return out


@app.get("/api/runs")
def list_runs():
    with SessionLocal() as s:
        return [_run_dict(s, r) for r in s.scalars(select(Run).order_by(desc(Run.id)).limit(20))]


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    with SessionLocal() as s:
        run = s.get(Run, run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return _run_dict(s, run, with_events=True)


# ---------- approvals ----------
class DecisionIn(BaseModel):
    approved: bool
    who: str = "human"


@app.get("/api/approvals")
def list_approvals(status: str = "pending"):
    with SessionLocal() as s:
        q = select(Approval).order_by(desc(Approval.id))
        if status != "all":
            q = q.where(Approval.status == status)
        return [{"id": a.id, "run_id": a.run_id, "team_key": a.team_key, "title": a.title,
                 "payload": a.payload, "status": a.status, "decided_by": a.decided_by,
                 "created_at": a.created_at} for a in s.scalars(q.limit(50))]


@app.post("/api/approvals/{approval_id}")
def decide(approval_id: int, body: DecisionIn, who: access.Actor = Depends(require_token)):
    with SessionLocal() as s:
        a = s.get(Approval, approval_id)
        team = a.team_key if a else ""
    if who.user and not who.can_edit(team):
        raise HTTPException(403, f"only the {team} department (or an admin) can approve this")
    try:
        return engine.decide(approval_id, body.approved, who.user.name if who.user else body.who)
    except KeyError:
        raise HTTPException(404, "approval not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---------- skill tools: MCP servers + connectors a skill may use ----------
class MCPServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=8, max_length=500)
    token: str = Field(default="", max_length=2000)


@app.get("/api/mcp/servers")
def mcp_servers():
    return [mcp.public(x) for x in mcp.servers()]


@app.post("/api/mcp/servers", dependencies=[Depends(require_admin)])
async def add_mcp_server(body: MCPServerIn):
    """Add an MCP server (Streamable HTTP) and list its tools."""
    try:
        srv = await mcp.add(body.name, body.url, body.token)
    except registry.UnsafeURL as e:
        raise HTTPException(400, str(e))
    bus.emit("mcp.changed", {"server": srv["id"], "tools": len(srv["tools"])})
    return mcp.public(srv)


@app.post("/api/mcp/servers/{server_id}/refresh", dependencies=[Depends(require_admin)])
async def refresh_mcp_server(server_id: str):
    try:
        return mcp.public(await mcp.refresh(server_id))
    except mcp.MCPError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/mcp/servers/{server_id}", dependencies=[Depends(require_admin)])
def remove_mcp_server(server_id: str):
    mcp.remove(server_id)
    bus.emit("mcp.changed", {"removed": server_id})
    return {"ok": True}


@app.get("/api/tools")
def tool_catalog():
    """Everything a skill can be given access to (connectors and MCP servers with their tools)."""
    return agent.catalog()


class ToolsIn(BaseModel):
    tools: list[str] = Field(default_factory=list, max_length=100)


@app.put("/api/workflows/{wf_id}/tools")
def set_skill_tools(wf_id: int, body: ToolsIn, who: access.Actor = Depends(require_token)):
    """Choose which tools a skill may use. Empty = no limit (and no tools for its AI agent step)."""
    try:
        grants = agent.valid_grants(body.tools)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with SessionLocal() as s:
        w = s.get(Workflow, wf_id)
        if not w:
            raise HTTPException(404, "workflow not found")
        if w.kind == "hero":
            raise HTTPException(400, "the hero workflow is fixed in this version")
        access.require_edit(who, w.team_key)
        w.meta = {**(w.meta or {}), "tools": grants}
        s.commit()
        out = _wf_dict(w)
    bus.emit("skill.tools", {"workflow_id": wf_id, "tools": grants, "name": out["name"]})
    return out


# ---------- requests: ask any team; the best skill runs and its owners hear about it ----------
class RequestIn(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
    from_team: str = Field(default="", max_length=80)
    workflow_id: int | None = None   # pick a skill yourself instead of letting OrgTwin choose


_ROUTE_SYSTEM = """You route a work request to the one skill that can do it.
Skills (id · department · name · what it does):
{skills}
Reply with ONLY JSON: {{"skill_id": <id or null if none fits>, "why": "<one short sentence>"}}."""


async def _route(text: str, skills: list[dict], from_team: str) -> tuple[dict | None, str]:
    if not skills:
        return None, "no skills exist yet"
    by_id = {w["id"]: w for w in skills}

    def offline():
        best = graph.relevant_skills(text, from_team or "-", "-", None, skills, limit=1, min_score=0.2)
        return {"skill_id": best[0]["id"], "why": f"matched: {', '.join(best[0]['matched'][:5])}"} if best else \
            {"skill_id": None, "why": "no skill matched the words in the request"}
    lines = "\n".join(f"{w['id']} · {w['team_key']} · {w['name']} · " +
                      "; ".join(st.get("title", "") for st in w["steps"][:6]) for w in skills)
    data, _, _ = await llm.complete_json(_ROUTE_SYSTEM.format(skills=lines), f"Request: {text}", fallback=offline)
    if not isinstance(data, dict):
        data = offline()
    sid = data.get("skill_id")
    try:
        sid = int(sid) if sid is not None else None
    except (TypeError, ValueError):
        sid = None
    if sid not in by_id:
        data = offline()
        sid = data.get("skill_id")
    return by_id.get(sid), str(data.get("why") or "")[:300]


def _req_dict(r: WorkRequest) -> dict:
    return {"id": r.id, "text": r.text, "from_name": r.from_name, "from_team": r.from_team, "workflow_id": r.workflow_id,
            "team_key": r.team_key, "run_id": r.run_id, "status": r.status, "why": r.why, "result": r.result,
            "notified": r.notified or [], "created_at": r.created_at.isoformat() if r.created_at else None}


def _update_req(req_id: int, **kv) -> dict:
    with SessionLocal() as s:
        r = s.get(WorkRequest, req_id)
        for k, v in kv.items():
            setattr(r, k, v)
        s.commit()
        out = _req_dict(r)
    bus.emit("request.updated", out)
    return out


async def _notify(team_key: str, team_name: str, text: str, requester: User | None) -> list[dict]:
    """Tell a department's owners in Slack and/or Discord (each person picks; @mentions when their IDs are set)."""
    owners = access.owners_of(team_key)
    sent = []
    for channel in ("slack", "discord"):
        people = [u for u in owners if (u.notify or {}).get(channel, True)]
        if not people:
            continue
        idk = f"{channel}_id"
        mentions = " ".join((f"<@{u.notify[idk]}>" if (u.notify or {}).get(idk) else u.name) for u in people)
        cc = ""
        if requester and (requester.notify or {}).get(idk):
            cc = f" · cc <@{requester.notify[idk]}>"
        msg = f"{text}\n👤 {team_name}: {mentions}{cc}"
        try:
            if channel == "slack":
                rec = await slack.post(msg)
                mode = rec["mode"]
            else:
                rec = await registry.run("discord", "post", {"text": msg})
                mode = rec["mode"]
            sent.append({"channel": channel, "mode": mode, "to": [u.name for u in people]})
        except Exception as e:  # a broken webhook must not fail the request
            sent.append({"channel": channel, "error": f"{type(e).__name__}: {e}"[:200], "to": [u.name for u in people]})
    return sent


_request_lock = asyncio.Lock()


async def _serve_request(req_id: int, wf: dict, team_names: dict, inputs: dict, requester_id: int | None) -> None:
    """Wait for a free slot, run the skill with the request as input, then tell the skill's owners."""
    team_name = team_names.get(wf["team_key"], wf["team_key"])
    for _ in range(600):  # one run at a time (the world shows one); wait up to ~10 min
        async with _request_lock:
            with SessionLocal() as s:
                busy = s.scalars(select(Run).where(Run.status.in_(["running", "waiting_approval"]))).first()
                if not busy:
                    run = Run(org_id=wf["org_id"], workflow_id=wf["id"], status="running", context={"request_id": req_id})
                    s.add(run)
                    s.commit()
                    run_id = run.id
                    break
        await asyncio.sleep(1)
    else:
        _update_req(req_id, status="failed", result="timed out waiting for another run to finish")
        return
    _update_req(req_id, status="running", run_id=run_id)
    await engine.run_workflow(run_id, wf, team_names, inputs)
    with SessionLocal() as s:
        run = s.get(Run, run_id)
        status, ctx = run.status, run.context or {}
        err = next((e.payload.get("error", "") for e in s.scalars(
            select(Event).where(Event.run_id == run_id, Event.type == "run.failed").order_by(desc(Event.id)).limit(1))), "")
        requester = s.get(User, requester_id) if requester_id else None
    outs = [st["params"].get("save_as") for st in wf["steps"] if st.get("params", {}).get("save_as")]
    vars_ = ctx.get("vars") or {}
    result = next((vars_[k] for k in reversed(outs) if vars_.get(k)), "") if status == "completed" else err
    if status == "completed" and not result:
        result = f"Done — {wf['name']} ran" + (f" for {ctx.get('rows')} row(s)." if ctx.get("rows") else ".")
    head = f"📥 Request from {inputs.get('requested_by')} ({inputs.get('requester_team') or 'someone'}): “{inputs['request'][:300]}”"
    if status == "completed":
        note = f"{head}\n✅ Your skill “{wf['name']}” handled it. Result:\n{result[:1200]}"
    else:
        note = f"{head}\n⚠️ Your skill “{wf['name']}” could not finish: {(result or status)[:400]}"
    notified = await _notify(wf["team_key"], team_name, note, requester)
    _update_req(req_id, status="done" if status == "completed" else "failed", result=result[:8000], notified=notified)


@app.post("/api/requests")
async def make_request(body: RequestIn, who: access.Actor = Depends(require_token)):
    """“I need an analysis of …” — OrgTwin picks the best skill in any department, runs it with the request
    as input ({{request}}), and tells that department's people in Slack / Discord."""
    with SessionLocal() as s:
        org = _latest_org(s)
        if not org:
            raise HTTPException(400, "generate an org first")
        requester = s.get(User, who.id) if who.id else None
    from_team = body.from_team or (who.departments()[0] if who.departments() else "")
    candidates = [w for w in _skills(org.id) if w["executable"]]
    if body.workflow_id is not None:
        wf = next((w for w in candidates if w["id"] == body.workflow_id), None)
        if not wf:
            raise HTTPException(404, "that skill does not exist or cannot run")
        why = "picked by the person asking"
    else:
        wf, why = await _route(body.text, candidates, from_team)
    teams = {t["key"]: t["name"] for t in org.data["teams"]}
    with SessionLocal() as s:
        r = WorkRequest(org_id=org.id, text=body.text.strip(), from_user_id=who.id, from_name=who.name, from_team=from_team,
                        workflow_id=wf["id"] if wf else None, team_key=wf["team_key"] if wf else "",
                        status="queued" if wf else "no_skill", why=why)
        s.add(r)
        s.commit()
        out = _req_dict(r)
    bus.emit("request.created", out)
    if not wf:  # nobody has a skill for this yet: ask the most likely department to make one
        guess = workflows._guess_team(body.text, org.data["teams"]) or next(iter(teams), "")
        note = (f"📥 Request from {who.name} ({teams.get(from_team, from_team) or 'someone'}): “{body.text[:300]}”\n"
                f"🛠 No skill can do this yet. Could {teams.get(guess, guess)} record one?")
        notified = await _notify(guess, teams.get(guess, guess), note, requester) if guess else []
        return _update_req(out["id"], team_key=guess, notified=notified)
    inputs = {"request": body.text.strip(), "requested_by": who.name, "requester_team": teams.get(from_team, from_team)}
    t = asyncio.create_task(_serve_request(out["id"], wf, teams, inputs, who.id))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return out


@app.get("/api/requests")
def list_requests(limit: int = 30):
    with SessionLocal() as s:
        return [_req_dict(r) for r in s.scalars(select(WorkRequest).order_by(desc(WorkRequest.id)).limit(min(limit, 100)))]


# ---------- quests: the setup checklist (green ticks) ----------
@app.get("/api/quests")
def quests():
    with SessionLocal() as s:
        org = _latest_org(s)
        users = list(s.scalars(select(User)))
        skills_ = list(s.scalars(select(Workflow).where(Workflow.kind == "skill"))) if org else []
        reqs = list(s.scalars(select(WorkRequest).order_by(desc(WorkRequest.id)).limit(50)))
    live = [c for c in list_connectors() if c["mode"] == "live" and c["key"] != "web"]
    q = [
        ("org", "Build your company", bool(org), "Describe it or upload an org CSV"),
        ("admin", "Become the admin", bool(users), "👥 People → Become admin"),
        ("connect", "Connect an app", bool(live), "🔌 Connectors → Connect (Slack or Discord first)"),
        ("invite", "Invite a teammate", len(users) > 1, "👥 People → Invite someone from IT"),
        ("assign", "Give someone a department", any(u.role != "admin" and u.departments for u in users),
         "Tick the departments they look after"),
        ("skill", "Create a skill", bool(skills_), "Click a worker → Record a skill"),
        ("tools", "Give a skill tools", any((w.meta or {}).get("tools") for w in skills_), "📘 Skill → 🧰 Tools (MCP, GitHub…)"),
        ("request", "Send a cross-team request", bool(reqs), "📥 Ask a team: “I need an analysis of …”"),
        ("notified", "Owner notified in Slack / Discord", any(any(n.get("mode") for n in (r.notified or [])) for r in reqs),
         "Happens when a request finishes"),
    ]
    return [{"key": k, "title": t, "done": d, "hint": h} for k, t, d, h in q]


# ---------- outbox / audit / metrics ----------
@app.get("/api/outbox")
def outbox():
    return {"emails": email.outbox[-50:][::-1], "slack": slack.posted[-50:][::-1],
            "messages": registry.sent[-50:][::-1]}


# ---------- connectors ----------
@app.get("/api/connectors")
def list_connectors():
    """Every connector, whether it is live or in demo mode, what it needs, and the actions it offers."""
    core = [
        {"key": "sheets", "name": "Google Sheets", "icon": "📊", "category": "data", "mode": "live" if sheets.mode() != "mock" else "mock",
         "env": ["GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SHEET_ID"], "setup": "Sign in with Google and pick a sheet.", "actions": [], "core": True},
        {"key": "email", "name": "Email (Gmail / SMTP)", "icon": "✉️", "category": "chat", "mode": "live" if email.mode() != "mock" else "mock",
         "env": ["SMTP_HOST", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD"], "setup": "Sign in with Google to send from your Gmail, or use any SMTP server.", "actions": [], "core": True},
        {"key": "slack", "name": "Slack", "icon": "💬", "category": "chat", "mode": "live" if slack.mode() != "mock" else "mock",
         "env": ["SLACK_WEBHOOK_URL"], "setup": "Create an incoming webhook for a channel.", "actions": [], "core": True},
    ]
    out = core + registry.catalog()
    for c in out:
        c["connectable"] = c["key"] in userconfig.CONNECTOR_SETUP
        if c["connectable"]:
            st = _connection(c["key"])
            c["signin"], c["account"] = st["signin"], st["account"]
            c["connected_in_app"] = st.get("connected_in_app") or bool(st["account"])
    return out


class ConnectorTestIn(BaseModel):
    url: str = Field(default="", max_length=2000)


@app.post("/api/connectors/{key}/test", dependencies=[Depends(require_admin)])
async def test_connector(key: str, body: ConnectorTestIn | None = None):
    if key not in registry.CONNECTORS:
        raise HTTPException(404, "unknown connector (core connectors are tested by running the lead-to-cash demo)")
    try:
        out = await registry.test(key, (body.url if body else ""))
    except registry.UnsafeURL as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # report the provider's error to the person setting it up
        return {"ok": False, "mode": registry.CONNECTORS[key].mode(), "message": f"{type(e).__name__}: {e}"[:300]}
    bus.emit("connector.tested", {"connector": key, **out})
    return out


# ---------- connect connectors from the app (sign-in popups + short forms) ----------
def _connection(key: str) -> dict:
    """What the Connect popup needs for one connector: how to connect and what is connected now."""
    form = userconfig.connector_form(key) or {"fields": [], "steps": [], "link": "", "link_label": ""}
    oa, avail = userconfig.oauth_state(), oauth.available()
    signin = oauth.CONNECTOR_SIGNIN.get(key, "")
    account = ""
    if signin == "google":
        account = (settings.google_account or "Google account") if settings.google_signed_in() else ""
    elif signin in oa:
        account = oa[signin].get("name") or f"{signin.title()} account"
    return {**form, "signin": signin, "signin_available": bool(signin and avail.get(signin)),
            "signin_env": oauth.missing_env(signin) if signin else "",
            "account": account, "needs_sheet": key == "sheets" and bool(account) and not settings.sheet_id,
            "sheet_id": settings.sheet_id if key == "sheets" else "",
            "needs_database": key == "notion" and bool(account) and not settings.notion_database_id,
            "needs_repo": key == "github" and bool(settings.github_token) and not settings.github_repo,
            "repo": settings.github_repo if key == "github" else "",
            "database_id": settings.notion_database_id if key == "notion" else ""}


@app.get("/api/setup/connectors/{key}")
def connector_setup(key: str):
    if key not in userconfig.CONNECTOR_SETUP:
        raise HTTPException(404, "unknown connector")
    return _connection(key)


class ConnectorValuesIn(BaseModel):
    values: dict[str, str | bool] = Field(default_factory=dict)


async def _test_after_save(key: str) -> dict:
    try:
        if key == "sheets":
            if sheets.mode() == "mock":
                return {"ok": False, "message": "Saved — add a sheet to finish."}
            cols = await sheets.columns()
            return {"ok": True, "message": f"Connected — columns: {', '.join(cols[:8]) or '(empty sheet)'}"}
        if key == "email":
            if email.mode() == "mock":
                return {"ok": False, "message": "Saved — not complete yet."}
            return {"ok": True, "message": f"Email is live ({email.mode()})."}
        if key == "slack":
            if slack.mode() == "mock":
                return {"ok": False, "message": "Saved — not complete yet."}
            await slack.post("✅ OrgTwin is connected — agents can post here now.")
            return {"ok": True, "message": "Test message sent to Slack."}
        if key in registry.CONNECTORS and key != "webhook":
            return await registry.test(key)
        return {"ok": True, "message": "Saved."}
    except Exception as e:
        return {"ok": False, "message": f"Saved, but the check failed: {type(e).__name__}: {e}"[:300]}


@app.put("/api/setup/connectors/{key}", dependencies=[Depends(require_admin)])
async def save_connector(key: str, body: ConnectorValuesIn):
    """Save what the user typed in the Connect popup, then check the connection."""
    if key not in userconfig.CONNECTOR_SETUP:
        raise HTTPException(404, "unknown connector")
    allowed = {f.env for f in userconfig.CONNECTOR_SETUP[key].fields}
    vals = {k: v for k, v in body.values.items() if k in allowed}
    if any(isinstance(v, str) and len(v) > 20_000 for v in vals.values()):
        raise HTTPException(400, "value too long")
    userconfig.save_connector(key, vals)
    result = await _test_after_save(key)
    bus.emit("connector.tested", {"connector": key, **result})
    return {**result, "setup": _connection(key)}


@app.delete("/api/setup/connectors/{key}", dependencies=[Depends(require_admin)])
async def disconnect_connector(key: str):
    """Forget the connector's saved values; for Google/Stripe also sign out (revokes the app's access)."""
    if key not in userconfig.CONNECTOR_SETUP:
        raise HTTPException(404, "unknown connector")
    userconfig.clear_connector(key)
    signin = oauth.CONNECTOR_SIGNIN.get(key)
    if signin in oauth.PROVIDERS or signin == "telegram":
        await oauth.disconnect(signin)
    return {"ok": True, "setup": _connection(key)}


@app.post("/api/oauth/{provider}/start", dependencies=[Depends(require_admin)])
def oauth_start(provider: str):
    """The URL the Connect popup opens: the provider's own sign-in and "Allow access" screen."""
    try:
        return {"url": oauth.start(provider)}
    except oauth.OAuthError as e:
        raise HTTPException(400, str(e))


@app.get("/api/oauth/{provider}/callback", response_class=HTMLResponse)
async def oauth_callback(provider: str, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """The provider sends the popup back here; we finish the sign-in and the popup closes itself."""
    if error:
        msg = "Access was not allowed." if error == "access_denied" else f"Sign-in failed: {error_description or error}"
        return oauth.popup_page(provider, False, msg[:200])
    try:
        msg = await oauth.finish(provider, code, state)
    except oauth.OAuthError as e:
        return oauth.popup_page(provider, False, str(e))
    except httpx.HTTPError:
        return oauth.popup_page(provider, False, "Could not reach the provider — try again.")
    bus.emit("connector.connected", {"provider": provider})
    return oauth.popup_page(provider, True, msg)


@app.get("/api/oauth/notion/databases", dependencies=[Depends(require_admin)])
async def notion_databases():
    """Databases and pages the user shared with OrgTwin while signing in to Notion."""
    if "notion" not in userconfig.oauth_state():
        raise HTTPException(400, "connect Notion first")
    try:
        return {"databases": await oauth.notion_databases(), "pages": await oauth.notion_pages()}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


class NotionPickIn(BaseModel):
    database_id: str = Field(default="", max_length=100)
    create_in_page: str = Field(default="", max_length=100)


@app.post("/api/oauth/notion/database", dependencies=[Depends(require_admin)])
async def notion_pick_database(body: NotionPickIn):
    """Use one of the shared databases, or create an "OrgTwin" database inside a shared page."""
    if "notion" not in userconfig.oauth_state():
        raise HTTPException(400, "connect Notion first")
    db_id = body.database_id
    if not db_id and body.create_in_page:
        try:
            db_id = (await oauth.notion_create_database(body.create_in_page))["id"]
        except RuntimeError as e:
            raise HTTPException(400, str(e))
    if not db_id:
        raise HTTPException(400, "pick a database or a page")
    userconfig.save_connector("notion", {"NOTION_DATABASE_ID": db_id})
    result = await _test_after_save("notion")
    return {**result, "setup": _connection("notion")}


@app.get("/api/oauth/github/repos", dependencies=[Depends(require_admin)])
async def github_repos():
    """Repos the connected GitHub account can reach (newest first), to pick the default one."""
    if not settings.github_token:
        raise HTTPException(400, "connect GitHub first")
    try:
        return {"repos": await oauth.github_repos(), "current": settings.github_repo}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.post("/api/connect/telegram/start", dependencies=[Depends(require_admin)])
async def telegram_start():
    """A t.me link to OrgTwin's bot carrying a one-time code; the user taps Start and we learn the chat."""
    try:
        return await oauth.telegram_start()
    except oauth.OAuthError as e:
        raise HTTPException(400, str(e))


@app.get("/api/connect/telegram/check", dependencies=[Depends(require_admin)])
async def telegram_check(code: str):
    try:
        out = await oauth.telegram_check(code)
    except oauth.OAuthError as e:
        raise HTTPException(400, str(e))
    except (RuntimeError, httpx.HTTPError) as e:
        raise HTTPException(502, str(e)[:200])
    if out["connected"]:
        bus.emit("connector.connected", {"provider": "telegram"})
    return {**out, "setup": _connection("telegram")}


@app.post("/api/oauth/google/sample-sheet", dependencies=[Depends(require_admin)])
async def google_sample_sheet():
    """Create a ready-made CRM sheet with sample leads in the signed-in user's Google Drive and use it."""
    if not settings.google_signed_in():
        raise HTTPException(400, "connect Google first")
    try:
        sid = await oauth.create_sample_sheet(sheets._MOCK_LEADS, sheets.COLUMNS)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    userconfig.save_connector("sheets", {"GOOGLE_SHEET_ID": sid})
    return {"ok": True, "sheet_id": sid, "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit",
            "setup": _connection("sheets")}


@app.get("/api/audit")
def audit(limit: int = 100):
    with SessionLocal() as s:
        return [{"id": e.id, "run_id": e.run_id, "type": e.type, "payload": e.payload, "ts": e.created_at}
                for e in s.scalars(select(Event).order_by(desc(Event.id)).limit(min(limit, 500)))]


@app.get("/api/metrics")
def metrics():
    with SessionLocal() as s:
        org = _latest_org(s)
        runs = s.scalars(select(Run)).all()
        completed = [r for r in runs if r.status == "completed" and (r.context or {}).get("hours_saved")]
        steps = s.scalar(select(func.count()).select_from(Event).where(Event.type == "step.completed")) or 0
        approvals = s.scalar(select(func.count()).select_from(Approval)) or 0
    teams = org.data["teams"] if org else []
    return {
        "runs_total": len(runs),
        "runs_completed": len(completed),
        "steps_executed": steps,
        "approvals": approvals,
        "hours_saved": round(sum(r.context["hours_saved"] for r in completed), 2),
        "manual_minutes_per_run": engine.MANUAL_MINUTES,
        "automatable_hours_per_week": round(sum(t.get("automatable_hours", 0) for t in teams), 1),
    }


@app.post("/api/demo/reset", dependencies=[Depends(require_admin)])
def demo_reset():
    sheets.reset_mock()
    return {"ok": True}


# ---------- live events ----------
@app.get("/api/events/stream")
async def stream(request: Request):
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield sse_format(data)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
