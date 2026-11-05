"use client";
import { useEffect, useRef, useState } from "react";
import { api, post } from "@/lib/api";
import type { Org, Workflow } from "@/lib/types";
import { teamColor } from "@/components/world/layout";
import { WorkflowCard } from "./TeamDrawer";

type Idea = { name: string; goal: string };

/** Ready-made examples. The AI maps each one onto the departments this company actually has. */
export const SAMPLE_FLOWS: (Idea & { icon: string; chain: string })[] = [
  { icon: "💸", name: "Lead to invoice", chain: "Sales → Finance → Customer Success",
    goal: "Sales reads the Won leads in the sheet, Finance sends each one a Stripe invoice after a person approves and "
        + "marks the row Invoiced, then Customer Success emails each customer a welcome note and posts a summary to Slack." },
  { icon: "🔎", name: "Prospect research", chain: "Sales → Marketing → Sales",
    goal: "Sales searches the web for each New lead's company and writes a short brief, Marketing turns the brief into "
        + "a tailored intro email, then after a person approves Sales emails each lead and marks the row Contacted." },
  { icon: "↩️", name: "Refund request", chain: "Customer Success → Finance → Customer Success",
    goal: "Customer Success writes up the refund request with the reason, Finance asks a person to approve it and "
        + "writes the decision, then Customer Success emails the customer the outcome and posts it to Slack." },
  { icon: "⏰", name: "Overdue invoice chase", chain: "Finance → Sales",
    goal: "Finance reads the Overdue rows in the sheet, emails each customer a polite payment reminder after a person "
        + "approves and marks the row Reminded, then hands the list to Sales, who posts the accounts to call on Slack." },
  { icon: "📰", name: "Weekly market brief", chain: "Marketing → Sales → everyone",
    goal: "Marketing searches the web for this week's news about our industry and writes a 5-bullet brief, Sales adds "
        + "three talking points for customer calls, then the brief is posted to Slack." },
  { icon: "🛡️", name: "Security advisory", chain: "Engineering → Customer Success",
    goal: "Engineering searches the web for new vulnerabilities in our product's stack and writes a plain-English risk "
        + "summary, then Customer Success emails it to every New customer in the sheet after a person approves." },
];

export default function FlowBuilder({ org, workflows, onClose, onCreated, onRun, onChanged, onError }: {
  org: Org; workflows: Workflow[]; onClose: () => void; onCreated: (w: Workflow) => void;
  onRun: (id: number) => void; onChanged: () => void; onError: (m: string) => void;
}) {
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Workflow | null>(null);
  const [ideas, setIdeas] = useState<Idea[] | null>(null);
  const [ideasBusy, setIdeasBusy] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const flows = workflows.filter((w) => w.kind === "flow").reverse();
  const current = created ? workflows.find((w) => w.id === created.id) ?? created : null;

  useEffect(() => { box.current?.focus(); }, []);

  const pick = (g: string) => { setGoal(g); box.current?.focus(); };

  const design = async () => {
    setBusy(true);
    try {
      const w = await post<Workflow & { mocked?: boolean }>("/api/flows/design", { goal });
      setCreated(w); setGoal(""); onCreated(w);
      if (w.mocked) onError(`The AI planner was unavailable (${w.meta?.problem ?? "unknown"}), so a simple rule-based plan was used — check the steps.`);
    } catch (e: any) { onError(e.message); } finally { setBusy(false); }
  };

  const suggest = async () => {
    setIdeasBusy(true);
    try { setIdeas((await api<{ ideas: Idea[] }>("/api/flows/suggest")).ideas); }
    catch (e: any) { onError(e.message); } finally { setIdeasBusy(false); }
  };

  return (
    <aside className="drawer leftsheet block" aria-label="New cross-team workflow">
      <header className="drawer-head">
        <div className="flow-logo" aria-hidden>🔀</div>
        <div className="grow">
          <div className="eyebrow">Across departments</div>
          <h2>New workflow</h2>
        </div>
        <button className="x" onClick={onClose} aria-label="Close">✕</button>
      </header>

      <section className="builder">
        <label className="eyebrow" htmlFor="flow-goal">Describe the task</label>
        <p className="muted small">Say which team does what, in order. The AI splits it into steps, gives each step to a
          department&apos;s agent and adds the handoffs between them.</p>
        <div className="team-strip">
          {org.teams.map((t, i) => (
            <button key={t.key} className="team-badge as-btn" style={{ ["--team" as any]: teamColor(t.key, i) }}
                    onClick={() => pick(goal ? `${goal.trimEnd()} ${t.name} ` : `${t.name} `)} title={`Add “${t.name}” to the description`}>
              <i />{t.name}
            </button>
          ))}
        </div>
        <textarea id="flow-goal" ref={box} rows={4} value={goal} onChange={(e) => setGoal(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && goal.trim().length >= 3 && !busy) design(); }}
                  placeholder="e.g. Sales reads the Won deals, Finance invoices each one after approval, then Customer Success sends a welcome email" />
        <button className="btn btn-go btn-wide" disabled={busy || goal.trim().length < 3} onClick={design}>
          {busy ? <><span className="spinner" /> Designing — giving each step to a team…</> : "✦ Design with AI"}
        </button>
      </section>

      {current && (
        <section className="flow-created">
          <div className="eyebrow">✔ Created</div>
          <WorkflowCard wf={current} teams={org.teams} defaultOpen onSaved={onChanged} onError={onError} onRun={() => onRun(current.id)} />
        </section>
      )}

      <section>
        <h3>Sample workflows</h3>
        <p className="muted small">Click one to use it as a starting point, then edit the text.</p>
        <div className="samples">
          {SAMPLE_FLOWS.map((s) => (
            <button key={s.name} className="sample" onClick={() => pick(s.goal)}>
              <span className="sample-icon">{s.icon}</span>
              <span className="grow"><b>{s.name}</b><small>{s.chain}</small></span>
            </button>
          ))}
        </div>
        <button className="btn btn-wide" disabled={ideasBusy} onClick={suggest}>
          {ideasBusy ? <><span className="spinner" /> Thinking about {org.company}…</> : `✨ Ideas for ${org.company}`}
        </button>
        {ideas && (
          <div className="samples">
            {ideas.map((s) => (
              <button key={s.name + s.goal} className="sample sample-ai" onClick={() => pick(s.goal)}>
                <span className="sample-icon">✨</span>
                <span className="grow"><b>{s.name}</b><small>{s.goal}</small></span>
              </button>
            ))}
          </div>
        )}
      </section>

      {flows.filter((w) => w.id !== current?.id).length > 0 && (
        <section>
          <h3>Your cross-team workflows</h3>
          {flows.filter((w) => w.id !== current?.id).map((w) => (
            <WorkflowCard key={w.id} wf={w} teams={org.teams} defaultOpen={false} onSaved={onChanged} onError={onError} onRun={() => onRun(w.id)} />
          ))}
        </section>
      )}
    </aside>
  );
}
