"use client";
import { useCallback, useEffect, useState } from "react";
import { api, del, post, put, setUserToken } from "@/lib/api";
import { loadMe, useMe, type Notify, type Person, type Quest } from "@/lib/access";
import type { Org } from "@/lib/types";
import { teamColor } from "@/components/world/layout";

/** Quests (setup checklist with green ticks), the admin's people list, invites and department ticks. */
export default function PeoplePanel({ org, onError, onToast, version }: {
  org: Org; onError: (m: string) => void; onToast: (m: string) => void; version: number;
}) {
  const me = useMe();
  const [people, setPeople] = useState<Person[]>([]);
  const [quests, setQuests] = useState<Quest[]>([]);
  const [fresh, setFresh] = useState<Person | null>(null);   // just invited: show the link
  const reload = useCallback(() => {
    api<Quest[]>("/api/quests").then(setQuests).catch(() => {});
    api<Person[]>("/api/people").then(setPeople).catch(() => setPeople([]));
  }, []);
  useEffect(() => { reload(); }, [reload, version, me?.user?.id, me?.people_mode]);

  return (
    <>
      <QuestBoard quests={quests} />
      {me?.setup_needed && <ClaimAdmin onDone={(p) => { onToast(`👑 ${p.name} is the admin`); reload(); }} onError={onError} />}
      {me?.people_mode && !me.user && !me.admin && <SignIn onError={onError} onToast={onToast} />}
      {me?.user && <MyCard me={me.user} org={org} onSaved={reload} onError={onError} />}

      {me?.people_mode && people.length > 0 && (
        <section>
          <h3>Team ({people.length})</h3>
          <p className="muted">Ticked departments = where that person can create and edit skills. Everyone can see and run every skill.</p>
          <div className="people">
            {people.map((p) => (
              <PersonRow key={p.id} p={p} org={org} admin={!!me?.admin} self={me?.user?.id === p.id}
                         onChanged={reload} onError={onError} onLink={setFresh} />
            ))}
          </div>
        </section>
      )}
      {fresh?.invite_url && <InviteLink p={fresh} onClose={() => setFresh(null)} />}
      {me?.admin && <InviteForm org={org} onInvited={(p) => { setFresh(p); reload(); onToast(`✉️ ${p.name} invited`); }} onError={onError} />}
    </>
  );
}

function QuestBoard({ quests }: { quests: Quest[] }) {
  if (!quests.length) return null;
  const done = quests.filter((q) => q.done).length;
  return (
    <section className="quests">
      <div className="row tight"><h3 className="grow">🗺 Quests</h3><span className="tag tag-go">{done}/{quests.length}</span></div>
      <div className="quest-bar"><i style={{ width: `${(done / quests.length) * 100}%` }} /></div>
      <ol className="quest-list">
        {quests.map((q) => (
          <li key={q.key} className={q.done ? "done" : ""}>
            <span className="tick" aria-label={q.done ? "done" : "not done"}>{q.done ? "✓" : ""}</span>
            <span className="grow"><b>{q.title}</b>{!q.done && <small>{q.hint}</small>}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ClaimAdmin({ onDone, onError }: { onDone: (p: Person) => void; onError: (m: string) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const claim = async () => {
    setBusy(true);
    try {
      const p = await post<Person>("/api/people/claim", { name, email });
      setUserToken(p.token ?? "");
      await loadMe(true);
      onDone(p);
    } catch (e: any) { onError(e.message); } finally { setBusy(false); }
  };
  return (
    <section className="claim">
      <h3>👑 Become the admin</h3>
      <p className="muted">Right now anyone can change anything. Make yourself the admin, then invite your team and choose
        which departments each person looks after.</p>
      <div className="manual-form">
        <label className="field"><span>Your name</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Ada Lovelace" /></label>
        <label className="field"><span>Email <small>· optional</small></span><input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="ada@company.com" /></label>
        <button className="btn btn-go" disabled={busy || !name.trim()} onClick={claim}>{busy ? "Setting up…" : "I'm the admin"}</button>
      </div>
    </section>
  );
}

function SignIn({ onError, onToast }: { onError: (m: string) => void; onToast: (m: string) => void }) {
  const [link, setLink] = useState("");
  const go = async () => {
    const token = (link.match(/invite=([\w-]+)/)?.[1] ?? link).trim();
    try {
      const p = await post<Person>("/api/people/join", { token });
      setUserToken(token); await loadMe(true); onToast(`👋 Welcome, ${p.name}`);
    } catch (e: any) { onError(e.message); }
  };
  return (
    <section className="claim">
      <h3>🔑 Sign in</h3>
      <p className="muted">You can look around and see every skill. To run skills, send requests or record skills, open the
        invite link your admin sent you, or paste it here.</p>
      <div className="row">
        <input className="grow" value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://…/?invite=ot_…" aria-label="Invite link" />
        <button className="btn btn-go" disabled={link.trim().length < 8} onClick={go}>Sign in</button>
      </div>
    </section>
  );
}

function MyCard({ me, org, onSaved, onError }: { me: Person; org: Org; onSaved: () => void; onError: (m: string) => void }) {
  const [n, setN] = useState<Notify>({ slack: true, discord: true, slack_id: "", discord_id: "", ...me.notify });
  const [dirty, setDirty] = useState(false);
  const set = (patch: Partial<Notify>) => { setN({ ...n, ...patch }); setDirty(true); };
  const save = async () => {
    try { await put(`/api/people/${me.id}`, { notify: n }); setDirty(false); onSaved(); loadMe(true); } catch (e: any) { onError(e.message); }
  };
  const depts = me.role === "admin" ? "every department (admin)" : me.departments.map((k) => org.teams.find((t) => t.key === k)?.name ?? k).join(", ") || "no department yet";
  return (
    <section className="me-card">
      <div className="row tight">
        <span className="p-avatar" aria-hidden>{me.name[0]?.toUpperCase()}</span>
        <div className="grow"><b>{me.name}</b><span className="muted"> · {me.role}</span><div className="muted">Edits skills in {depts}</div></div>
        <button className="linkbtn" onClick={() => { setUserToken(""); loadMe(true); }}>sign out</button>
      </div>
      <details>
        <summary>🔔 How should OrgTwin tell me about requests?</summary>
        <div className="manual-form">
          <label className="toggle-row"><input type="checkbox" checked={n.slack} onChange={(e) => set({ slack: e.target.checked })} /> Slack</label>
          <label className="field"><span>Slack member ID <small>· to @mention you (Profile → ⋯ → Copy member ID)</small></span>
            <input value={n.slack_id} onChange={(e) => set({ slack_id: e.target.value.trim() })} placeholder="U0123ABCD" /></label>
          <label className="toggle-row"><input type="checkbox" checked={n.discord} onChange={(e) => set({ discord: e.target.checked })} /> Discord</label>
          <label className="field"><span>Discord user ID <small>· Settings → Advanced → Developer Mode, then right-click you → Copy User ID</small></span>
            <input value={n.discord_id} onChange={(e) => set({ discord_id: e.target.value.trim() })} placeholder="123456789012345678" /></label>
          <button className="btn btn-go" disabled={!dirty} onClick={save}>Save</button>
        </div>
      </details>
    </section>
  );
}

function DeptTicks({ org, value, onToggle, disabled }: {
  org: Org; value: string[]; onToggle: (k: string) => void; disabled?: boolean;
}) {
  return (
    <div className="dept-ticks">
      {org.teams.map((t, i) => {
        const on = value.includes(t.key);
        return (
          <button key={t.key} type="button" className={`dept-tick ${on ? "on" : ""}`} disabled={disabled} aria-pressed={on}
                  style={{ ["--team" as any]: teamColor(t.key, i) }} onClick={() => onToggle(t.key)}>
            <span className="tick" aria-hidden>{on ? "✓" : ""}</span>{t.name}
          </button>
        );
      })}
    </div>
  );
}

function PersonRow({ p, org, admin, self, onChanged, onError, onLink }: {
  p: Person; org: Org; admin: boolean; self: boolean; onChanged: () => void; onError: (m: string) => void; onLink: (p: Person) => void;
}) {
  const toggle = async (k: string) => {
    const departments = p.departments.includes(k) ? p.departments.filter((x) => x !== k) : [...p.departments, k];
    try { await put(`/api/people/${p.id}`, { departments }); onChanged(); loadMe(true); } catch (e: any) { onError(e.message); }
  };
  const role = async () => {
    try { await put(`/api/people/${p.id}`, { role: p.role === "admin" ? "member" : "admin" }); onChanged(); loadMe(true); } catch (e: any) { onError(e.message); }
  };
  const link = async () => { try { onLink(await post<Person>(`/api/people/${p.id}/link`)); onChanged(); } catch (e: any) { onError(e.message); } };
  const remove = async () => {
    if (!window.confirm(`Remove ${p.name}? Their invite link stops working.`)) return;
    try { await del(`/api/people/${p.id}`); onChanged(); } catch (e: any) { onError(e.message); }
  };
  return (
    <div className="person-row">
      <div className="row tight">
        <span className="p-avatar" aria-hidden>{p.name[0]?.toUpperCase()}</span>
        <div className="grow">
          <b>{p.name}</b>{self && <span className="muted"> (you)</span>}
          <div className="muted">{p.email || "no email"} · {p.joined ? "joined" : "invite not opened yet"}</div>
        </div>
        <span className={`tag ${p.role === "admin" ? "tag-warn" : ""}`}>{p.role === "admin" ? "👑 admin" : "member"}</span>
      </div>
      {p.role === "admin"
        ? <p className="muted">Admins can edit skills in every department.</p>
        : <DeptTicks org={org} value={p.departments} onToggle={toggle} disabled={!admin} />}
      {admin && (
        <div className="row tight person-actions">
          <button className="linkbtn" onClick={link}>🔗 new invite link</button>
          {!self && <button className="linkbtn" onClick={role}>{p.role === "admin" ? "make member" : "make admin"}</button>}
          {!self && <button className="linkbtn bad-link" onClick={remove}>remove</button>}
        </div>
      )}
    </div>
  );
}

function InviteLink({ p, onClose }: { p: Person; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(p.invite_url!); setCopied(true); } catch { setCopied(false); }
  };
  return (
    <section className="invite-link">
      <h3>✉️ Send this link to {p.name}</h3>
      <p className="muted">It signs them in, like a password: send it privately (Slack DM, email). Making a new link stops this one.</p>
      <div className="row">
        <input className="grow" readOnly value={p.invite_url} onFocus={(e) => e.currentTarget.select()} aria-label="Invite link" />
        <button className="btn btn-go" onClick={copy}>{copied ? "✓ Copied" : "Copy"}</button>
      </div>
      {p.email && <a className="linkbtn" href={`mailto:${p.email}?subject=${encodeURIComponent("Your OrgTwin invite")}&body=${encodeURIComponent(`Open this link to join: ${p.invite_url}`)}`}>Open an email to {p.email}</a>}
      <button className="linkbtn" onClick={onClose}>done</button>
    </section>
  );
}

function InviteForm({ org, onInvited, onError }: { org: Org; onInvited: (p: Person) => void; onError: (m: string) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [deps, setDeps] = useState<string[]>([]);
  const [slackId, setSlackId] = useState("");
  const [discordId, setDiscordId] = useState("");
  const [busy, setBusy] = useState(false);
  const invite = async () => {
    setBusy(true);
    try {
      const p = await post<Person>("/api/people", { name, email, role: "member", departments: deps,
                                                    notify: { slack: true, discord: true, slack_id: slackId, discord_id: discordId } });
      setName(""); setEmail(""); setDeps([]); setSlackId(""); setDiscordId("");
      onInvited(p);
    } catch (e: any) { onError(e.message); } finally { setBusy(false); }
  };
  return (
    <section>
      <h3>＋ Invite someone</h3>
      <div className="manual-form">
        <label className="field"><span>Name</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Ivan from IT" /></label>
        <label className="field"><span>Email <small>· optional</small></span><input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="ivan@company.com" /></label>
        <div className="field"><span>Departments they look after <small>· tick one or more</small></span>
          <DeptTicks org={org} value={deps} onToggle={(k) => setDeps(deps.includes(k) ? deps.filter((x) => x !== k) : [...deps, k])} />
        </div>
        <details>
          <summary>🔔 Notify them (optional)</summary>
          <label className="field"><span>Slack member ID</span><input value={slackId} onChange={(e) => setSlackId(e.target.value.trim())} placeholder="U0123ABCD" /></label>
          <label className="field"><span>Discord user ID</span><input value={discordId} onChange={(e) => setDiscordId(e.target.value.trim())} placeholder="123456789012345678" /></label>
        </details>
        <button className="btn btn-go" disabled={busy || !name.trim()} onClick={invite}>{busy ? "Inviting…" : "Invite & get link"}</button>
      </div>
    </section>
  );
}
