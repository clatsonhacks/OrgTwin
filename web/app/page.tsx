"use client";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE, api, post, setToken } from "@/lib/api";
import type { Approval, LiveEvent, Metrics, Org, Outbox, Status, Workflow } from "@/lib/types";
import type { Courier } from "@/components/world/World";
import StartCard from "@/components/ui/StartCard";
import TeamDrawer from "@/components/ui/TeamDrawer";
import { Activity, ApprovalCard, Departments, Hotbar, RunDetails, SidePanel, SkillLibrary, Toasts, TopBar, useRunView,
         type Toast } from "@/components/ui/Hud";
import SkillRecorder from "@/components/ui/SkillRecorder";
import { ownerOf, type Person } from "@/lib/people";

const World = dynamic(() => import("@/components/world/World"), { ssr: false });

type Panel = "team" | "run" | "activity" | "skills" | null;

export default function Home() {
  const [status, setStatus] = useState<Status | null>(null);
  const [org, setOrg] = useState<Org | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [onboarding, setOnboarding] = useState(false);
  const [selectedTeam, setSelectedTeam] = useState<string | null>(null);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [outbox, setOutbox] = useState<Outbox>({ emails: [], slack: [] });
  const [activeTeam, setActiveTeam] = useState<string | null>(null);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [recorder, setRecorder] = useState<{ team: string; role: string; person: Person | null } | null>(null);
  const [inside, setInside] = useState<string | null>(null);
  const [selectedPerson, setSelectedPerson] = useState<string | null>(null);
  const [activePerson, setActivePerson] = useState<string | null>(null);
  const runIdRef = useRef<number | null>(null);
  runIdRef.current = runId;

  const toast = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t.slice(-3), { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "bad" ? 8000 : 4200);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [o, w, a, m, ob] = await Promise.all([
        api<Org | null>("/api/org"), api<Workflow[]>("/api/workflows"), api<Approval[]>("/api/approvals"),
        api<Metrics>("/api/metrics"), api<Outbox>("/api/outbox"),
      ]);
      setOrg(o); setWorkflows(w); setApprovals(a); setMetrics(m); setOutbox(ob);
    } catch (e: any) { toast(e.message, "bad"); }
    setLoaded(true);
  }, [toast]);

  useEffect(() => {
    api<Status>("/api/status").then((s) => {
      setStatus(s);
      if (s.auth_required && !localStorage.getItem("orgtwin_token")) setToken(window.prompt("Access token for this demo:") ?? "");
    }).catch((e) => toast(`API not reachable: ${e.message}`, "bad"));
    refresh();
  }, [refresh, toast]);

  // Live events from the API (Server-Sent Events) drive the world.
  useEffect(() => {
    const es = new EventSource(`${API_BASE}/api/events/stream`);
    es.onmessage = (msg) => {
      const ev: LiveEvent = JSON.parse(msg.data);
      setEvents((prev) => [...prev.slice(-400), ev]);
      const p = ev.payload || {};
      switch (ev.type) {
        case "run.started":
          setRunId(ev.run_id); api<Metrics>("/api/metrics").then(setMetrics); break;
        case "step.started":
          setActiveTeam(p.team); break;
        case "handoff": {
          const id = `c${ev.id}`;
          const label = p.task?.message?.parts?.[0]?.text ?? "handoff";
          setCouriers((c) => [...c, { id, from: p.from, to: p.to, start: performance.now(), label }]);
          setTimeout(() => setCouriers((c) => c.filter((x) => x.id !== id)), 3600);
          toast(`📦 ${cap(p.from)} agent → ${cap(p.to)} agent: ${label}`);
          break;
        }
        case "approval.requested":
          toast(`✋ ${cap(p.team)} agent is waiting for your approval`);
          api<Approval[]>("/api/approvals").then(setApprovals); break;
        case "approval.decided":
          api<Approval[]>("/api/approvals").then(setApprovals); break;
        case "run.completed":
          setActiveTeam(null); setActivePerson(null); refresh();
          toast(p.hours_saved ? `🎉 Done — ~${p.hours_saved}h of manual work saved` : `${p.result}`, "ok"); break;
        case "run.failed":
          setActiveTeam(null); setActivePerson(null); refresh(); toast(`Run failed: ${p.error}`, "bad"); break;
        case "step.completed":
          if (["email", "slack"].includes(p.tool) || p.step === "send_invoice") api<Outbox>("/api/outbox").then(setOutbox);
          break;
        case "org.generated":
          setInside(null); setSelectedPerson(null); refresh(); break;
        case "workflow.assigned":
          if (p.person_name) toast(`📘 ${p.person_name} now owns “${p.name}”`, "ok");
          refresh(); break;
        case "workflow.created":
        case "skill.saved":
          refresh(); break;
      }
    };
    return () => es.close();
  }, [refresh, toast]);

  const hero = workflows.find((w) => w.kind === "hero");
  const view = useRunView(events, runId);
  const running = runId != null && !view.finished && Object.keys(view.st).length > 0;
  const pending = approvals.filter((a) => a.status === "pending");
  const approvalTeams = useMemo(() => pending.map((a) => a.team_key), [pending]);
  const teamIdx = org?.teams.findIndex((t) => t.key === selectedTeam) ?? -1;
  const team = teamIdx >= 0 ? org!.teams[teamIdx] : null;
  const showStart = loaded && (!org || onboarding);
  const skills = workflows.filter((w) => w.kind === "skill");
  const skillRoles = useMemo(() => skills.map((w) => `${w.team_key}/${w.role_key}`), [skills]);
  const recTeamIdx = recorder ? org?.teams.findIndex((t) => t.key === recorder.team) ?? -1 : -1;
  const recTeam = recTeamIdx >= 0 ? org!.teams[recTeamIdx] : null;
  const recRole = recTeam?.roles.find((r) => r.key === recorder?.role) ?? null;

  // people with skills inside the open office: { "role#seat": count }
  const skillPeople = useMemo(() => {
    const out: Record<string, number> = {};
    workflows.filter((w) => w.kind === "skill" && w.team_key === inside).forEach((w) => { const k = ownerOf(w); out[k] = (out[k] ?? 0) + 1; });
    return out;
  }, [workflows, inside]);

  const selectTeam = (k: string | null) => {
    setSelectedTeam(k); setSelectedRole(null); setSelectedPerson(null);
    // switching department while inside walks you into the other office
    setInside((cur) => (cur && k ? k : null));
    setPanel(k ? "team" : (p) => (p === "team" ? null : p));
  };
  const enter = (k: string) => { setSelectedTeam(k); setInside(k); setSelectedPerson(null); setPanel("team"); };
  const exit = () => { setInside(null); setSelectedPerson(null); };
  const selectPerson = (id: string | null) => {
    setSelectedPerson(id);
    if (id) { setSelectedRole(id.split("#")[0]); setPanel("team"); }
  };

  const startRun = async () => {
    setBusy(true);
    try {
      const r = await post<{ run_id: number }>("/api/runs");
      setRunId(r.run_id); selectTeam(null);
    } catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
  };
  const runWorkflow = async (id: number) => {
    setBusy(true);
    try {
      const r = await post<{ run_id: number }>(`/api/workflows/${id}/run`);
      const wf = workflows.find((w) => w.id === id);
      setRunId(r.run_id);
      if (wf && inside === wf.team_key) setActivePerson(ownerOf(wf)); // stay inside and watch them work
      else { selectTeam(null); setPanel(null); }
    } catch (e: any) { toast(e.message, "bad"); } finally { setBusy(false); }
  };
  const decide = async (a: Approval, approved: boolean) => {
    try { await post(`/api/approvals/${a.id}`, { approved, who: "demo user" }); } catch (e: any) { toast(e.message, "bad"); }
  };

  return (
    <main className="app">
      <div className="sky" aria-hidden />
      <div className="world">
        <World org={showStart && !org ? null : org} selectedTeam={selectedTeam} selectedRole={selectedRole}
               activeTeam={activeTeam} approvalTeams={approvalTeams} couriers={couriers} skillRoles={skillRoles} paused={!!recorder}
               inside={inside} selectedPerson={selectedPerson} activePerson={activePerson} skillPeople={skillPeople}
               onSelectPerson={selectPerson} onEnter={enter}
               onSelectTeam={selectTeam}
               onSelectRole={(t, r) => { setSelectedTeam(t); setSelectedRole(r); setPanel("team"); }} />
      </div>

      <TopBar org={org} metrics={metrics} status={status} onRebuild={() => setOnboarding(true)} />

      {org && !showStart && (
        <>
          <Departments org={org} selected={selectedTeam} onSelect={selectTeam} />
          <div className="fabs">
            <button className={`btn ${panel === "skills" ? "btn-on" : ""}`} onClick={() => setPanel(panel === "skills" ? null : "skills")}>
              📘 Skills {skills.length > 0 && <span className="count count-skill">{skills.length}</span>}
            </button>
            <button className={`btn ${panel === "activity" ? "btn-on" : ""}`} onClick={() => setPanel(panel === "activity" ? null : "activity")}>
              📜 Activity {outbox.emails.length > 0 && <span className="count">{outbox.emails.length}</span>}
            </button>
          </div>
          {panel === "team" && team && (
            <TeamDrawer org={org} team={team} index={teamIdx} inside={inside === team.key} onEnter={() => enter(team.key)} onExit={exit}
                        selectedPerson={selectedPerson} onSelectPerson={selectPerson} selectedRole={selectedRole} onSelectRole={setSelectedRole}
                        workflows={workflows} onChanged={refresh} onError={(m) => toast(m, "bad")}
                        onClose={() => selectTeam(null)} onRunHero={startRun} onRunWorkflow={runWorkflow}
                        onRecord={(person) => { const r = person?.role ?? selectedRole; if (r) setRecorder({ team: team.key, role: r, person }); }} />
          )}
          {panel === "run" && <SidePanel title="Run details" onClose={() => setPanel(null)}><RunDetails view={view} /></SidePanel>}
          {panel === "skills" && (
            <SidePanel title="Skill library" onClose={() => setPanel(null)}>
              <SkillLibrary org={org} workflows={workflows} onRun={runWorkflow}
                            onOpenTeam={(t, r, person) => { if (person) { enter(t); selectPerson(person); } else { setSelectedTeam(t); setSelectedRole(r); setPanel("team"); } }} />
            </SidePanel>
          )}
          {panel === "activity" && <SidePanel title="Activity" onClose={() => setPanel(null)}><Activity outbox={outbox} events={events} /></SidePanel>}

          <div className="bottom">
            {pending.map((a) => <ApprovalCard key={a.id} a={a} onDecide={(ok) => decide(a, ok)} />)}
            {hero && <Hotbar steps={view.steps ?? hero.steps} view={view} running={running} busy={busy} canRun={!!org}
                             onRun={startRun} onDetails={() => setPanel("run")} />}
          </div>
          {!selectedTeam && !running && !view.finished && (
            <div className="hint block">Click a building, then click it again to step inside · scroll to zoom · drag to move</div>
          )}
          {inside && !selectedPerson && (
            <div className="hint block">Click anyone at a desk to see their role and give them a skill</div>
          )}
        </>
      )}

      {showStart && (
        <StartCard onError={(m) => toast(m, "bad")} onClose={org ? () => setOnboarding(false) : undefined}
                   onBuilt={(o) => { setOrg(o); setOnboarding(false); selectTeam(null); refresh(); toast(`🧱 ${o.company} is built — ${o.teams.length} departments`, "ok"); }} />
      )}
      {recorder && recTeam && recRole && (
        <SkillRecorder team={recTeam} teamIndex={recTeamIdx} role={recRole} person={recorder.person} onError={(m) => toast(m, "bad")}
                       onClose={() => setRecorder(null)}
                       onSaved={(w) => { setRecorder(null); refresh(); toast(`📘 ${recorder.person?.name ?? recRole.name} learned “${w.name}”`, "ok"); }} />
      )}
      <Toasts toasts={toasts} />
    </main>
  );
}

const cap = (s?: string) => (s ? s[0].toUpperCase() + s.slice(1) : "");
