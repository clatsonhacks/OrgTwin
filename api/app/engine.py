"""Lead-to-cash: the hero workflow that runs for real across Sales → Finance → Customer Success."""
import asyncio
import json
import traceback

from . import a2a, llm, runner
from .config import settings
from .connectors import email, sheets, slack, stripe_c
from .db import Approval, Run, SessionLocal, now
from .events import bus

# Each step: who does it, which connector, and how long it takes a human (for "hours saved").
HERO_STEPS = [
    {"key": "pick_lead", "team": "sales", "title": "Pick next new lead from CRM", "tool": "sheets", "manual_min": 3},
    {"key": "score_lead", "team": "sales", "title": "Score lead in isolated sandbox", "tool": "sandbox", "manual_min": 10},
    {"key": "qualify", "team": "sales", "title": "Research & qualify, draft outreach", "tool": "llm", "manual_min": 20},
    {"key": "outreach", "team": "sales", "title": "Send outreach email, update CRM", "tool": "email", "manual_min": 5},
    {"key": "deal_won", "team": "sales", "title": "Deal marked won", "tool": "sheets", "manual_min": 2},
    {"key": "handoff_finance", "team": "sales", "to": "finance", "title": "Hand off to Finance agent", "tool": "a2a", "manual_min": 10},
    {"key": "draft_invoice", "team": "finance", "title": "Create draft invoice", "tool": "stripe", "manual_min": 10},
    {"key": "approve_invoice", "team": "finance", "title": "Human approval: send invoice", "tool": "approval", "manual_min": 0},
    {"key": "send_invoice", "team": "finance", "title": "Finalize & email invoice", "tool": "email", "manual_min": 8},
    {"key": "handoff_support", "team": "finance", "to": "support", "title": "Hand off to Customer Success agent", "tool": "a2a", "manual_min": 10},
    {"key": "onboarding", "team": "support", "title": "Send personalised onboarding email", "tool": "email", "manual_min": 20},
    {"key": "notify", "team": "support", "title": "Post summary to team channel", "tool": "slack", "manual_min": 5},
]
MANUAL_MINUTES = sum(s["manual_min"] for s in HERO_STEPS)

# Executed inside the sandbox: pure function of the lead, no network, no secrets.
SCORING_CODE = r"""
import json, sys
lead = json.load(sys.stdin)
notes = (lead.get("notes") or "").lower()
score, reasons = 0, []
try:
    value = float(lead.get("deal_value") or 0)
except ValueError:
    value = 0
if value >= 5000: score += 40; reasons.append("deal value >= 5000")
elif value >= 2000: score += 25; reasons.append("deal value >= 2000")
for word, pts in (("urgent", 20), ("this quarter", 15), ("budget", 15), ("referral", 10), ("inbound", 10)):
    if word in notes: score += pts; reasons.append(word)
if "exploring" in notes: score -= 10; reasons.append("still exploring")
score = max(0, min(100, score))
print(json.dumps({"score": score, "tier": "A" if score >= 60 else "B" if score >= 35 else "C", "reasons": reasons}))
"""

_approval_events: dict[int, asyncio.Event] = {}


def hero_workflow_steps() -> list[dict]:
    return [dict(s) for s in HERO_STEPS]


def _ctx_update(run_id: int, **kv) -> dict:
    with SessionLocal() as s:
        run = s.get(Run, run_id)
        run.context = {**(run.context or {}), **kv}
        s.commit()
        return run.context


def _set_status(run_id: int, status: str) -> None:
    with SessionLocal() as s:
        run = s.get(Run, run_id)
        run.status = status
        if status in ("completed", "failed", "rejected"):
            run.finished_at = now()
        s.commit()


async def _step(run_id: int, key: str, fn):
    step = next(s for s in HERO_STEPS if s["key"] == key)
    bus.emit("step.started", {"step": key, "team": step["team"], "title": step["title"], "tool": step["tool"]}, run_id)
    await asyncio.sleep(settings.step_delay_s)
    result = await fn()
    bus.emit("step.completed", {"step": key, "team": step["team"], "title": step["title"],
                                "tool": step["tool"], "result": result}, run_id)
    return result


async def request_approval(run_id: int, team: str, title: str, payload: dict) -> bool:
    with SessionLocal() as s:
        ap = Approval(run_id=run_id, team_key=team, title=title, payload=payload)
        s.add(ap)
        s.commit()
        ap_id = ap.id
    ev = asyncio.Event()
    _approval_events[ap_id] = ev
    _set_status(run_id, "waiting_approval")
    bus.emit("approval.requested", {"approval_id": ap_id, "team": team, "title": title, "payload": payload}, run_id)
    await slack.post(f":hourglass: OrgTwin needs approval: *{title}* — {settings.public_app_url}")
    await ev.wait()
    _approval_events.pop(ap_id, None)
    with SessionLocal() as s:
        ap = s.get(Approval, ap_id)
        approved = ap.status == "approved"
    _set_status(run_id, "running" if approved else "rejected")
    return approved


def decide(approval_id: int, approved: bool, who: str) -> dict:
    with SessionLocal() as s:
        ap = s.get(Approval, approval_id)
        if ap is None:
            raise KeyError("approval not found")
        if ap.status != "pending":
            raise ValueError(f"approval already {ap.status}")
        ap.status = "approved" if approved else "rejected"
        ap.decided_by, ap.decided_at = who[:120], now()
        s.commit()
        out = {"approval_id": ap.id, "status": ap.status, "team": ap.team_key, "run_id": ap.run_id}
    bus.emit("approval.decided", out, out["run_id"])
    ev = _approval_events.get(approval_id)
    if ev:
        ev.set()
    return out


def _qualify_fallback(lead: dict, score: dict) -> str:
    first = (lead.get("name") or "there").split()[0]
    return json.dumps({
        "summary": f"{lead.get('company')} — tier {score.get('tier')} ({score.get('score')}/100). "
                   f"Signals: {', '.join(score.get('reasons') or ['none'])}.",
        "qualified": score.get("score", 0) >= 35,
        "email_subject": f"Next steps for {lead.get('company')}",
        "email_body": f"Hi {first},\n\nThanks for your interest. Based on what you shared "
                      f"({lead.get('notes')}), I think we can help quickly. "
                      "Could we set up a 20-minute call this week?\n\nBest,\nThe Sales team",
    })


def _onboarding_fallback(lead: dict) -> str:
    first = (lead.get("name") or "there").split()[0]
    return (f"Hi {first},\n\nWelcome aboard! Here is how we'll get {lead.get('company')} live:\n"
            "1. Kick-off call this week\n2. Connect your data sources\n3. First automated report in 7 days\n\n"
            "Reply to this email with any questions.\n\nYour Customer Success team")


async def run_lead_to_cash(run_id: int) -> None:
    try:
        await _run(run_id)
    except Exception as e:  # surface the failure in the UI + audit log
        _set_status(run_id, "failed")
        bus.emit("run.failed", {"error": f"{type(e).__name__}: {e}",
                                "trace": traceback.format_exc()[-1500:]}, run_id)


async def _run(run_id: int) -> None:
    bus.emit("run.started", {"workflow": "lead-to-cash", "steps": hero_workflow_steps(),
                             "modes": settings.modes()}, run_id)

    lead = await _step(run_id, "pick_lead", sheets.next_new_lead)
    if not lead:
        _set_status(run_id, "completed")
        bus.emit("run.completed", {"result": "no new leads in CRM"}, run_id)
        return
    _ctx_update(run_id, lead=lead)

    async def score():
        res = await runner.run_python(SCORING_CODE, lead)
        if not res.get("ok") or not isinstance(res.get("output"), dict):
            raise RuntimeError(f"sandbox failed: {res.get('error') or res.get('stderr')}")
        return res
    scored = await _step(run_id, "score_lead", score)
    score_out = scored["output"]
    _ctx_update(run_id, score=score_out)

    async def qualify():
        res = await llm.complete(
            "You are the Sales agent of a B2B company. Return ONLY JSON: "
            '{"summary": str, "qualified": bool, "email_subject": str, "email_body": str}. '
            "The email is a short, friendly first-touch reply that proposes a call. Sign as 'The Sales team'.",
            f"Lead: {json.dumps(lead)}\nSandbox score: {json.dumps(score_out)}",
            fallback=lambda: _qualify_fallback(lead, score_out))
        try:
            data = llm.extract_json(res.text)
        except Exception:
            data = json.loads(_qualify_fallback(lead, score_out))
        return {**data, "llm": {"model": res.model, "mocked": res.mocked}}
    q = await _step(run_id, "qualify", qualify)
    _ctx_update(run_id, qualification=q)

    async def outreach():
        sent = await email.send(lead["email"], q["email_subject"], q["email_body"])
        crm = await sheets.update_status(lead["id"], "Contacted")
        return {"email": sent, "crm": crm}
    await _step(run_id, "outreach", outreach)

    # Demo shortcut: in production this step waits for the buyer's reply / signed order form.
    async def deal_won():
        crm = await sheets.update_status(lead["id"], "Won")
        return {"crm": crm, "note": "Buyer acceptance simulated for the demo"}
    await _step(run_id, "deal_won", deal_won)

    amount_cents = int(float(lead.get("deal_value") or 0) * 100)
    task = a2a.handoff("sales", "finance", f"Invoice {lead['company']} for the won deal",
                       {"lead": lead, "amount_cents": amount_cents, "score": score_out})

    async def handoff_fin():
        bus.emit("handoff", {"from": "sales", "to": "finance", "task": task}, run_id)
        return {"a2a_task": task}
    await _step(run_id, "handoff_finance", handoff_fin)

    async def draft():
        return await stripe_c.create_draft_invoice(lead["email"], lead["name"], amount_cents,
                                                   f"OrgTwin subscription — {lead['company']}")
    inv = await _step(run_id, "draft_invoice", draft)
    _ctx_update(run_id, invoice=inv)

    bus.emit("step.started", {"step": "approve_invoice", "team": "finance",
                              "title": "Human approval: send invoice", "tool": "approval"}, run_id)
    approved = await request_approval(
        run_id, "finance", f"Send invoice of ${amount_cents / 100:,.2f} to {lead['company']}",
        {"invoice": inv, "lead": {k: lead[k] for k in ("name", "company", "email")}})
    bus.emit("step.completed", {"step": "approve_invoice", "team": "finance", "tool": "approval",
                                "title": "Human approval: send invoice",
                                "result": {"approved": approved}}, run_id)
    if not approved:
        await sheets.update_status(lead["id"], "Invoice rejected")
        bus.emit("run.completed", {"result": "invoice rejected by human", "hours_saved": 0}, run_id)
        return

    async def send_inv():
        fin = await stripe_c.finalize_invoice(inv["invoice_id"], amount_cents)
        link = fin.get("hosted_invoice_url") or "(demo mode: no payment link)"
        sent = await email.send(lead["email"], f"Invoice for {lead['company']}",
                                f"Hi {lead['name'].split()[0]},\n\nThanks for choosing us. Your invoice for "
                                f"${amount_cents / 100:,.2f} is ready: {link}\n\nFinance team")
        crm = await sheets.update_status(lead["id"], "Invoiced")
        return {"invoice": fin, "email": sent, "crm": crm}
    await _step(run_id, "send_invoice", send_inv)

    task2 = a2a.handoff("finance", "support", f"Onboard {lead['company']}", {"lead": lead, "invoice": inv})

    async def handoff_sup():
        bus.emit("handoff", {"from": "finance", "to": "support", "task": task2}, run_id)
        return {"a2a_task": task2}
    await _step(run_id, "handoff_support", handoff_sup)

    async def onboarding():
        res = await llm.complete(
            "You are the Customer Success agent. Write a warm, concise onboarding email (plain text, "
            "max 120 words) with 3 numbered first steps. Sign as 'Your Customer Success team'. Return only the email body.",
            f"Customer: {json.dumps(lead)}", fallback=lambda: _onboarding_fallback(lead))
        sent = await email.send(lead["email"], f"Welcome, {lead['company']}!", res.text.strip())
        return {"email": sent, "llm": {"model": res.model, "mocked": res.mocked}}
    await _step(run_id, "onboarding", onboarding)

    hours_saved = round(MANUAL_MINUTES / 60, 2)

    async def notify():
        return await slack.post(f":white_check_mark: Lead-to-cash done for *{lead['company']}*: "
                                f"contacted → won → invoiced ${amount_cents / 100:,.2f} → onboarded. "
                                f"~{hours_saved} h of manual work saved.")
    await _step(run_id, "notify", notify)

    _ctx_update(run_id, hours_saved=hours_saved)
    _set_status(run_id, "completed")
    bus.emit("run.completed", {"result": "lead-to-cash complete", "hours_saved": hours_saved,
                               "company": lead["company"]}, run_id)


# =====================================================================================
# Generic engine: runs designed drafts and recorded skills built from the step library
# (see workflows.py). Consecutive per-row steps form a loop over the rows read earlier.
# =====================================================================================
import re as _re

from . import workflows as _wf
from .connectors import registry

_TPL = _re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def render(text: str, ctx: dict, row: dict | None) -> str:
    def sub(m):
        key = m.group(1)
        if key.startswith("row.") and row is not None:
            col = key[4:].lower()
            return str(next((v for k, v in row.items() if k.lower() == col), ""))
        if key == "count":
            return str(ctx.get("count", 0))
        return str(ctx.get("vars", {}).get(key, ""))
    return _TPL.sub(sub, text or "")


def _row_label(row: dict | None) -> str:
    if not row:
        return ""
    return str(row.get("name") or row.get("company") or row.get("email") or row.get("id") or "row")


def _email_fallback(row: dict | None, instruction: str, team_name: str) -> str:
    first = (_row_label(row).split() or ["there"])[0]
    notes = (row or {}).get("notes")
    ctx_line = f"Following up on your note — {notes}." if notes else "Just following up on our last conversation."
    if "payment" in instruction.lower() or "invoice" in instruction.lower():
        ctx_line = "A friendly reminder that your invoice is still open — happy to help if anything is unclear."
    return f"Hi {first},\n\n{ctx_line} Would you have 15 minutes this week to talk next steps?\n\nBest,\nThe {team_name} team"


async def run_workflow(run_id: int, wf: dict, team_names: dict[str, str] | str, inputs: dict | None = None) -> None:
    """team_names: {team_key: name} for the whole org (a plain string still works for one team).
    inputs: starting {{vars}}, e.g. {"request": "…", "requested_by": "…"} when someone asked for this work."""
    if isinstance(team_names, str):
        team_names = {wf["team_key"]: team_names}
    try:
        await _run_generic(run_id, wf, team_names, inputs or {})
    except Exception as e:
        _set_status(run_id, "failed")
        bus.emit("run.failed", {"error": f"{type(e).__name__}: {e}",
                                "trace": traceback.format_exc()[-1500:]}, run_id)


async def _exec(step: dict, ctx: dict, row: dict | None, team: str, team_name: str) -> dict:
    p, tool, action = step["params"], step["tool"], step["action"]
    if tool == "sheets" and action == "read_rows":
        rows = [r for r in await sheets.list_leads() if sheets.matches(r, p.get("where", {}))]
        rows = rows[: int(p.get("limit") or 10)]
        ctx["rows"], ctx["count"] = rows, len(rows)
        return {"count": len(rows), "rows": [_row_label(r) for r in rows], "where": p.get("where"), "mode": sheets.mode()}
    if tool == "sheets" and action == "update_row":
        if not row or "id" not in row:
            raise RuntimeError("update_row needs a row with an 'id' column")
        return await sheets.update_cell(str(row["id"]), p["column"], render(p["value"], ctx, row))
    if tool == "llm":
        instr = render(p["instruction"], ctx, row)
        data: dict = {"row": row} if row else {"rows": ctx.get("rows", [])[:20]}
        if ctx.get("vars"):  # earlier results (web search, notes, handoff messages) are context too
            data["earlier_results"] = {k: str(v)[:3000] for k, v in ctx["vars"].items()}
        res = await llm.complete(f"You are the {team_name} agent. Do the instruction. Plain text, concise.",
                                 f"Instruction: {instr}\nData: {json.dumps(data, default=str)[:12000]}",
                                 fallback=lambda: f"{instr} — done for {ctx.get('count', 0)} rows (demo mode).")
        ctx.setdefault("vars", {})[p["save_as"]] = res.text.strip()
        return {"save_as": p["save_as"], "text": res.text.strip()[:600], "llm": {"model": res.model, "mocked": res.mocked}}
    if tool == "email":
        to = render(p["to"], ctx, row).strip()
        if not to:
            raise RuntimeError("email step has no recipient (set params.to, e.g. {{row.email}})")
        instr = render(p["instruction"], ctx, row)
        res = await llm.complete(f"You are the {team_name} agent writing an email. Return only the email body, "
                                 f"plain text, signed 'The {team_name} team'.",
                                 f"Instruction: {instr}\nRecipient data: {json.dumps(row or {})}",
                                 fallback=lambda: _email_fallback(row, instr, team_name))
        sent = await email.send(to, render(p["subject"], ctx, row), res.text.strip())
        return {"email": sent, "llm": {"model": res.model, "mocked": res.mocked}}
    if tool == "slack":
        return await slack.post(render(p["text"], ctx, row))
    if tool == "agent":
        from . import agent
        instr = render(p["instruction"], ctx, row)
        data = {"earlier_results": {k: str(v)[:3000] for k, v in ctx.get("vars", {}).items()},
                **({"rows": [_row_label(r) for r in ctx["rows"][:20]]} if ctx.get("rows") else {})}

        async def approve(tool_name: str, args: dict) -> bool:
            return await request_approval(ctx["run_id"], team, f"{team_name} agent wants to use {tool_name}",
                                          {"tool": tool_name, "args": args, "step": step["title"]})

        def on_call(call: dict) -> None:
            bus.emit("agent.tool", {"team": team, "step": step["title"], **call}, ctx["run_id"])
        out = await agent.run(instr, data, team_name, ctx.get("tools") or [], approve, on_call)
        ctx.setdefault("vars", {})[p["save_as"]] = out["text"]
        return {"save_as": p["save_as"], "text": out["text"][:800], "tool_calls": out["calls"],
                "llm": {"model": out["model"], "mocked": out["mocked"]}}
    if tool in registry.CONNECTORS:
        out = await registry.run(tool, action, {k: render(str(v), ctx, row) for k, v in p.items()})
        if out.get("save_as") and "text" in out:  # e.g. web search results → {{search}}
            ctx.setdefault("vars", {})[out["save_as"]] = out["text"]
            out = {**out, "text": out["text"][:800]}
        return out
    raise RuntimeError(f"step {tool}.{action} cannot run in the generic engine")


async def _run_generic(run_id: int, wf: dict, team_names: dict[str, str], inputs: dict | None = None) -> None:
    from . import agent
    steps = wf["steps"]
    if not _wf.is_executable(steps):
        raise RuntimeError("this workflow uses steps the engine cannot run yet")
    grants = list((wf.get("meta") or {}).get("tools") or [])
    blocked = sorted({s["tool"] for s in steps if not agent.step_allowed(s["tool"], grants)})
    if blocked:
        raise RuntimeError(f"this skill has no access to: {', '.join(blocked)} (give it access under 🧰 Tools)")
    home = wf["team_key"]
    team_of = [s.get("team") or home for s in steps]  # each step is done by its own department's agent
    view = [{"key": f"s{i}", "team": team_of[i], "title": s["title"], "tool": s["tool"], "per_row": s["per_row"],
             **({"to": s["params"].get("to")} if s["tool"] == "a2a" else {})} for i, s in enumerate(steps)]
    teams_in_order = list(dict.fromkeys(team_of))
    bus.emit("run.started", {"workflow": wf["name"], "workflow_id": wf["id"], "kind": wf["kind"],
                             "teams": teams_in_order, "steps": view, "modes": settings.modes()}, run_id)
    ctx: dict = {"rows": [], "count": 0, "vars": {k: str(v) for k, v in (inputs or {}).items()},
                 "run_id": run_id, "tools": grants}
    name = lambda k: team_names.get(k, k.replace("-", " ").title())  # noqa: E731

    async def do(i: int, row: dict | None):
        s, team = steps[i], team_of[i]
        title = s["title"] + (f" → {_row_label(row)}" if row else "")
        bus.emit("step.started", {"step": f"s{i}", "team": team, "title": title, "tool": s["tool"]}, run_id)
        await asyncio.sleep(settings.step_delay_s)
        result = await _exec(s, ctx, row, team, name(team))
        bus.emit("step.completed", {"step": f"s{i}", "team": team, "title": title, "tool": s["tool"],
                                    "result": result}, run_id)

    async def handoff(i: int):
        s, frm = steps[i], team_of[i]
        to = s["params"].get("to") or frm
        message = render(s["params"].get("message") or s["title"], ctx, None)
        task = a2a.handoff(frm, to, message, {"count": ctx["count"], "rows": [_row_label(r) for r in ctx["rows"]][:20],
                                              "results": {k: str(v)[:500] for k, v in ctx["vars"].items()}})
        bus.emit("step.started", {"step": f"s{i}", "team": frm, "title": s["title"], "tool": "a2a"}, run_id)
        await asyncio.sleep(settings.step_delay_s)
        bus.emit("handoff", {"from": frm, "to": to, "task": task}, run_id)
        ctx["vars"]["handoff"] = message
        bus.emit("step.completed", {"step": f"s{i}", "team": frm, "title": s["title"], "tool": "a2a",
                                    "result": {"a2a_task": task, "to": to}}, run_id)

    i = 0
    approved_just_now = False  # an approval step right before covers the next sending step(s)
    while i < len(steps):
        s = steps[i]
        if s["tool"] == "a2a":
            await handoff(i)
            i += 1
            continue
        if s["per_row"]:
            j = i
            while j < len(steps) and steps[j]["per_row"] and team_of[j] == team_of[i]:
                j += 1
            group = list(range(i, j))
            rows = ctx["rows"]
            if any(steps[k]["needs_approval"] for k in group) and rows and not approved_just_now:
                ok = await request_approval(run_id, team_of[i], f"{wf['name']}: run for {len(rows)} row(s)", {
                    "rows": [{"label": _row_label(r), "email": r.get("email", "")} for r in rows],
                    "steps": [steps[k]["title"] for k in group]})
                if not ok:
                    bus.emit("run.completed", {"result": "stopped: a person rejected it", "hours_saved": 0}, run_id)
                    return
            approved_just_now = False
            for row in rows:
                for k in group:
                    await do(k, row)
            if not rows:
                for k in group:
                    bus.emit("step.completed", {"step": f"s{k}", "team": team_of[k], "title": steps[k]["title"],
                                                "tool": steps[k]["tool"], "result": {"skipped": "no matching rows"}}, run_id)
            i = j
            continue
        if (s["needs_approval"] and not approved_just_now) or (s["tool"] == "approval"):
            title = render(s["params"].get("title") or s["title"], ctx, None)
            if s["tool"] == "approval":
                bus.emit("step.started", {"step": f"s{i}", "team": team_of[i], "title": s["title"], "tool": "approval"}, run_id)
            ok = await request_approval(run_id, team_of[i], title, {"step": s["title"], "count": ctx["count"]})
            if not ok:
                bus.emit("run.completed", {"result": "stopped: a person rejected it", "hours_saved": 0}, run_id)
                return
            if s["tool"] == "approval":
                bus.emit("step.completed", {"step": f"s{i}", "team": team_of[i], "title": s["title"], "tool": "approval",
                                            "result": {"approved": True}}, run_id)
                approved_just_now = True
                i += 1
                continue
        await do(i, None)
        approved_just_now = approved_just_now and steps[i]["tool"] == "sheets"  # reading rows keeps the approval
        i += 1

    hours = round(_wf.manual_minutes(steps, ctx["count"]) / 60, 2)
    _ctx_update(run_id, hours_saved=hours, rows=ctx["count"],
                vars={k: str(v)[:4000] for k, v in ctx["vars"].items()})
    _set_status(run_id, "completed")
    chain = " → ".join(name(t) for t in teams_in_order)
    done = f"{wf['name']} — done" + (f" for {ctx['count']} row(s)" if ctx["count"] else "")
    bus.emit("run.completed", {"result": done + (f" · {chain}" if len(teams_in_order) > 1 else ""),
                               "hours_saved": hours, "rows": ctx["count"]}, run_id)
