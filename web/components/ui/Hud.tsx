"use client";
import { useMemo, useState } from "react";
import type { Approval, LiveEvent, Metrics, Org, Outbox, Status, Step, Workflow } from "@/lib/types";
import { stepIcon } from "@/lib/api";
import { teamColor } from "@/components/world/layout";

/* ---------- top bar ---------- */

export function TopBar({ org, metrics, status, onRebuild }: {
  org: Org | null; metrics: Metrics | null; status: Status | null; onRebuild: () => void;
}) {
  const mocked = status ? Object.entries(status.modes).filter(([k, v]) => v === "mock" && k !== "runner").map(([k]) => k) : [];
  return (
    <header className="topbar">
      <div className="brand block">
        <CubeLogo />
        <div>
          <b>OrgTwin</b>
          <span>{org ? org.company : "AI operations twin"}</span>
        </div>
        {org && <button className="linkbtn" onClick={onRebuild}>rebuild</button>}
      </div>
      <div className="grow" />
      {metrics && org && (
        <div className="stats block">
          <Stat icon="⚡" value={`${metrics.automatable_hours_per_week}h`} label="automatable / wk" />
          <Stat icon="▶" value={`${metrics.runs_completed}`} label="runs done" />
          <Stat icon="▣" value={`${metrics.steps_executed}`} label="steps executed" />
          <Stat icon="⏱" value={`${metrics.hours_saved}h`} label="saved" accent />
        </div>
      )}
      {status && (
        <div className={`mode block ${status.demo_mode ? "mode-demo" : "mode-live"}`}
             title={Object.entries(status.modes).map(([k, v]) => `${k}: ${v}`).join("\n")}>
          <i />{status.demo_mode ? <>Demo mode<small>mock: {mocked.join(", ")}</small></> : <>Live<small>Vultr inference + real connectors</small></>}
        </div>
      )}
    </header>
  );
}

function Stat({ icon, value, label, accent }: { icon: string; value: string; label: string; accent?: boolean }) {
  return (
    <div className={`stat ${accent ? "stat-accent" : ""}`}>
      <span className="stat-icon">{icon}</span>
      <div><b>{value}</b><small>{label}</small></div>
    </div>
  );
}

function CubeLogo() {
  return (
    <svg width="34" height="34" viewBox="0 0 34 34" aria-hidden>
      <path d="M17 3 30 10 17 17 4 10Z" fill="#7bd651" stroke="#1b1f2a" strokeWidth="2" strokeLinejoin="round" />
      <path d="M4 10 17 17 17 31 4 24Z" fill="#9a6a44" stroke="#1b1f2a" strokeWidth="2" strokeLinejoin="round" />
      <path d="M30 10 17 17 17 31 30 24Z" fill="#7d5034" stroke="#1b1f2a" strokeWidth="2" strokeLinejoin="round" />
    </svg>
  );
}

/* ---------- departments list (2D fallback for the world) ---------- */

export function Departments({ org, selected, onSelect }: { org: Org; selected: string | null; onSelect: (k: string) => void }) {
  const [open, setOpen] = useState(true);
  const max = Math.max(1, ...org.teams.map((t) => t.automatable_hours));
  return (
    <nav className="depts block">
      <button className="depts-head" onClick={() => setOpen(!open)}>
        <span className="eyebrow">Departments</span><span className="muted">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ul>
          {org.teams.map((t, i) => (
            <li key={t.key}>
              <button className={selected === t.key ? "on" : ""} onClick={() => onSelect(t.key)} style={{ ["--team" as any]: teamColor(t.key, i) }}>
                <span className="sw" />
                <span className="grow">{t.name}<span className="bar"><i style={{ width: `${(t.automatable_hours / max) * 100}%` }} /></span></span>
                <span className="muted">{t.automatable_hours}h</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}

/* ---------- hotbar: live lead-to-cash run ---------- */

const STEP_ICON: Record<string, string> = {
  pick_lead: "📇", score_lead: "🧪", qualify: "🧠", outreach: "✉️", deal_won: "🤝", handoff_finance: "📦",
  draft_invoice: "🧾", approve_invoice: "✋", send_invoice: "💸", handoff_support: "📦", onboarding: "🎉", notify: "💬",
};

export type RunView = {
  st: Record<string, "running" | "done">; results: Record<string, any>; finished?: LiveEvent; current?: string;
  currentTitle?: string; currentTeam?: string; steps?: Step[]; name?: string; kind?: string;
};

export function useRunView(events: LiveEvent[], runId: number | null): RunView {
  return useMemo(() => {
    const st: RunView["st"] = {}; const results: Record<string, any> = {};
    let finished: LiveEvent | undefined; let current: string | undefined;
    let currentTitle: string | undefined, currentTeam: string | undefined, steps: Step[] | undefined, name: string | undefined, kind: string | undefined;
    for (const e of events) {
      if (e.run_id !== runId) continue;
      if (e.type === "run.started") { steps = e.payload.steps; name = e.payload.workflow; kind = e.payload.kind ?? "hero"; }
      if (e.type === "step.started") { st[e.payload.step] = "running"; current = e.payload.step; currentTitle = e.payload.title; currentTeam = e.payload.team; }
      if (e.type === "step.completed") { st[e.payload.step] = "done"; results[e.payload.step] = e.payload.result; }
      if (e.type === "run.completed" || e.type === "run.failed") finished = e;
    }
    return { st, results, finished, current, currentTitle, currentTeam, steps, name, kind };
  }, [events, runId]);
}

export function Hotbar({ steps, view, running, busy, canRun, onRun, onDetails }: {
  steps: Step[]; view: RunView; running: boolean; busy: boolean; canRun: boolean; onRun: () => void; onDetails: () => void;
}) {
  const isHero = !view.kind || view.kind === "hero";
  const doneCount = steps.filter((s) => view.st[s.key ?? ""] === "done").length;
  let caption = "Lead-to-cash · Sales → Finance → Customer Success, run by agents";
  if (!isHero && view.name) caption = `${view.kind === "skill" ? "📘 " : ""}${view.name}`;
  if (running && view.currentTitle) caption = `${cap(view.currentTeam)} agent · ${view.currentTitle}`;
  if (view.finished) caption = view.finished.type === "run.failed" ? `Run failed: ${view.finished.payload.error}`
    : `✔ ${view.finished.payload.result}${view.finished.payload.hours_saved ? ` · ~${view.finished.payload.hours_saved}h of manual work saved` : ""}`;
  return (
    <div className="hotbar-wrap">
      <div className={`caption block ${view.finished?.type === "run.failed" ? "caption-bad" : view.finished ? "caption-ok" : ""}`}>
        {running && <span className="spinner" />}{caption}
        {(running || view.finished) && <button className="linkbtn" onClick={onDetails}>details</button>}
      </div>
      <div className="hotbar">
        <button className="btn btn-go btn-run" disabled={!canRun || busy || running} onClick={onRun}>
          {running ? "Running…" : view.finished && isHero ? "▶ Run again" : "▶ Run lead-to-cash"}
        </button>
        <div className="slots" role="list">
          {steps.map((s, i) => {
            const st = view.st[s.key ?? ""];
            return (
              <div key={s.key} role="listitem" className={`slot ${st ?? ""} ${s.tool === "approval" ? "slot-gate" : ""}`}
                   title={`${i + 1}. ${s.title} (${s.team}${s.to ? ` → ${s.to}` : ""})`}>
                <span>{(isHero && STEP_ICON[s.key ?? ""]) || stepIcon(s)}</span>
                <small>{i + 1}</small>
              </div>
            );
          })}
        </div>
        <div className="progress"><b>{doneCount}</b>/{steps.length}</div>
      </div>
    </div>
  );
}

const cap = (s?: string) => (s ? s[0].toUpperCase() + s.slice(1) : "");

/* ---------- approval ---------- */

export function ApprovalCard({ a, onDecide }: { a: Approval; onDecide: (approved: boolean) => void }) {
  const inv = a.payload?.invoice;
  return (
    <div className="approval block">
      <div className="approval-icon">✋</div>
      <div className="grow">
        <div className="eyebrow">{cap(a.team_key)} agent needs your OK</div>
        <b>{a.title}</b>
        {inv && <div className="muted">Stripe invoice {inv.invoice_id} · {inv.mode === "mock" ? "demo" : "test mode"}</div>}
        {a.payload?.rows && <div className="muted">To: {a.payload.rows.map((r: any) => r.label).join(", ")}{a.payload.steps ? ` · ${a.payload.steps.join(" → ")}` : ""}</div>}
      </div>
      <button className="btn btn-bad" onClick={() => onDecide(false)}>Reject</button>
      <button className="btn btn-go" onClick={() => onDecide(true)}>Approve & send</button>
    </div>
  );
}

/* ---------- toasts ---------- */

export type Toast = { id: string; text: string; kind?: "ok" | "info" | "bad" };
export function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => <div key={t.id} className={`toast block toast-${t.kind ?? "info"}`}>{t.text}</div>)}
    </div>
  );
}

/* ---------- run details + activity ---------- */

export function SidePanel({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <aside className="drawer block">
      <header className="drawer-head">
        <div className="grow"><div className="eyebrow">OrgTwin</div><h2>{title}</h2></div>
        <button className="x" onClick={onClose} aria-label="Close">✕</button>
      </header>
      {children}
    </aside>
  );
}

export function RunDetails({ view }: { view: RunView }) {
  const lead = view.results.pick_lead, score = view.results.score_lead, q = view.results.qualify;
  const inv = view.results.send_invoice?.invoice ?? view.results.draft_invoice;
  if (!lead) return <p className="muted">Start a run to see the details here.</p>;
  return (
    <>
      <section>
        <h3>Lead from CRM</h3>
        <p><b>{lead.name}</b> · {lead.company}</p>
        <p className="muted">{lead.notes}</p>
        <p className="muted">Deal value ${Number(lead.deal_value).toLocaleString()}</p>
      </section>
      {score?.output && (
        <section>
          <h3>Score · computed in a sandbox</h3>
          <div className="score"><b>{score.output.score}</b><span>/100 · tier {score.output.tier}</span></div>
          <p className="muted">{score.output.reasons?.join(" · ")}</p>
          <p className="sandbox-note">{score.isolation?.type === "docker"
            ? `🔒 Throwaway container · no network · read-only · ${score.isolation.memory} RAM · no secrets · ${score.duration_ms} ms`
            : "Local runner (development mode, not isolated)"}</p>
        </section>
      )}
      {q && (
        <section>
          <h3>Sales agent&apos;s take</h3>
          <p>{q.summary}</p>
          <p className="muted">Model: {q.llm?.mocked ? "demo generator" : q.llm?.model}</p>
        </section>
      )}
      {inv && (
        <section>
          <h3>Invoice</h3>
          <p>{inv.invoice_id} · {inv.status} · ${(inv.amount_due / 100).toLocaleString()}</p>
          {inv.hosted_invoice_url && <a href={inv.hosted_invoice_url} target="_blank" rel="noreferrer">Open Stripe invoice ↗</a>}
        </section>
      )}
    </>
  );
}

export function Activity({ outbox, events }: { outbox: Outbox; events: LiveEvent[] }) {
  return (
    <>
      <section>
        <h3>Emails sent ({outbox.emails.length})</h3>
        {outbox.emails.length === 0 && <p className="muted">None yet.</p>}
        {outbox.emails.map((m, i) => (
          <details key={i} className="mail">
            <summary><b>{m.subject}</b><span className="muted"> → {m.to}{m.mode === "mock" ? " · demo" : ""}</span></summary>
            <pre>{m.body}</pre>
          </details>
        ))}
      </section>
      <section>
        <h3>Slack</h3>
        {outbox.slack.length === 0 && <p className="muted">None yet.</p>}
        {outbox.slack.map((m, i) => <p key={i} className="slackmsg">{m.text}</p>)}
      </section>
      <section>
        <h3>Audit log</h3>
        <ul className="audit">
          {[...events].reverse().slice(0, 80).map((e) => (
            <li key={e.id}><time>{new Date(e.ts).toLocaleTimeString()}</time><code>{e.type}</code>
              <span>{e.payload?.title ?? e.payload?.result ?? e.payload?.company ?? ""}</span></li>
          ))}
        </ul>
      </section>
    </>
  );
}

/* ---------- org skill library ---------- */

export function SkillLibrary({ org, workflows, onRun, onOpenTeam }: {
  org: Org; workflows: Workflow[]; onRun: (id: number) => void; onOpenTeam: (team: string, role: string) => void;
}) {
  const skills = workflows.filter((w) => w.kind === "skill");
  if (!skills.length) {
    return (
      <section>
        <p><b>No skills recorded yet.</b></p>
        <p className="muted">Open a department, pick an agent and press <b>🎥 Record a skill</b>. Do the task once while
          talking it through; OrgTwin turns it into a workflow that agent can run.</p>
      </section>
    );
  }
  return (
    <>
      {org.teams.filter((t) => skills.some((s) => s.team_key === t.key)).map((t, i) => (
        <section key={t.key} style={{ ["--team" as any]: teamColor(t.key, org.teams.indexOf(t)) }}>
          <h3><span className="sw sw-inline" /> {t.name}</h3>
          {skills.filter((s) => s.team_key === t.key).map((s) => {
            const role = t.roles.find((r) => r.key === s.role_key);
            return (
              <div key={s.id} className="skill">
                <div className="grow">
                  <b>📘 {s.name}</b>
                  <div className="muted">{role?.name ?? s.role_key} · {s.steps.length} steps · learned from {s.meta?.how ?? "a recording"}</div>
                  <div className="mini-steps">{s.steps.map((st, k) => <span key={k} title={st.title}>{stepIcon(st)}</span>)}</div>
                </div>
                <div className="col-btns">
                  <button className="btn btn-go" disabled={!s.executable} onClick={() => onRun(s.id)}>▶ Run</button>
                  <button className="linkbtn" onClick={() => onOpenTeam(s.team_key, s.role_key)}>open</button>
                </div>
              </div>
            );
          })}
        </section>
      ))}
    </>
  );
}
