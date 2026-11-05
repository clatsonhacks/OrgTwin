"""OrgTwin API."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from . import a2a, engine, graph, llm, orggen, skills, workflows
from .config import settings
from .connectors import email, registry, sheets, slack
from .db import Approval, Event, Org, Run, SessionLocal, Workflow, init_db
from .events import bus, sse_format

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="OrgTwin API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                   allow_headers=["*"])
_tasks: set[asyncio.Task] = set()


def require_token(x_access_token: str = Header(default="")) -> None:
    if settings.app_access_token and x_access_token != settings.app_access_token:
        raise HTTPException(401, "missing or wrong X-Access-Token")


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


# ---------- org ----------
class OrgIn(BaseModel):
    description: str = Field(default="", max_length=4000)
    url: str = Field(default="", max_length=500)
    csv: str = Field(default="", max_length=200_000)


@app.post("/api/org/generate", dependencies=[Depends(require_token)])
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


@app.post("/api/workflows/generate", dependencies=[Depends(require_token)])
async def generate_workflow(body: WorkflowGenIn):
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


@app.put("/api/workflows/{wf_id}", dependencies=[Depends(require_token)])
def update_workflow(wf_id: int, body: WorkflowUpdateIn):
    with SessionLocal() as s:
        w = s.get(Workflow, wf_id)
        if not w:
            raise HTTPException(404, "workflow not found")
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


@app.put("/api/workflows/{wf_id}/assign", dependencies=[Depends(require_token)])
def assign_workflow(wf_id: int, body: AssignIn):
    """Give a workflow / skill to one specific person (desk) inside a department."""
    _, team, role = _team_role(body.team_key, body.role_key)
    _check_person(role, body.person)
    with SessionLocal() as s:
        w = s.get(Workflow, wf_id)
        if not w:
            raise HTTPException(404, "workflow not found")
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


@app.post("/api/skills/analyze", dependencies=[Depends(require_token)])
async def analyze_skill(body: AnalyzeIn):
    if any(len(f) > 1_500_000 for f in body.frames):
        raise HTTPException(413, "a frame is too large; send JPEG keyframes under ~1 MB")
    if not body.transcript.strip() and not body.frames:
        raise HTTPException(400, "send at least narration or some frames")
    _, team, role = _team_role(body.team_key, body.role_key)
    out = await skills.analyze(team, role, body.transcript, body.frames, await sheets.columns(), body.title)
    bus.emit("skill.analyzed", {"team": team["key"], "role": role["key"], "how": out["meta"]["how"],
                                "steps": len(out["steps"])})
    return out


@app.post("/api/skills", dependencies=[Depends(require_token)])
def save_skill(body: SkillIn):
    org, team, role = _team_role(body.team_key, body.role_key)
    meta = {k: v for k, v in body.meta.items() if k != "frames"}  # never persist frames
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


@app.post("/api/approvals/{approval_id}", dependencies=[Depends(require_token)])
def decide(approval_id: int, body: DecisionIn):
    try:
        return engine.decide(approval_id, body.approved, body.who)
    except KeyError:
        raise HTTPException(404, "approval not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


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
         "env": ["GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SHEET_ID"], "setup": "Share the sheet with the service account email.", "actions": [], "core": True},
        {"key": "email", "name": "Email (SMTP)", "icon": "✉️", "category": "chat", "mode": "live" if email.mode() != "mock" else "mock",
         "env": ["SMTP_HOST", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD"], "setup": "Gmail: smtp.gmail.com + an app password.", "actions": [], "core": True},
        {"key": "slack", "name": "Slack", "icon": "💬", "category": "chat", "mode": "live" if slack.mode() != "mock" else "mock",
         "env": ["SLACK_WEBHOOK_URL"], "setup": "Create an incoming webhook for a channel.", "actions": [], "core": True},
    ]
    return core + registry.catalog()


class ConnectorTestIn(BaseModel):
    url: str = Field(default="", max_length=2000)


@app.post("/api/connectors/{key}/test", dependencies=[Depends(require_token)])
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


@app.post("/api/demo/reset", dependencies=[Depends(require_token)])
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
