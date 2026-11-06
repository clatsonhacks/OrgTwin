"use client";
import { useCallback, useEffect, useState } from "react";
import { api, post } from "@/lib/api";
import { canAct, useMe } from "@/lib/access";
import type { Org, Workflow } from "@/lib/types";
import { teamColor } from "@/components/world/layout";

export type WorkRequest = {
  id: number; text: string; from_name: string; from_team: string; workflow_id: number | null; team_key: string;
  run_id: number | null; status: "routing" | "queued" | "running" | "done" | "failed" | "no_skill"; why: string; result: string;
  notified: { channel: string; mode?: string; to: string[]; error?: string }[]; created_at: string;
};

const STATUS: Record<WorkRequest["status"], [string, string]> = {
  routing: ["finding a skill", ""], queued: ["waiting its turn", "tag-warn"], running: ["agents working", "tag-flow"],
  done: ["✓ done", "tag-go"], failed: ["✕ failed", "tag-bad"], no_skill: ["no skill yet", "tag-warn"],
};

/** "I need an analysis of …": OrgTwin finds the skill in whichever department has it, runs it, and pings its owners. */
export default function RequestsPanel({ org, workflows, onError, version, onWatch }: {
  org: Org; workflows: Workflow[]; onError: (m: string) => void; version: number; onWatch?: (runId: number) => void;
}) {
  const me = useMe();
  const [text, setText] = useState("");
  const [skill, setSkill] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [list, setList] = useState<WorkRequest[]>([]);
  const reload = useCallback(() => { api<WorkRequest[]>("/api/requests").then(setList).catch(() => {}); }, []);
  useEffect(() => { reload(); }, [reload, version]);
  const skills = workflows.filter((w) => w.kind === "skill" && w.executable);
  const team = (k: string) => org.teams.find((t) => t.key === k)?.name ?? k;
  const send = async () => {
    setBusy(true);
    try {
      const r = await post<WorkRequest>("/api/requests", { text, ...(skill ? { workflow_id: Number(skill) } : {}) });
      setText(""); setSkill(""); reload();
      if (r.status === "no_skill") onError(`No skill can do that yet — ${team(r.team_key)} was asked to make one.`);
    } catch (e: any) { onError(e.message); } finally { setBusy(false); }
  };
  const examples = ["I need an analysis of last month's support tickets", "Review the open pull requests and summarise the risky ones",
                    "Find 5 competitors of ours and compare their pricing"];
  return (
    <>
      <section>
        <p className="muted">Ask for anything. OrgTwin finds the department whose skill can do it, runs that skill with your
          request, and tells the people who own it in Slack or Discord. You see the answer here.</p>
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="I need an analysis of …" aria-label="Your request" />
        <div className="chips-row">{examples.map((x) => <button key={x} className="chip" onClick={() => setText(x)}>{x}</button>)}</div>
        <div className="row">
          <select value={skill} onChange={(e) => setSkill(e.target.value)} aria-label="Which skill" className="grow">
            <option value="">✨ Let OrgTwin pick the skill</option>
            {skills.map((w) => <option key={w.id} value={w.id}>📘 {w.name} · {team(w.team_key)}</option>)}
          </select>
          <button className="btn btn-go" disabled={busy || text.trim().length < 3 || !canAct(me)} onClick={send}>
            {busy ? "Sending…" : "📥 Send"}
          </button>
        </div>
        {!canAct(me) && <p className="muted">Sign in with your invite link (👥 People) to send requests.</p>}
      </section>
      <section>
        <h3>Recent requests</h3>
        {!list.length && <p className="muted">Nothing asked yet.</p>}
        <div className="requests">
          {list.map((r) => {
            const wf = workflows.find((w) => w.id === r.workflow_id);
            const [label, cls] = STATUS[r.status] ?? [r.status, ""];
            const i = org.teams.findIndex((t) => t.key === r.team_key);
            return (
              <div key={r.id} className="req" style={{ ["--team" as any]: i >= 0 ? teamColor(r.team_key, i) : "#999" }}>
                <div className="row tight">
                  <b className="grow">“{r.text}”</b>
                  <span className={`tag ${cls}`}>{label}</span>
                </div>
                <div className="muted">
                  {r.from_name}{r.from_team ? ` (${team(r.from_team)})` : ""} → {r.team_key ? <b className="req-team">{team(r.team_key)}</b> : "?"}
                  {wf && <> · 📘 {wf.name}</>}{r.why && <> · {r.why}</>}
                </div>
                {r.result && <p className={`req-result ${r.status === "failed" ? "bad-line" : ""}`}>{r.result.slice(0, 1200)}</p>}
                {r.notified.length > 0 && (
                  <div className="muted">🔔 {r.notified.map((n) => `${n.channel === "slack" ? "💬 Slack" : "🎮 Discord"}${n.mode === "mock" ? " (demo)" : ""}${n.error ? " ✕" : ""} → ${n.to.join(", ")}`).join(" · ")}</div>
                )}
                {r.run_id && r.status === "running" && onWatch && <button className="linkbtn" onClick={() => onWatch(r.run_id!)}>watch it run</button>}
              </div>
            );
          })}
        </div>
      </section>
    </>
  );
}
