"use client";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, put } from "@/lib/api";
import type { Workflow } from "@/lib/types";

type Catalog = {
  connectors: { id: string; name: string; icon: string; mode?: string; actions: { key: string; label: string; external: boolean }[] }[];
  mcp: { id: string; name: string; icon: string; error: string; tools: { id: string; name: string; description: string; read_only: boolean }[] }[];
  always: string[];
};

/** Short labels for a skill's granted tools. */
export function toolChips(tools: string[] | undefined): string[] {
  return (tools ?? []).map((t) => (t.startsWith("mcp:") ? `🧩 ${t.slice(4).replace("/", " · ")}` : t));
}

/** Pick what a skill may use: connectors (GitHub, Notion, Slack…) and MCP servers / single MCP tools. */
export default function SkillTools({ wf, onClose, onSaved, onError }: {
  wf: Workflow; onClose: () => void; onSaved: (w: Workflow) => void; onError: (m: string) => void;
}) {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set(wf.meta?.tools ?? []));
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<Catalog>("/api/tools").then(setCat).catch((e) => onError(e.message)); }, [onError]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const flip = (id: string) => { const n = new Set(sel); if (n.has(id)) n.delete(id); else n.add(id); setSel(n); };
  const flipServer = (srv: Catalog["mcp"][number]) => {
    const n = new Set(sel);
    if (n.has(srv.id)) n.delete(srv.id); else { n.add(srv.id); srv.tools.forEach((t) => n.delete(t.id)); }
    setSel(n);
  };
  const save = async () => {
    setBusy(true);
    try { onSaved(await put<Workflow>(`/api/workflows/${wf.id}/tools`, { tools: Array.from(sel) })); onClose(); }
    catch (e: any) { onError(e.message); } finally { setBusy(false); }
  };
  const used = new Set(wf.steps.map((s) => s.tool));
  const missing = Array.from(used).filter((t) => sel.size && !(cat?.always ?? []).includes(t) && !sel.has(t));
  const hasAgent = used.has("agent");

  return createPortal(
    <div className="modal-bg modal-fixed" role="dialog" aria-modal aria-label={`Tools for ${wf.name}`}
         onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal block connect-modal tools-modal">
        <header className="drawer-head">
          <div className="swatch connect-icon" aria-hidden>🧰</div>
          <div className="grow"><div className="eyebrow">Tool access</div><h2>{wf.name}</h2></div>
          <button className="x" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="modal-body connect-body">
          <p className="muted">Tick what this skill may use. Its steps can only use ticked tools (rows, AI writing, approvals and
            handoffs always work). An <b>🧰 AI agent</b> step picks from these tools by itself to finish the job, and anything that
            sends or changes something asks a person first.</p>
          {!hasAgent && <p className="muted">Tip: add an “AI agent with tools” step (Edit → + Step) so the skill can use MCP tools on its own.</p>}
          {!cat && <p className="muted">Loading…</p>}
          {cat && (
            <>
              <h3>Apps</h3>
              <div className="pick-list tool-grid">
                {cat.connectors.map((c) => (
                  <label key={c.id} className={`pick tool-pick ${sel.has(c.id) ? "on" : ""}`}>
                    <input type="checkbox" checked={sel.has(c.id)} onChange={() => flip(c.id)} />
                    <span>{c.icon} {c.name}{c.mode === "mock" && <small> · demo</small>}</span>
                  </label>
                ))}
              </div>
              <h3>MCP servers</h3>
              {!cat.mcp.length && <p className="muted">None yet. An admin adds them under 🔌 Connectors → MCP servers.</p>}
              {cat.mcp.map((srv) => (
                <div key={srv.id} className="mcp-pick">
                  <label className={`pick tool-pick ${sel.has(srv.id) ? "on" : ""}`}>
                    <input type="checkbox" checked={sel.has(srv.id)} onChange={() => flipServer(srv)} />
                    <span>🧩 <b>{srv.name}</b> · all {srv.tools.length} tools{srv.error && <small className="bad-line"> · {srv.error}</small>}</span>
                  </label>
                  {!sel.has(srv.id) && srv.tools.length > 0 && (
                    <div className="mcp-tools">
                      {srv.tools.map((t) => (
                        <label key={t.id} className={`tool-one ${sel.has(t.id) ? "on" : ""}`} title={t.description}>
                          <input type="checkbox" checked={sel.has(t.id)} onChange={() => flip(t.id)} />
                          {t.name}{t.read_only ? " 👀" : " ✋"}
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              <p className="muted">👀 read-only · ✋ asks a person before it runs</p>
            </>
          )}
          {missing.length > 0 && <p className="bad-line">This skill already uses {missing.join(", ")} — tick it or that step will be blocked.</p>}
          <div className="row connect-foot">
            <button className="linkbtn" onClick={() => setSel(new Set())}>clear (no limit)</button>
            <span className="grow" />
            <button className="btn btn-sm" onClick={onClose}>Cancel</button>
            <button className="btn btn-go btn-sm" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save tools"}</button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
