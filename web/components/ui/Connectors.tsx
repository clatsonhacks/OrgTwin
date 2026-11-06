"use client";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, del, post, put } from "@/lib/api";
import { loadConnectors, useConnectors, type Connector, type ConnectorSetup, type SetupField, type SignIn } from "@/lib/connectors";
import { connectTelegram, connectWithPopup, openHelp } from "@/lib/oauth";
import { useMe } from "@/lib/access";

const CATEGORY: Record<string, string> = {
  data: "Data", chat: "Messaging", docs: "Docs", crm: "CRM", billing: "Billing", developer: "Developer", research: "Research",
};
const SIGNIN_NAME: Record<SignIn, string> = {
  google: "Google", stripe: "Stripe", notion: "Notion", slack: "Slack", discord: "Discord", hubspot: "HubSpot", telegram: "Telegram",
  github: "GitHub",
};
/** What the user is allowing, in plain words (shown before they sign in). */
const PERMS: Record<string, string[]> = {
  sheets: ["📊 Read and update the Google Sheet you choose", "✉️ Send emails from your Gmail (after you approve each batch)"],
  email: ["✉️ Send emails from your Gmail (after you approve each batch)", "📊 Also connects Google Sheets"],
  stripe: ["🧾 Create and send invoices in your Stripe account (test mode only)"],
  notion: ["📝 Add pages to the database you pick", "👀 Only the pages you share on the next screen"],
  slack: ["💬 Post messages in the channel you pick on the next screen"],
  discord: ["🎮 Post messages in the server channel you pick on the next screen"],
  hubspot: ["🧲 Create and update contacts in your HubSpot CRM"],
  telegram: ["✈️ The OrgTwin bot posts in the chat or group where you press Start"],
  github: ["🐙 Read pull requests and issues in your repos", "💬 Comment, review (never approve) and open PRs / issues when a skill asks"],
};

/** Every connector: one Connect button each. Google and Stripe open a sign-in popup; the rest a short form. */
export default function ConnectorsPanel({ onError, onToast }: { onError: (m: string) => void; onToast?: (m: string) => void }) {
  const list = useConnectors();
  const me = useMe();
  const mayConnect = !me?.people_mode || !!me?.admin;
  const [connecting, setConnecting] = useState<Connector | null>(null);
  const live = list.filter((c) => c.mode === "live").length;
  const groups = Array.from(new Set(list.map((c) => c.category)));
  return (
    <>
      <section>
        <p className="muted">
          Agents use these in workflows. <b>{live}</b> of {list.length} are connected; the rest run in <b>demo mode</b> and
          only record what they would have sent. {mayConnect ? <>Click <b>Connect</b> to link your own account.</> : <>Only an admin can connect apps.</>}
        </p>
      </section>
      {groups.map((g) => (
        <section key={g}>
          <h3>{CATEGORY[g] ?? g}</h3>
          <div className="conns">
            {list.filter((c) => c.category === g).map((c) => (
              <ConnectorCard key={c.key} c={c} onError={onError} onConnect={mayConnect ? () => setConnecting(c) : undefined} />
            ))}
          </div>
        </section>
      ))}
      <McpServers admin={mayConnect} onError={onError} onToast={onToast} />
      {connecting && (
        <ConnectDialog c={connecting} onClose={() => { setConnecting(null); loadConnectors(true).catch(() => {}); }}
                       onError={onError} onToast={onToast} />
      )}
    </>
  );
}

function ConnectorCard({ c, onError, onConnect }: { c: Connector; onError: (m: string) => void; onConnect?: () => void }) {
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
        {c.connectable && onConnect && (
          isLive
            ? <button className="btn btn-sm btn-done" onClick={onConnect} aria-label={`Manage ${c.name}`}>✓ Connected</button>
            : <button className="btn btn-sm btn-go" onClick={onConnect}>Connect</button>
        )}
        {(!c.connectable || !onConnect) && <span className={`tag ${isLive ? "tag-go" : ""}`}>{isLive ? "live" : "demo"}</span>}
      </div>
      {open && (
        <div className="conn-body">
          {c.setup && <p>{c.setup}</p>}
          {c.actions.map((a) => <p key={a.key} className="muted">• <b>{a.label}</b> — {a.doc}{a.external ? " (asks for approval)" : ""}</p>)}
          {!c.core && c.key !== "web" && (
            <div className="row">
              {c.key === "webhook" && <input className="grow" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.zapier.com/…" aria-label="Webhook URL" />}
              <button className="btn" disabled={!onConnect || busy || (c.key === "webhook" && !url.startsWith("http"))} onClick={test}>
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
  const [tg] = useState({ cancelled: false });
  useEffect(() => () => { tg.cancelled = true; }, [tg]);

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
  const telegram = async (where: "group" | "dm") => {
    tg.cancelled = false;
    setBusy("signin"); setMsg(null);
    const r = await connectTelegram(where, tg);
    await reload().catch(() => null);
    setBusy("");
    if (r.message) setMsg(r);
    if (r.ok) { loadConnectors(true).catch(() => {}); onToast?.(`🔌 ${c.name} connected`); }
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
    const google = st?.signin === "google" && st.account;
    if (!window.confirm(google ? `Disconnect Google (${st!.account})? Both Google Sheets and Gmail go back to demo mode.`
                               : `Disconnect ${c.name}? Agents go back to demo mode for it.`)) return;
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
              <p className="lead">{st.signin === "telegram"
                ? <>Tap the button, pick a chat in Telegram and press <b>Start</b>. That&apos;s it — no keys to copy.</>
                : <>Sign in with your {who} account and click <b>Allow</b>. That&apos;s it — no keys to copy.</>}</p>
              <ul className="perm-list">
                {(PERMS[c.key] ?? []).map((p) => <li key={p}>{p}</li>)}
                <li>🔒 You can disconnect any time</li>
              </ul>
              {st.signin === "telegram" ? (
                busy === "signin" ? (
                  <>
                    <p className="muted center-text">In Telegram, pick the chat and press <b>Start</b>. This window updates by itself…</p>
                    <button className="btn" onClick={() => { tg.cancelled = true; }}>Cancel</button>
                  </>
                ) : (
                  <div className="row tg-row">
                    <button className="btn btn-big signin-btn signin-telegram" onClick={() => telegram("group")}>Add to a Telegram group</button>
                    <button className="btn signin-btn" onClick={() => telegram("dm")}>…or message me directly</button>
                  </div>
                )
              ) : (
                <button className={`btn btn-big signin-btn signin-${st.signin}`} disabled={busy === "signin"} onClick={signin}>
                  {busy === "signin" ? "Waiting for the sign-in window…" : st.signin === "google" ? <><GoogleMark /> Sign in with Google</> : <>Connect with {who}</>}
                </button>
              )}
              {busy === "signin" && st.signin !== "telegram" && <p className="muted center-text">Finish in the popup. Nothing opened? Allow popups for this site and click again.</p>}
              {st.fields.length > 0 && <button className="linkbtn" onClick={() => setManual(true)}>Use a key or password instead</button>}
            </>
          )}

          {st && st.signin && !st.signin_available && !signedIn && !manual && (
            <>
              <p className="lead">Fill in the details below to connect.</p>
              <p className="muted">One-click {who} connect isn&apos;t turned on for this server yet. The admin can enable it once
                (<code>{st.signin_env}</code> in <code>.env</code>), and then everyone just clicks Connect and allows access.</p>
            </>
          )}

          {st && signedIn && (
            <div className="account-box">
              <span className="ok-dot" aria-hidden>✓</span>
              <div className="grow"><b>Connected</b><span className="muted">{st.account}</span></div>
              <button className="btn btn-sm" onClick={() => (st.signin === "telegram" ? telegram("group") : signin())}
                      disabled={!!busy || !st.signin_available}>{st.signin === "telegram" ? "Change chat" : "Switch account"}</button>
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

          {st && c.key === "github" && (signedIn || st.repo || st.needs_repo) && (
            <RepoPicker st={st} busy={!!busy} onPicked={(r) => { setSt(r.setup); setMsg(r); loadConnectors(true).catch(() => {}); if (r.ok) onToast?.("🐙 GitHub connected"); }}
                        onError={(m) => setMsg({ ok: false, message: m })} />
          )}

          {st && c.key === "notion" && signedIn && (
            <NotionPicker st={st} busy={!!busy} onPicked={(r) => { setSt(r.setup); setMsg(r); loadConnectors(true).catch(() => {}); if (r.ok) onToast?.("📝 Notion connected"); }}
                          onError={(m) => setMsg({ ok: false, message: m })} />
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

function RepoPicker({ st, busy, onPicked, onError }: {
  st: ConnectorSetup; busy: boolean; onError: (m: string) => void;
  onPicked: (r: { ok: boolean; message: string; setup: ConnectorSetup }) => void;
}) {
  const [repos, setRepos] = useState<{ full_name: string; private: boolean; open_issues: number }[] | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => { api<{ repos: typeof repos }>("/api/oauth/github/repos").then((r) => setRepos(r.repos)).catch((e) => onError(e.message)); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = async (full: string) => {
    try { onPicked(await put("/api/setup/connectors/github", { values: { GITHUB_REPO: full } })); } catch (e: any) { onError(e.message); }
  };
  const shown = (repos ?? []).filter((r) => r.full_name.toLowerCase().includes(q.toLowerCase())).slice(0, 12);
  return (
    <div className="sheet-pick">
      <h3>{st.repo ? "Agents work on" : "Pick the repo agents work on"}</h3>
      {st.repo && <p><a href={`https://github.com/${st.repo}`} target="_blank" rel="noreferrer">🐙 {st.repo} ↗</a> <span className="muted">(a step can name another repo)</span></p>}
      {!repos && <p className="muted">Loading your repos…</p>}
      {repos && repos.length > 8 && <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter repos…" aria-label="Filter repos" />}
      {repos && (
        <div className="pick-list">
          {shown.map((r) => (
            <button key={r.full_name} className={`pick ${st.repo === r.full_name ? "on" : ""}`} disabled={busy} onClick={() => pick(r.full_name)}>
              {r.private ? "🔒" : "📂"} {r.full_name}{st.repo === r.full_name && " ✓"}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Admin: MCP servers whose tools skills can be given (🧰 Tools on a skill). */
function McpServers({ admin, onError, onToast }: { admin: boolean; onError: (m: string) => void; onToast?: (m: string) => void }) {
  type Srv = { id: string; name: string; url: string; has_token: boolean; error: string; tools: { name: string; description: string; read_only: boolean }[] };
  const [list, setList] = useState<Srv[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState("");
  const reload = () => api<Srv[]>("/api/mcp/servers").then(setList).catch(() => {});
  useEffect(() => { reload(); }, []);
  const add = async () => {
    setBusy("add");
    try {
      const s = await post<Srv>("/api/mcp/servers", { name, url, token });
      setName(""); setUrl(""); setToken(""); reload();
      if (s.error) onError(`Added ${s.name}, but: ${s.error}`); else onToast?.(`🧩 ${s.name}: ${s.tools.length} tools`);
    } catch (e: any) { onError(e.message); } finally { setBusy(""); }
  };
  const act = async (id: string, what: "refresh" | "remove") => {
    setBusy(id);
    try {
      if (what === "refresh") await post(`/api/mcp/servers/${id}/refresh`); else await del(`/api/mcp/servers/${id}`);
      reload();
    } catch (e: any) { onError(e.message); } finally { setBusy(""); }
  };
  return (
    <section>
      <h3>🧩 MCP servers <small className="muted">· tools for skills</small></h3>
      <p className="muted">Add a remote MCP server (Streamable HTTP) once; then tick it under 🧰 Tools on any skill. Its AI agent
        step can call these tools by itself.</p>
      <div className="conns">
        {list.map((s) => (
          <div key={s.id} className="conn">
            <div className="conn-row">
              <div className="conn-head">
                <span className="conn-icon" aria-hidden>🧩</span>
                <span className="grow"><b>{s.name}</b>
                  <span className="muted">{s.error ? <span className="bad-line">{s.error}</span> : `${s.tools.length} tools · ${s.tools.slice(0, 4).map((t) => t.name).join(", ")}${s.tools.length > 4 ? "…" : ""}`}</span>
                </span>
              </div>
              {admin && <button className="btn btn-sm" disabled={busy === s.id} onClick={() => act(s.id, "refresh")}>↻</button>}
              {admin && <button className="btn btn-sm btn-bad" disabled={busy === s.id} onClick={() => act(s.id, "remove")} aria-label={`Remove ${s.name}`}>✕</button>}
            </div>
          </div>
        ))}
      </div>
      {admin && (
        <div className="manual-form mcp-add">
          <div className="row tight">
            <input className="grow" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. Linear)" aria-label="MCP server name" />
          </div>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://mcp.example.com/mcp" aria-label="MCP server URL" />
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Bearer token (optional)" aria-label="MCP token" autoComplete="off" />
          <button className="btn btn-go" disabled={busy === "add" || !name.trim() || !url.startsWith("http")} onClick={add}>{busy === "add" ? "Connecting…" : "+ Add MCP server"}</button>
        </div>
      )}
    </section>
  );
}

function NotionPicker({ st, busy, onPicked, onError }: {
  st: ConnectorSetup; busy: boolean; onError: (m: string) => void;
  onPicked: (r: { ok: boolean; message: string; setup: ConnectorSetup }) => void;
}) {
  const [data, setData] = useState<{ databases: { id: string; title: string; url: string }[]; pages: { id: string; title: string }[] } | null>(null);
  const [working, setWorking] = useState(false);
  const load = () => api<typeof data>("/api/oauth/notion/databases").then(setData).catch((e) => onError(e.message));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = async (body: { database_id?: string; create_in_page?: string }) => {
    setWorking(true);
    try { onPicked(await post("/api/oauth/notion/database", body)); } catch (e: any) { onError(e.message); } finally { setWorking(false); }
  };
  const cur = data?.databases.find((d) => d.id.replace(/-/g, "") === st.database_id.replace(/-/g, ""));
  return (
    <div className="sheet-pick">
      <h3>{st.database_id ? "Agents write to" : "Pick a database"}</h3>
      {!data && <p className="muted">Loading what you shared…</p>}
      {data && data.databases.length > 0 && (
        <div className="pick-list">
          {data.databases.map((d) => (
            <button key={d.id} className={`pick ${cur?.id === d.id ? "on" : ""}`} disabled={busy || working} onClick={() => pick({ database_id: d.id })}>
              🗂 {d.title}{cur?.id === d.id && " ✓"}
            </button>
          ))}
        </div>
      )}
      {data && data.pages.length > 0 && (
        <>
          <p className="muted or">or create a new “OrgTwin” database inside a page</p>
          <div className="pick-list">
            {data.pages.slice(0, 8).map((p) => (
              <button key={p.id} className="pick" disabled={busy || working} onClick={() => pick({ create_in_page: p.id })}>＋ in “{p.title}”</button>
            ))}
          </div>
        </>
      )}
      {data && !data.databases.length && !data.pages.length && (
        <p className="muted">Nothing was shared with OrgTwin. Click <b>Switch account</b> and tick a page or database on Notion&apos;s screen.</p>
      )}
      {data && <button className="linkbtn" onClick={load}>↻ Refresh list</button>}
    </div>
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
