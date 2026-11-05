"use client";
import { useState } from "react";
import { post } from "@/lib/api";
import { loadConnectors, useConnectors, type Connector } from "@/lib/connectors";

const CATEGORY: Record<string, string> = {
  data: "Data", chat: "Messaging", docs: "Docs", crm: "CRM", billing: "Billing", developer: "Developer",
};

/** Every connector, live or demo, with what to set and a one-click connection test. */
export default function ConnectorsPanel({ onError }: { onError: (m: string) => void }) {
  const list = useConnectors();
  const live = list.filter((c) => c.mode === "live").length;
  const groups = Array.from(new Set(list.map((c) => c.category)));
  return (
    <>
      <section>
        <p className="muted">
          Agents use these in workflows. <b>{live}</b> of {list.length} are live; the rest run in <b>demo mode</b> and only
          record what they would have sent. To go live, set the env vars in <code>.env</code> and restart the API.
        </p>
      </section>
      {groups.map((g) => (
        <section key={g}>
          <h3>{CATEGORY[g] ?? g}</h3>
          <div className="conns">{list.filter((c) => c.category === g).map((c) => <ConnectorCard key={c.key} c={c} onError={onError} />)}</div>
        </section>
      ))}
    </>
  );
}

function ConnectorCard({ c, onError }: { c: Connector; onError: (m: string) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState("");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const test = async () => {
    setBusy(true); setResult(null);
    try {
      setResult(await post<{ ok: boolean; message: string }>(`/api/connectors/${c.key}/test`, { url }));
      loadConnectors(true).catch(() => {});
    } catch (e: any) { setResult({ ok: false, message: e.message }); onError(e.message); } finally { setBusy(false); }
  };
  return (
    <div className={`conn ${open ? "open" : ""}`}>
      <button className="conn-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="conn-icon" aria-hidden>{c.icon}</span>
        <span className="grow"><b>{c.name}</b>
          <span className="muted">{c.actions.length ? c.actions.map((a) => a.label).join(" · ") : "built into lead-to-cash"}</span>
        </span>
        <span className={`tag ${c.mode === "live" ? "tag-go" : ""}`}>{c.mode === "live" ? "live" : "demo"}</span>
      </button>
      {open && (
        <div className="conn-body">
          {c.setup && <p>{c.setup}</p>}
          <p className="muted">Env: {c.env.map((e) => <code key={e}>{e}</code>).reduce<React.ReactNode[]>((acc, x, i) => (i ? [...acc, " ", x] : [x]), [])}</p>
          {c.actions.map((a) => <p key={a.key} className="muted">• <b>{a.label}</b> — {a.doc}{a.external ? " (asks for approval)" : ""}</p>)}
          {!c.core && (
            <div className="row">
              {c.key === "webhook" && <input className="grow" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.zapier.com/…" aria-label="Webhook URL" />}
              <button className="btn" disabled={busy || (c.key === "webhook" && !url.startsWith("http"))} onClick={test}>
                {busy ? "Testing…" : c.mode === "live" ? "Send test" : "Check"}
              </button>
            </div>
          )}
          {result && <p className={result.ok ? "ok-line" : "bad-line"}>{result.ok ? "✓" : "✕"} {result.message}</p>}
        </div>
      )}
    </div>
  );
}
