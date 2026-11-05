"use client";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, del, post, put } from "@/lib/api";
import { loadConnectors, useConnectors, type Connector, type ConnectorSetup, type SetupField } from "@/lib/connectors";
import { connectWithPopup, openHelp } from "@/lib/oauth";

const CATEGORY: Record<string, string> = {
  data: "Data", chat: "Messaging", docs: "Docs", crm: "CRM", billing: "Billing", developer: "Developer", research: "Research",
};
const SIGNIN_NAME = { google: "Google", stripe: "Stripe" } as const;

/** Every connector: one Connect button each. Google and Stripe open a sign-in popup; the rest a short form. */
export default function ConnectorsPanel({ onError, onToast }: { onError: (m: string) => void; onToast?: (m: string) => void }) {
  const list = useConnectors();
  const [connecting, setConnecting] = useState<Connector | null>(null);
  const live = list.filter((c) => c.mode === "live").length;
  const groups = Array.from(new Set(list.map((c) => c.category)));
  return (
    <>
      <section>
        <p className="muted">
          Agents use these in workflows. <b>{live}</b> of {list.length} are connected; the rest run in <b>demo mode</b> and
          only record what they would have sent. Click <b>Connect</b> to link your own account.
        </p>
      </section>
      {groups.map((g) => (
        <section key={g}>
          <h3>{CATEGORY[g] ?? g}</h3>
          <div className="conns">
            {list.filter((c) => c.category === g).map((c) => (
              <ConnectorCard key={c.key} c={c} onError={onError} onConnect={() => setConnecting(c)} />
            ))}
          </div>
        </section>
      ))}
      {connecting && (
        <ConnectDialog c={connecting} onClose={() => { setConnecting(null); loadConnectors(true).catch(() => {}); }}
                       onError={onError} onToast={onToast} />
      )}
    </>
  );
}

function ConnectorCard({ c, onError, onConnect }: { c: Connector; onError: (m: string) => void; onConnect: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState("");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const isLive = c.mode === "live";
  const test = async () => {
    setBusy(true); setResult(null);
    try {
      setResult(await post<{ ok: boolean; message: string }>(`/api/connectors/${c.key}/test`, { url }));
      loadConnectors(true).catch(() => {});
    } catch (e: any) { setResult({ ok: false, message: e.message }); onError(e.message); } finally { setBusy(false); }
  };
  return (
    <div className={`conn ${open ? "open" : ""}`}>
      <div className="conn-row">
        <button className="conn-head" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="conn-icon" aria-hidden>{c.icon}</span>
          <span className="grow"><b>{c.name}</b>
            <span className="muted">
              {isLive && c.account ? <>Connected as {c.account}</> :
               c.actions.length ? c.actions.map((a) => a.label).join(" · ") : "built into lead-to-cash"}
            </span>
          </span>
        </button>
        {c.connectable && (
          isLive
            ? <button className="btn btn-sm btn-done" onClick={onConnect} aria-label={`Manage ${c.name}`}>✓ Connected</button>
            : <button className="btn btn-sm btn-go" onClick={onConnect}>Connect</button>
        )}
        {!c.connectable && <span className={`tag ${isLive ? "tag-go" : ""}`}>{isLive ? "live" : "demo"}</span>}
      </div>
      {open && (
        <div className="conn-body">
          {c.setup && <p>{c.setup}</p>}
          {c.actions.map((a) => <p key={a.key} className="muted">• <b>{a.label}</b> — {a.doc}{a.external ? " (asks for approval)" : ""}</p>)}
          {!c.core && c.key !== "web" && (
            <div className="row">
              {c.key === "webhook" && <input className="grow" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.zapier.com/…" aria-label="Webhook URL" />}
              <button className="btn" disabled={busy || (c.key === "webhook" && !url.startsWith("http"))} onClick={test}>
                {busy ? "Testing…" : isLive ? "Send test" : "Check"}
              </button>
            </div>
          )}
          {result && <p className={result.ok ? "ok-line" : "bad-line"}>{result.ok ? "✓" : "✕"} {result.message}</p>}
        </div>
      )}
    </div>
  );
}

/* ---------- the Connect popup ---------- */

function ConnectDialog({ c, onClose, onError, onToast }: {
  c: Connector; onClose: () => void; onError: (m: string) => void; onToast?: (m: string) => void;
}) {
  const [st, setSt] = useState<ConnectorSetup | null>(null);
  const [vals, setVals] = useState<Record<string, string | boolean>>({});
  const [manual, setManual] = useState(false);
  const [busy, setBusy] = useState<"" | "signin" | "save" | "sheet" | "off">("");
  const [msg, setMsg] = useState<{ ok: boolean; message: string } | null>(null);
  const [sheetLink, setSheetLink] = useState("");

  const reload = async () => {
    const s = await api<ConnectorSetup>(`/api/setup/connectors/${c.key}`);
    setSt(s);
    setVals(Object.fromEntries(s.fields.filter((f) => f.type !== "secret" && f.type !== "textarea").map((f) => [f.env, f.value])));
    return s;
  };
  useEffect(() => { reload().catch((e) => onError(e.message)); }, [c.key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const signin = async () => {
    if (!st?.signin) return;
    setBusy("signin"); setMsg(null);
    const r = await connectWithPopup(st.signin);
    const s = await reload().catch(() => null);
    setBusy("");
    if (r.message) setMsg(r);
    if (s?.account) { loadConnectors(true).catch(() => {}); if (!s.needs_sheet) onToast?.(`🔌 ${c.name} connected`); }
  };
  const save = async (values: Record<string, string | boolean>) => {
    setBusy("save"); setMsg(null);
    try {
      const r = await put<{ ok: boolean; message: string; setup: ConnectorSetup }>(`/api/setup/connectors/${c.key}`, { values });
      setMsg(r); setSt(r.setup); loadConnectors(true).catch(() => {});
      if (r.ok) onToast?.(`🔌 ${c.name} connected`);
    } catch (e: any) { setMsg({ ok: false, message: e.message }); } finally { setBusy(""); }
  };
  const sampleSheet = async () => {
    setBusy("sheet"); setMsg(null);
    try {
      const r = await post<{ url: string; setup: ConnectorSetup }>("/api/oauth/google/sample-sheet");
      setSt(r.setup); loadConnectors(true).catch(() => {});
      setMsg({ ok: true, message: "Created “OrgTwin CRM (sample leads)” in your Google Drive and connected it." });
      onToast?.("📊 Google Sheets connected");
    } catch (e: any) { setMsg({ ok: false, message: e.message }); } finally { setBusy(""); }
  };
  const disconnect = async () => {
    if (!window.confirm(`Disconnect ${c.name}? Agents go back to demo mode for it.`)) return;
    setBusy("off"); setMsg(null);
    try {
      const r = await del<{ setup: ConnectorSetup }>(`/api/setup/connectors/${c.key}`);
      setSt(r.setup); setManual(false); loadConnectors(true).catch(() => {});
      setMsg({ ok: true, message: "Disconnected." });
    } catch (e: any) { setMsg({ ok: false, message: e.message }); } finally { setBusy(""); }
  };

  const who = st?.signin ? SIGNIN_NAME[st.signin] : "";
  const signedIn = !!st?.account;
  const showSignin = !!st?.signin && st.signin_available && !manual;
  const isLive = c.mode === "live";

  return createPortal(
    <div className="modal-bg modal-fixed" role="dialog" aria-modal aria-label={`Connect ${c.name}`} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal block connect-modal">
        <header className="drawer-head">
          <div className="swatch connect-icon" aria-hidden>{c.icon}</div>
          <div className="grow">
            <div className="eyebrow">Connector</div>
            <h2>{signedIn || isLive ? c.name : `Connect ${c.name}`}</h2>
          </div>
          <button className="x" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="modal-body connect-body">
          {!st && <p className="muted">Loading…</p>}

          {st && showSignin && !signedIn && (
            <>
              <p className="lead">Sign in with your {who} account and click <b>Allow</b>. That&apos;s it — no keys to copy.</p>
              <ul className="perm-list">
                {c.key === "sheets" && <li>📊 Read and update the Google Sheet you choose</li>}
                {(c.key === "sheets" || c.key === "email") && <li>✉️ Send emails from your Gmail (after you approve each batch)</li>}
                {c.key === "stripe" && <li>🧾 Create and send invoices in your Stripe account (test mode only)</li>}
                <li>🔒 You can disconnect any time</li>
              </ul>
              <button className={`btn btn-big signin-btn signin-${st.signin}`} disabled={busy === "signin"} onClick={signin}>
                {busy === "signin" ? "Waiting for the sign-in window…" : st.signin === "google" ? <><GoogleMark /> Sign in with Google</> : <>Connect with Stripe</>}
              </button>
              {busy === "signin" && <p className="muted center-text">Finish in the popup. Nothing opened? Allow popups for this site and click again.</p>}
              {st.fields.length > 0 && <button className="linkbtn" onClick={() => setManual(true)}>Use a key or password instead</button>}
            </>
          )}

          {st && st.signin && !st.signin_available && !signedIn && !manual && (
            <>
              <p className="lead">Fill in the details below to connect.</p>
              <p className="muted">One-click {who} sign-in isn&apos;t turned on for this server yet. The admin can enable it once
                (<code>{st.signin === "google" ? "GOOGLE_OAUTH_CLIENT_ID" : "STRIPE_CONNECT_CLIENT_ID"}</code> in <code>.env</code>), and then
                everyone just clicks “Sign in with {who}”.</p>
            </>
          )}

          {st && signedIn && (
            <div className="account-box">
              <span className="ok-dot" aria-hidden>✓</span>
              <div className="grow"><b>Connected</b><span className="muted">{st.account}</span></div>
              <button className="btn btn-sm" onClick={signin} disabled={!!busy || !st.signin_available}>Switch account</button>
            </div>
          )}

          {st && c.key === "sheets" && signedIn && (
            <div className="sheet-pick">
              <h3>{st.sheet_id ? "Your sheet" : "Pick a sheet"}</h3>
              {st.sheet_id && (
                <p><a href={`https://docs.google.com/spreadsheets/d/${st.sheet_id}/edit`} target="_blank" rel="noreferrer">Open the connected sheet ↗</a></p>
              )}
              <button className="btn btn-go btn-wide" disabled={!!busy} onClick={sampleSheet}>
                {busy === "sheet" ? "Creating…" : "✨ Create a sample CRM sheet for me"}
                <small>Makes a sheet with the right columns and 5 sample leads in your Drive</small>
              </button>
              <p className="muted or">or paste the link of a sheet you already have</p>
              <div className="row">
                <input className="grow" value={sheetLink} onChange={(e) => setSheetLink(e.target.value)}
                       placeholder="https://docs.google.com/spreadsheets/d/…" aria-label="Google Sheet link" />
                <button className="btn" disabled={!!busy || !sheetLink.trim()} onClick={() => save({ GOOGLE_SHEET_ID: sheetLink.trim() })}>Use it</button>
              </div>
              <p className="muted">Columns: id · name · company · email · notes · deal_value · status</p>
            </div>
          )}

          {st && (manual || !st.signin || !st.signin_available) && !(signedIn && st.signin) && st.fields.length > 0 && (
            <ManualForm st={st} vals={vals} setVals={setVals} busy={busy === "save"} onSave={() => save(vals)}
                        onBack={st.signin && st.signin_available ? () => setManual(false) : undefined} />
          )}

          {msg?.message && <p className={msg.ok ? "ok-line" : "bad-line"} role="status">{msg.ok ? "✓" : "✕"} {msg.message}</p>}

          {st && (signedIn || st.connected_in_app) && (
            <div className="row connect-foot">
              <span className="grow" />
              <button className="btn btn-bad btn-sm" onClick={disconnect} disabled={!!busy}>{busy === "off" ? "Disconnecting…" : "Disconnect"}</button>
              <button className="btn btn-sm" onClick={onClose}>Done</button>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function ManualForm({ st, vals, setVals, busy, onSave, onBack }: {
  st: ConnectorSetup; vals: Record<string, string | boolean>; setVals: (v: Record<string, string | boolean>) => void;
  busy: boolean; onSave: () => void; onBack?: () => void;
}) {
  const set = (k: string, v: string | boolean) => setVals({ ...vals, [k]: v });
  const missing = st.fields.some((f) => !f.optional && f.type !== "toggle" && !f.set && !String(vals[f.env] ?? "").trim());
  return (
    <form className="manual-form" onSubmit={(e) => { e.preventDefault(); onSave(); }}>
      {st.steps.length > 0 && (
        <ol className="how-steps">
          {st.steps.map((s) => <li key={s}>{s}</li>)}
        </ol>
      )}
      {st.link && <button type="button" className="btn btn-sm" onClick={() => openHelp(st.link)}>{st.link_label || "Open setup page"} ↗</button>}
      {st.fields.map((f) => <FieldInput key={f.env} f={f} value={vals[f.env]} onChange={(v) => set(f.env, v)} />)}
      <div className="row">
        {onBack && <button type="button" className="linkbtn" onClick={onBack}>← Sign in instead</button>}
        <span className="grow" />
        <button className="btn btn-go" disabled={busy || missing}>{busy ? "Connecting…" : "Save & connect"}</button>
      </div>
    </form>
  );
}

function FieldInput({ f, value, onChange }: { f: SetupField; value: string | boolean | undefined; onChange: (v: string | boolean) => void }) {
  const id = `f-${f.env}`;
  if (f.type === "toggle") {
    return (
      <label className="toggle-row" htmlFor={id}>
        <input id={id} type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {f.label}
      </label>
    );
  }
  const ph = f.set && f.hint ? `${f.hint} (saved — leave empty to keep)` : f.placeholder;
  return (
    <label className="field" htmlFor={id}>
      <span>{f.label}{f.optional && <small> · optional</small>}{f.source === "env" && <small> · set by admin</small>}</span>
      {f.type === "textarea"
        ? <textarea id={id} rows={4} value={String(value ?? "")} placeholder={ph} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
        : <input id={id} type={f.type === "secret" ? "password" : f.type === "number" ? "number" : "text"} autoComplete="off"
                 value={String(value ?? "")} placeholder={ph} onChange={(e) => onChange(e.target.value)} spellCheck={false} />}
    </label>
  );
}

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden style={{ verticalAlign: "-3px", marginRight: 6 }}>
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C36.9 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}
