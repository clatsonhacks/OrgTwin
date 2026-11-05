"use client";
import { useEffect, useState } from "react";
import { api, post, put } from "@/lib/api";
import type { Step, Team, Workflow } from "@/lib/types";
import { teamColor } from "@/components/world/layout";
import StepEditor, { StepList, newStep } from "./StepEditor";

export default function TeamDrawer({ team, index, selectedRole, onSelectRole, workflows, onChanged, onError, onClose,
                                     onRunHero, onRunWorkflow, onRecord }: {
  team: Team; index: number; selectedRole: string | null; onSelectRole: (k: string) => void;
  workflows: Workflow[]; onChanged: () => void; onError: (m: string) => void; onClose: () => void;
  onRunHero: () => void; onRunWorkflow: (id: number) => void; onRecord: () => void;
}) {
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const role = team.roles.find((r) => r.key === selectedRole) ?? null;
  const color = teamColor(team.key, index);
  const teamWfs = workflows.filter((w) => w.team_key === team.key);
  const roleSkills = (k: string) => workflows.filter((w) => w.kind === "skill" && w.team_key === team.key && w.role_key === k).length;
  const maxRole = Math.max(1, ...team.roles.map((r) => r.automatable_hours));

  useEffect(() => { setGoal(""); }, [selectedRole]);

  const generate = async () => {
    if (!role) return;
    setBusy(true);
    try { await post("/api/workflows/generate", { team_key: team.key, role_key: role.key, goal }); setGoal(""); onChanged(); }
    catch (e: any) { onError(e.message); } finally { setBusy(false); }
  };

  return (
    <aside className="drawer block" style={{ ["--team" as any]: color }}>
      <header className="drawer-head">
        <div className="swatch" />
        <div className="grow">
          <div className="eyebrow">Department</div>
          <h2>{team.name}</h2>
        </div>
        <button className="x" onClick={onClose} aria-label="Close">✕</button>
      </header>
      <div className="stats3">
        <div><b>{team.headcount}</b><span>people</span></div>
        <div><b>{team.roles.length}</b><span>agents</span></div>
        <div><b>{team.automatable_hours}</b><span>h/wk to automate</span></div>
      </div>

      <section>
        <h3>Agents in this team</h3>
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

      <section className="builder">
        <h3>{role ? `Teach ${role.name}` : "Teach an agent"}</h3>
        {!role ? <p className="muted">Pick an agent above (or click a worker in the world).</p> : (
          <>
            <button className="btn btn-rec" onClick={onRecord}>🎥 Record a skill <small>show it once, the agent learns it</small></button>
            <div className="or"><span>or describe it</span></div>
            <div className="chips-row">
              {role.suggested_workflows.map((s) => <button key={s} className="chip" onClick={() => setGoal(s)}>{s}</button>)}
            </div>
            <textarea rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="e.g. Email every overdue invoice a reminder and mark it Reminded" />
            <button className="btn btn-go" disabled={busy || goal.trim().length < 3} onClick={generate}>
              {busy ? "Designing workflow…" : "✦ Design workflow"}
            </button>
          </>
        )}
      </section>

      {teamWfs.map((w) => (
        <WorkflowCard key={w.id} wf={w} onSaved={onChanged} onError={onError}
                      onRun={() => (w.kind === "hero" ? onRunHero() : onRunWorkflow(w.id))} />
      ))}
    </aside>
  );
}

export function WorkflowCard({ wf, onSaved, onError, onRun, defaultOpen }: {
  wf: Workflow; onSaved: () => void; onError: (m: string) => void; onRun: () => void; defaultOpen?: boolean;
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
  return (
    <section className="wf">
      <button className="wf-head" onClick={() => setOpen(!open)}>
        <span className="grow"><b>{wf.kind === "hero" ? "⚡ " : ""}{wf.name}</b></span>
        <span className={`tag ${tag[1]}`}>{tag[0]}</span>
        <span className="muted">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <>
          {wf.kind === "skill" && wf.meta?.how && <p className="muted">Learned from a recording ({wf.meta.how}).</p>}
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
