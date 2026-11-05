"use client";
import { ACTIONS, TOOL_NAME, stepIcon } from "@/lib/api";
import type { Step } from "@/lib/types";

const RUNNABLE = Object.keys(ACTIONS);

/** Plain-language summary of what a step will do. */
export function describe(s: Step): string {
  const p = s.params || {};
  const each = s.per_row ? "for each row: " : "";
  if (s.tool === "sheets" && s.action === "read_rows") {
    const w = Object.entries(p.where || {}).map(([k, v]) => `${k} = ${v}`).join(", ");
    return `Read up to ${p.limit ?? 10} rows${w ? ` where ${w}` : ""}`;
  }
  if (s.tool === "sheets" && s.action === "update_row") return `${each}set ${p.column} to “${p.value}”`;
  if (s.tool === "email") return `${each}email ${p.to || "?"} — “${p.subject}”`;
  if (s.tool === "llm") return `${each}AI writes: ${p.instruction}`;
  if (s.tool === "approval") return `Ask a person: ${p.title}`;
  if (s.tool === "slack") return `Post to Slack: ${p.text}`;
  return s.description || TOOL_NAME[s.tool] || s.tool;
}

export function StepList({ steps }: { steps: Step[] }) {
  return (
    <ol className="flow">
      {steps.map((s, i) => (
        <li key={i}>
          <span className="slot slot-sm">{stepIcon(s)}</span>
          <div className="grow">
            <div>{s.title} {s.needs_approval && <span className="tag tag-warn">approval</span>} {s.per_row && <span className="tag">each row</span>}</div>
            <div className="muted">{describe(s)}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function StepEditor({ steps, onChange }: { steps: Step[]; onChange: (s: Step[]) => void }) {
  const set = (i: number, patch: Partial<Step>) => onChange(steps.map((s, k) => (k === i ? { ...s, ...patch } : s)));
  const setP = (i: number, key: string, value: any) => set(i, { params: { ...(steps[i].params || {}), [key]: value } });
  const move = (i: number, d: number) => {
    const j = i + d; if (j < 0 || j >= steps.length) return;
    const n = [...steps]; [n[i], n[j]] = [n[j], n[i]]; onChange(n);
  };
  return (
    <ol className="flow flow-edit">
      {steps.map((s, i) => {
        const p = s.params || {};
        const whereKey = Object.keys(p.where || {})[0] ?? "";
        return (
          <li key={i}>
            <span className="slot slot-sm">{stepIcon(s)}</span>
            <div className="grow stepbox">
              <div className="row tight">
                <input className="grow" value={s.title} onChange={(e) => set(i, { title: e.target.value })} aria-label="Step title" />
                <button className="iconbtn" onClick={() => move(i, -1)} aria-label="Move up">↑</button>
                <button className="iconbtn" onClick={() => move(i, 1)} aria-label="Move down">↓</button>
                <button className="iconbtn" onClick={() => onChange(steps.filter((_, k) => k !== i))} aria-label="Remove">✕</button>
              </div>
              <div className="row tight">
                <select value={s.tool} onChange={(e) => set(i, { tool: e.target.value, action: ACTIONS[e.target.value]?.[0]?.key, params: {} })}>
                  {[...RUNNABLE, ...(RUNNABLE.includes(s.tool) ? [] : [s.tool])].map((t) => <option key={t} value={t}>{TOOL_NAME[t] ?? t}</option>)}
                </select>
                {ACTIONS[s.tool]?.length > 1 && (
                  <select value={s.action} onChange={(e) => set(i, { action: e.target.value, params: {} })}>
                    {ACTIONS[s.tool].map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
                  </select>
                )}
                {!(s.tool === "sheets" && s.action === "read_rows") && (
                  <label className="check"><input type="checkbox" checked={!!s.per_row} onChange={(e) => set(i, { per_row: e.target.checked })} /> each row</label>
                )}
                <label className="check"><input type="checkbox" checked={!!s.needs_approval} onChange={(e) => set(i, { needs_approval: e.target.checked })} /> approval</label>
              </div>
              {s.tool === "sheets" && s.action === "read_rows" && (
                <div className="params">
                  <label>where <input value={whereKey} placeholder="column" onChange={(e) => setP(i, "where", e.target.value ? { [e.target.value]: p.where?.[whereKey] ?? "" } : {})} /></label>
                  <label>= <input value={p.where?.[whereKey] ?? ""} placeholder="value" disabled={!whereKey} onChange={(e) => setP(i, "where", { [whereKey]: e.target.value })} /></label>
                  <label>max <input type="number" min={1} max={100} value={p.limit ?? 10} onChange={(e) => setP(i, "limit", Number(e.target.value))} /></label>
                </div>
              )}
              {s.tool === "sheets" && s.action === "update_row" && (
                <div className="params">
                  <label>set <input value={p.column ?? ""} onChange={(e) => setP(i, "column", e.target.value)} /></label>
                  <label>to <input value={p.value ?? ""} onChange={(e) => setP(i, "value", e.target.value)} /></label>
                </div>
              )}
              {s.tool === "email" && (
                <div className="params col">
                  <label>to <input value={p.to ?? ""} onChange={(e) => setP(i, "to", e.target.value)} /></label>
                  <label>subject <input value={p.subject ?? ""} onChange={(e) => setP(i, "subject", e.target.value)} /></label>
                  <label>AI writes <textarea rows={2} value={p.instruction ?? ""} onChange={(e) => setP(i, "instruction", e.target.value)} /></label>
                </div>
              )}
              {s.tool === "llm" && (
                <div className="params col">
                  <label>AI writes <textarea rows={2} value={p.instruction ?? ""} onChange={(e) => setP(i, "instruction", e.target.value)} /></label>
                  <label>save as <input value={p.save_as ?? ""} onChange={(e) => setP(i, "save_as", e.target.value)} /></label>
                </div>
              )}
              {s.tool === "approval" && (
                <div className="params col"><label>ask <input value={p.title ?? ""} onChange={(e) => setP(i, "title", e.target.value)} /></label></div>
              )}
              {s.tool === "slack" && (
                <div className="params col"><label>text <input value={p.text ?? ""} onChange={(e) => setP(i, "text", e.target.value)} /></label></div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function newStep(): Step {
  return { title: "New step", tool: "llm", action: "write", params: { instruction: "", save_as: "note" }, per_row: false, needs_approval: false };
}
