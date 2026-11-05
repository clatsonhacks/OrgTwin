"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, post, put } from "@/lib/api";
import type { Org, SkillMatch, Step, Team, Workflow } from "@/lib/types";
import { ownerOf, peopleOf, type Person } from "@/lib/people";
import { teamColor } from "@/components/world/layout";
import StepEditor, { StepList, newStep } from "./StepEditor";

export default function TeamDrawer({ org, team, index, selectedRole, onSelectRole, workflows, onChanged, onError, onClose,
                                     onRunHero, onRunWorkflow, onRecord, inside, onEnter, onExit, selectedPerson, onSelectPerson }: {
  org: Org; team: Team; index: number; selectedRole: string | null; onSelectRole: (k: string) => void;
  workflows: Workflow[]; onChanged: () => void; onError: (m: string) => void; onClose: () => void;
  onRunHero: () => void; onRunWorkflow: (id: number) => void; onRecord: (person: Person | null) => void;
  inside: boolean; onEnter: () => void; onExit: () => void;
  selectedPerson: string | null; onSelectPerson: (id: string | null) => void;
}) {
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const touchY = useRef<number | null>(null);
  useEffect(() => setCollapsed(false), [selectedPerson, selectedRole, inside]);
  const people = useMemo(() => peopleOf(team), [team]);
  const person = inside ? people.find((p) => p.id === selectedPerson) ?? null : null;
  const role = team.roles.find((r) => r.key === (person?.role ?? selectedRole)) ?? null;
  const color = teamColor(team.key, index);
  const teamWfs = workflows.filter((w) => w.team_key === team.key);
  const roleSkills = (k: string) => workflows.filter((w) => w.kind === "skill" && w.team_key === team.key && w.role_key === k).length;
  const personWfs = (id: string) => teamWfs.filter((w) => ownerOf(w) === id);
  const nameOf = (id: string) => people.find((p) => p.id === id)?.name ?? id;
  const maxRole = Math.max(1, ...team.roles.map((r) => r.automatable_hours));

  useEffect(() => { setGoal(""); }, [selectedRole, selectedPerson]);

  // live preview: which existing skills (from the org knowledge graph) the new workflow will reuse
  const [matches, setMatches] = useState<SkillMatch[]>([]);
  useEffect(() => {
    if (!role || goal.trim().length < 4) { setMatches([]); return; }
    const q = new URLSearchParams({ team: team.key, role: role.key, goal, ...(person ? { person: person.id } : {}) });
    const t = setTimeout(() => { api<SkillMatch[]>(`/api/graph/suggest?${q}`).then(setMatches).catch(() => setMatches([])); }, 300);
    return () => clearTimeout(t);
  }, [goal, role, person, team.key]);
  const whoHas = (m: SkillMatch) => {
    const t = org.teams.find((x) => x.key === m.team_key);
    const p = t ? peopleOf(t).find((x) => x.id === m.person) : null;
    return `${p?.name ?? m.role_key} · ${t?.name ?? m.team_key}`;
  };

  const generate = async () => {
    if (!role) return;
    setBusy(true);
    try {
      await post("/api/workflows/generate", { team_key: team.key, role_key: role.key, goal,
                                              ...(person ? { person: person.id, person_name: person.name } : {}) });
      setGoal(""); onChanged();
    } catch (e: any) { onError(e.message); } finally { setBusy(false); }
  };

  return (
    <aside className={`drawer block ${collapsed ? "collapsed" : ""}`} style={{ ["--team" as any]: color }}>
      <button className="sheet-handle" aria-label={collapsed ? "Expand panel" : "Collapse panel"}
              onClick={() => setCollapsed(!collapsed)}
              onTouchStart={(e) => { touchY.current = e.touches[0].clientY; }}
              onTouchEnd={(e) => {
                const d = touchY.current == null ? 0 : e.changedTouches[0].clientY - touchY.current;
                touchY.current = null;
                if (d > 30) { e.preventDefault(); setCollapsed(true); } else if (d < -30) { e.preventDefault(); setCollapsed(false); }
              }}><i /></button>
      <header className="drawer-head">
        <div className="swatch" />
        <div className="grow">
          <div className="eyebrow">{inside ? "Inside the office" : "Department"}</div>
          <h2>{team.name}</h2>
        </div>
        <button className="x" onClick={onClose} aria-label="Close">✕</button>
      </header>
      <div className="stats3">
        <div><b>{team.headcount}</b><span>people</span></div>
        <div><b>{team.roles.length}</b><span>roles</span></div>
        <div><b>{team.automatable_hours}</b><span>h/wk to automate</span></div>
      </div>

      <section>
        {inside
          ? <button className="btn btn-wide" onClick={onExit}>⬅ Back to the campus</button>
          : <button className="btn btn-go btn-wide" onClick={onEnter}>🚪 Step inside <small>see everyone at their desk</small></button>}
      </section>

      {inside ? (
        <>
          <section>
            <h3>People at their desks</h3>
            {team.roles.map((r) => {
              const mine = people.filter((p) => p.role === r.key);
              const extra = r.headcount - mine.length;
              return (
                <div key={r.key} className="people-group">
                  <div className="eyebrow">{r.name} <span className="muted">· {r.headcount}×</span></div>
                  <div className="people">
                    {mine.map((p) => {
                      const n = personWfs(p.id).length;
                      return (
                        <button key={p.id} className={`person ${p.id === selectedPerson ? "on" : ""}`} onClick={() => onSelectPerson(p.id)}>
                          <span className="avatar" /><span>{p.name}</span>{n > 0 && <span className="skillcount">📘 {n}</span>}
                        </button>
                      );
                    })}
                    {extra > 0 && <span className="muted person-more">+{extra} more</span>}
                  </div>
                </div>
              );
            })}
          </section>
          {person && role && (
            <PersonCard org={org} team={team} person={person} tasks={role.tasks} workflows={workflows} owned={personWfs(person.id)}
                        onChanged={onChanged} onError={onError} />
          )}
        </>
      ) : (
        <section>
          <h3>Roles in this team</h3>
          <ul className="roles">
            {team.roles.map((r) => (
              <li key={r.key}>
                <button className={`role ${r.key === selectedRole ? "on" : ""}`} onClick={() => onSelectRole(r.key)}>
                  <span className="avatar" />
                  <span className="grow">
                    <b>{r.name}</b>{roleSkills(r.key) > 0 && <span className="skillcount">📘 {roleSkills(r.key)}</span>}
                    <span className="bar"><i style={{ width: `${(r.automatable_hours / maxRole) * 100}%` }} /></span>
                  </span>
                  <span className="muted">{r.headcount}× · {r.automatable_hours}h</span>
                </button>
                {r.key === selectedRole && r.tasks.length > 0 && (
                  <ul className="tasks">{r.tasks.map((t) => <li key={t}>{t}</li>)}</ul>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="builder">
        <h3>{person ? `Teach ${person.name}` : role ? `Teach ${role.name}` : "Teach someone"}</h3>
        {!role ? <p className="muted">{inside ? "Click someone at a desk (or pick a name above)." : "Pick a role above, or step inside and pick a person."}</p> : (
          <>
            <button className="btn btn-rec" onClick={() => onRecord(person)}>🎥 Record a skill <small>show it once, {person ? person.name : "the agent"} learns it</small></button>
            <div className="or"><span>or describe it</span></div>
            <div className="chips-row">
              {role.suggested_workflows.map((s) => <button key={s} className="chip" onClick={() => setGoal(s)}>{s}</button>)}
            </div>
            <textarea rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="e.g. Email every overdue invoice a reminder and mark it Reminded" />
            {matches.length > 0 && (
              <div className="reuse">
                <div className="eyebrow">♻ Will reuse an existing skill</div>
                {matches.map((m, k) => (
                  <div key={m.id} className={`reuse-row ${k === 0 ? "on" : ""}`}>
                    <b>📘 {m.name}</b>
                    <span className="muted">{whoHas(m)} · {m.why} · {Math.round(m.score * 100)}% match</span>
                  </div>
                ))}
              </div>
            )}
            <button className="btn btn-go" disabled={busy || goal.trim().length < 3} onClick={generate}>
              {busy ? "Designing workflow…" : person ? `✦ Design workflow for ${person.name}` : "✦ Design workflow"}
            </button>
          </>
        )}
      </section>

      {(person ? personWfs(person.id) : teamWfs).map((w) => (
        <WorkflowCard key={w.id} wf={w} onSaved={onChanged} onError={onError} owner={inside && !person ? nameOf(ownerOf(w)) : undefined}
                      onRun={() => (w.kind === "hero" ? onRunHero() : onRunWorkflow(w.id))} />
      ))}
    </aside>
  );
}

/** One person at a desk: what they do, which skills they own, and a picker to hand them another skill. */
function PersonCard({ org, team, person, tasks, workflows, owned, onChanged, onError }: {
  org: Org; team: Team; person: Person; tasks: string[]; workflows: Workflow[]; owned: Workflow[];
  onChanged: () => void; onError: (m: string) => void;
}) {
  const [pick, setPick] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const candidates = workflows.filter((w) =>
    !(w.team_key === team.key && ownerOf(w) === person.id) &&
    (w.kind !== "hero" || (w.team_key === team.key && w.role_key === person.role)));
  const ownerLabel = (w: Workflow) => {
    const t = org.teams.find((x) => x.key === w.team_key);
    const p = t ? peopleOf(t).find((x) => x.id === ownerOf(w)) : null;
    return `${p?.name ?? w.role_key} · ${t?.name ?? w.team_key}`;
  };
  useEffect(() => setPick(""), [person.id]);

  const assign = async () => {
    if (!pick) return;
    setBusy(true);
    try {
      await put(`/api/workflows/${pick}/assign`, { team_key: team.key, role_key: person.role, person: person.id, person_name: person.name });
      setPick(""); onChanged();
    } catch (e: any) { onError(e.message); } finally { setBusy(false); }
  };

  return (
    <section className="person-card">
      <div className="person-head">
        <span className="avatar avatar-lg" />
        <div className="grow">
          <h3>{person.name}</h3>
          <div className="muted">{person.roleName} · desk {person.seat + 1}</div>
        </div>
        <span className="tag tag-skill">📘 {owned.length}</span>
      </div>
      {tasks.length > 0 && <ul className="tasks">{tasks.map((t) => <li key={t}>{t}</li>)}</ul>}
      <div className="assign">
        <label className="eyebrow" htmlFor="assign-pick">Give {person.name} a skill</label>
        {candidates.length === 0 ? <p className="muted">No other skills or workflows yet — record or design one below.</p> : (
          <div className="row">
            <select id="assign-pick" className="grow" value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">Choose a skill or workflow…</option>
              {candidates.map((w) => (
                <option key={w.id} value={w.id}>{w.kind === "skill" ? "📘" : w.kind === "hero" ? "⚡" : "✦"} {w.name} — now: {ownerLabel(w)}</option>
              ))}
            </select>
            <button className="btn btn-go" disabled={!pick || busy} onClick={assign}>{busy ? "…" : "Assign"}</button>
          </div>
        )}
      </div>
    </section>
  );
}

export function WorkflowCard({ wf, onSaved, onError, onRun, defaultOpen, owner }: {
  wf: Workflow; onSaved: () => void; onError: (m: string) => void; onRun: () => void; defaultOpen?: boolean; owner?: string;
}) {
  const [steps, setSteps] = useState<Step[]>(wf.steps);
  const [dirty, setDirty] = useState(false);
  const [open, setOpen] = useState(defaultOpen ?? wf.kind !== "hero");
  const [editing, setEditing] = useState(false);
  const [preview, setPreview] = useState<{ rows: { label: string; email: string }[] } | null>(null);
  useEffect(() => { setSteps(wf.steps); setDirty(false); }, [wf.steps]);
  useEffect(() => {
    if (!open || wf.kind === "hero" || !wf.steps.some((s) => s.action === "read_rows")) return;
    api<{ rows: { label: string; email: string }[] }>(`/api/workflows/${wf.id}/preview`).then(setPreview).catch(() => setPreview(null));
  }, [open, wf.id, wf.kind, wf.steps]);
  const save = async () => {
    try { await put(`/api/workflows/${wf.id}`, { steps }); setDirty(false); setEditing(false); onSaved(); } catch (e: any) { onError(e.message); }
  };
  const tag = wf.kind === "hero" ? ["runs live", "tag-go"] : wf.kind === "skill" ? ["📘 recorded skill", "tag-skill"] : ["designed", ""];
  const reused = Array.from(new Map(wf.steps.filter((s) => s.from_skill).map((s) => [s.from_skill!.id, s.from_skill!.name])).values());
  return (
    <section className="wf">
      <button className="wf-head" onClick={() => setOpen(!open)}>
        <span className="grow"><b>{wf.kind === "hero" ? "⚡ " : ""}{wf.name}</b>{owner && <span className="muted wf-owner">👤 {owner}</span>}</span>
        <span className={`tag ${tag[1]}`}>{tag[0]}</span>
        <span className="muted">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <>
          {wf.kind === "skill" && wf.meta?.how && <p className="muted">Learned from a recording ({wf.meta.how}).</p>}
          {reused.length > 0 && <p className="muted">♻ Built on existing skill{reused.length > 1 ? "s" : ""}: {reused.map((n) => `📘 ${n}`).join(", ")}</p>}
          {editing ? <StepEditor steps={steps} onChange={(s) => { setSteps(s); setDirty(true); }} /> : <StepList steps={steps} />}
          {preview && preview.rows.length > 0 && !editing && (
            <p className="preview-rows">Next run touches <b>{preview.rows.length}</b> row(s): {preview.rows.slice(0, 4).map((r) => r.label).join(", ")}{preview.rows.length > 4 ? "…" : ""}</p>
          )}
          <div className="row">
            {wf.kind !== "hero" && !editing && <button className="btn" onClick={() => setEditing(true)}>Edit</button>}
            {editing && <>
              <button className="btn" onClick={() => { setSteps([...steps, newStep()]); setDirty(true); }}>+ Step</button>
              <button className="btn" onClick={() => { setSteps(wf.steps); setDirty(false); setEditing(false); }}>Cancel</button>
              <button className="btn btn-go" disabled={!dirty} onClick={save}>Save</button>
            </>}
            <span className="grow" />
            {!editing && (wf.executable || wf.kind === "hero"
              ? <button className="btn btn-go" onClick={onRun}>▶ Run</button>
              : <span className="muted">Has steps the engine can&apos;t run yet</span>)}
          </div>
        </>
      )}
    </section>
  );
}
