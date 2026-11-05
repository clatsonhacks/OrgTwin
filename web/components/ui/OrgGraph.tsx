"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, TOOL_ICON } from "@/lib/api";
import type { GraphEdge, GraphNode, Org, OrgGraph as Graph } from "@/lib/types";
import { MAX_DESKS_PER_ROLE, peopleOf } from "@/lib/people";
import { teamColor } from "@/components/world/layout";

/** Radial knowledge graph of the org: org → teams → roles → people → skills/workflows, with reuse links. */

const R = { team: 120, role: 225, person: 325, wf: 425 };
const SKILL = "#5b3fd6";
const WF = "#c9971d";
const VIEW = 1180;

type Placed = GraphNode & { x: number; y: number; a: number; color: string; name: string };

export default function OrgGraph({ org, version, onClose, onVisit }: {
  org: Org; version: number; onClose: () => void; onVisit: (team: string, person: string) => void;
}) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [view, setView] = useState({ x: -VIEW / 2, y: -VIEW / 2, w: VIEW });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<number | null>(null);
  const fitView = useRef(view);
  const svg = useRef<SVGSVGElement>(null);

  useEffect(() => { api<Graph>("/api/graph").then(setGraph).catch((e) => setErr(e.message)); }, [version]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && (sel ? setSel(null) : onClose());
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [sel, onClose]);

  const names = useMemo(() => {
    const m: Record<string, string> = {};
    org.teams.forEach((t) => peopleOf(t).forEach((p) => { m[`${t.key}/${p.id}`] = p.name; }));
    return m;
  }, [org]);

  const { placed, edges, byId } = useMemo(() => layout(graph, org, names), [graph, org, names]);

  // fit the view to what's drawn (labels stick out ~200 units past the outermost nodes)
  useEffect(() => {
    if (!placed.length) return;
    const xs = placed.map((n) => n.x), ys = placed.map((n) => n.y);
    const pad = 230;
    const x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad, y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad;
    const w = Math.max(x1 - x0, y1 - y0, 500);
    const v = { x: (x0 + x1) / 2 - w / 2, y: (y0 + y1) / 2 - w / 2, w };
    fitView.current = v;
    setView(v);
  }, [placed]);

  const focus = sel ?? hover;
  const lit = useMemo(() => (focus ? neighbourhood(focus, graph?.edges ?? []) : null), [focus, graph]);
  const on = (id: string) => !lit || lit.has(id);

  /** zoom by factor f around a screen point (defaults to the centre) */
  const zoomAt = (f: number, clientX?: number, clientY?: number) => setView((v) => {
    const r = svg.current!.getBoundingClientRect();
    const side = Math.min(r.width, r.height);
    const scale = v.w / side;
    const px = v.x + ((clientX ?? r.left + r.width / 2) - r.left - (r.width - side) / 2) * scale;
    const py = v.y + ((clientY ?? r.top + r.height / 2) - r.top - (r.height - side) / 2) * scale;
    const w = Math.max(250, Math.min(2600, v.w * f));
    return { w, x: px - (px - v.x) * (w / v.w), y: py - (py - v.y) * (w / v.w) };
  });
  const wheel = (e: React.WheelEvent) => zoomAt(e.deltaY > 0 ? 1.12 : 1 / 1.12, e.clientX, e.clientY);
  const down = (e: React.PointerEvent) => {
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 2) { drag.current = null; pinch.current = null; return; }
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
  };
  const move = (e: React.PointerEvent) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 2) {
      const [a, b] = Array.from(pointers.current.values());
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch.current) zoomAt(pinch.current / d, (a.x + b.x) / 2, (a.y + b.y) / 2);
      pinch.current = d;
      return;
    }
    if (!drag.current) return;
    const r = svg.current!.getBoundingClientRect();
    const scale = view.w / Math.min(r.width, r.height);
    setView((v) => ({ ...v, x: drag.current!.vx - (e.clientX - drag.current!.x) * scale, y: drag.current!.vy - (e.clientY - drag.current!.y) * scale }));
  };
  const up = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    pinch.current = null;
    drag.current = null;
  };

  const s = sel ? byId[sel] : null;
  const counts = {
    people: placed.filter((n) => n.type === "person").length,
    skills: placed.filter((n) => n.type === "skill").length,
    workflows: placed.filter((n) => n.type === "workflow").length,
    reuse: (graph?.edges ?? []).filter((e) => e.type === "reuses").length,
  };

  return (
    <div className="graph-modal block" role="dialog" aria-label="Org knowledge graph">
      <header className="graph-head">
        <div className="grow">
          <div className="eyebrow">Knowledge graph</div>
          <h2>{org.company} — who knows what</h2>
        </div>
        <div className="graph-legend">
          <span><i style={{ background: "#f6c443" }} />team</span>
          <span><i style={{ background: "#fff" }} />role</span>
          <span><i style={{ background: "#f1c7a0" }} />person</span>
          <span><i style={{ background: SKILL }} />skill</span>
          <span><i style={{ background: WF }} />workflow</span>
          <span style={{ color: SKILL }}>╌ reuses</span>
        </div>
        <button className="x" onClick={onClose} aria-label="Close">✕</button>
      </header>
      <div className="graph-body">
        <svg ref={svg} className="graph-svg" viewBox={`${view.x} ${view.y} ${view.w} ${view.w}`}
             onWheel={wheel} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onPointerLeave={up}
             onClick={(e) => { if (e.target === svg.current) setSel(null); }}>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M0 0 10 5 0 10Z" fill={SKILL} />
            </marker>
          </defs>
          {[R.team, R.role, R.person, R.wf].map((r) => (
            <circle key={r} r={r} fill="none" stroke="#1d233312" strokeWidth={1} strokeDasharray="2 6" />
          ))}
          {/* tree edges */}
          {edges.filter((e) => e.type !== "reuses").map((e, i) => {
            const a = byId[e.source], b = byId[e.target];
            return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={e.type === "owns" ? b.color : a.color}
                         strokeWidth={e.type === "owns" ? 2 : 1.5} opacity={on(a.id) && on(b.id) ? 0.55 : 0.08} />;
          })}
          {/* reuse links curve through the middle */}
          {edges.filter((e) => e.type === "reuses").map((e, i) => {
            const a = byId[e.source], b = byId[e.target];
            const cx = (a.x + b.x) * 0.25, cy = (a.y + b.y) * 0.25;
            return <path key={`r${i}`} d={`M${a.x} ${a.y} Q${cx} ${cy} ${b.x} ${b.y}`} fill="none" stroke={SKILL}
                         strokeWidth={2.5} strokeDasharray="7 5" markerEnd="url(#arrow)" opacity={on(a.id) && on(b.id) ? 0.9 : 0.12} />;
          })}
          {placed.map((n) => (
            <g key={n.id} className="g-node" transform={`translate(${n.x} ${n.y})`} opacity={on(n.id) ? 1 : 0.18}
               onClick={(e) => { e.stopPropagation(); setSel(n.id === sel ? null : n.id); }}
               onPointerEnter={() => setHover(n.id)} onPointerLeave={() => setHover(null)}>
              <Shape n={n} selected={n.id === sel} />
              <Label n={n} />
            </g>
          ))}
          {err && <text x={0} y={0} textAnchor="middle" fontSize={18}>{err}</text>}
          {graph && !placed.length && <text x={0} y={0} textAnchor="middle" fontSize={18}>Build an org first</text>}
        </svg>
        <div className="graph-zoom">
          <button className="iconbtn" onClick={() => zoomAt(1 / 1.3)} aria-label="Zoom in">＋</button>
          <button className="iconbtn" onClick={() => zoomAt(1.3)} aria-label="Zoom out">－</button>
          <button className="iconbtn" onClick={() => setView(fitView.current)} aria-label="Fit">⤢</button>
        </div>
        <aside className="graph-side">
          {!s ? (
            <>
              <h3>How this is used</h3>
              <p className="muted">Every recorded skill hangs off the person who owns it. When you design a new workflow,
                OrgTwin searches this graph for skills that already cover part of the goal — same person first, then the same
                role, team, and the rest of the company — and builds on them instead of starting over.</p>
              <div className="graph-stats">
                <div><b>{org.teams.length}</b><span>teams</span></div>
                <div><b>{counts.people}</b><span>people</span></div>
                <div><b>{counts.skills}</b><span>skills</span></div>
                <div><b>{counts.reuse}</b><span>reuse links</span></div>
              </div>
              <p className="muted">Click any node to trace it. Scroll to zoom, drag to pan.</p>
            </>
          ) : <Detail n={s} graph={graph!} byId={byId} onVisit={onVisit} onPick={setSel} />}
        </aside>
      </div>
    </div>
  );
}

function Shape({ n, selected }: { n: Placed; selected: boolean }) {
  const stroke = selected ? "#000" : "#1d2333";
  const sw = selected ? 4 : 2.5;
  switch (n.type) {
    case "org": return <rect x={-34} y={-34} width={68} height={68} rx={10} fill="#f6c443" stroke={stroke} strokeWidth={3.5} />;
    case "team": return <rect x={-20} y={-20} width={40} height={40} rx={7} fill={n.color} stroke={stroke} strokeWidth={sw} />;
    case "role": return <rect x={-12} y={-12} width={24} height={24} rx={5} fill="#fff" stroke={n.color} strokeWidth={sw + 1} />;
    case "person": return (
      <g>
        <rect x={-10} y={-10} width={20} height={20} rx={4} fill="#f1c7a0" stroke={stroke} strokeWidth={sw} />
        <rect x={-10} y={-10} width={20} height={6} rx={2} fill="#3a2a1e" />
        <rect x={-10} y={5} width={20} height={5} fill={n.color} />
      </g>
    );
    case "skill": return <path d="M0 -14 14 0 0 14 -14 0Z" fill={SKILL} stroke={stroke} strokeWidth={sw} />;
    default: return <circle r={12} fill={n.kind === "hero" ? "#5aa83a" : WF} stroke={stroke} strokeWidth={sw} />;
  }
}

function Label({ n }: { n: Placed }) {
  if (n.type === "org") return <text y={56} textAnchor="middle" fontSize={20} fontWeight={700}>{n.name}</text>;
  if (n.type === "team") return <text y={38} textAnchor="middle" fontSize={17} fontWeight={700}>{n.name}</text>;
  if (n.type === "role") return <text y={-18} textAnchor="middle" fontSize={12} fill="#2d3550">{n.name}</text>;
  // people and skills: radial text pointing outward
  const deg = (n.a * 180) / Math.PI;
  const flip = Math.cos(n.a) < 0;
  const off = n.type === "person" ? 16 : 20;
  const label = n.type === "skill" ? `📘 ${n.name}` : n.type === "workflow" ? `${n.kind === "hero" ? "⚡" : "✦"} ${n.name}` : n.name;
  return (
    <text transform={`rotate(${flip ? deg + 180 : deg})`} x={flip ? -off : off} y={4} textAnchor={flip ? "end" : "start"}
          fontSize={n.type === "person" ? 13 : 13} fontWeight={n.type === "skill" ? 600 : 400}>
      {label.length > 34 ? `${label.slice(0, 33)}…` : label}
    </text>
  );
}

function Detail({ n, graph, byId, onVisit, onPick }: {
  n: Placed; graph: Graph; byId: Record<string, Placed>; onVisit: (team: string, person: string) => void; onPick: (id: string) => void;
}) {
  const out = (type: GraphEdge["type"]) => graph.edges.filter((e) => e.source === n.id && e.type === type).map((e) => byId[e.target] ?? null).filter(Boolean) as Placed[];
  const inn = (type: GraphEdge["type"]) => graph.edges.filter((e) => e.target === n.id && e.type === type).map((e) => byId[e.source] ?? null).filter(Boolean) as Placed[];
  const link = (x: Placed) => <li key={x.id}><button className="linkbtn" onClick={() => onPick(x.id)}>{x.type === "skill" ? "📘 " : ""}{x.name}</button></li>;
  const team = n.team ? byId[`team:${n.team}`] : null;

  if (n.type === "skill" || n.type === "workflow") {
    const owner = inn("owns")[0];
    const tools = graph.edges.filter((e) => e.source === n.id && e.type === "uses_tool").map((e) => e.target.slice(5));
    const reuses = out("reuses"), reusedBy = inn("reuses");
    return (
      <>
        <div className="eyebrow">{n.type === "skill" ? "📘 Recorded skill" : n.kind === "hero" ? "⚡ Live workflow" : "✦ Designed workflow"}</div>
        <h3>{n.name}</h3>
        <p className="muted">{owner ? `${owner.name} · ` : ""}{team?.name} · {n.steps} steps</p>
        <p>{tools.map((t) => <span key={t} className="tag" style={{ marginRight: 4 }}>{TOOL_ICON[t] ?? "•"} {t}</span>)}</p>
        {reuses.length > 0 && <><b>Built on</b><ul>{reuses.map(link)}</ul></>}
        {n.type === "skill" && <><b>Reused by</b>{reusedBy.length ? <ul>{reusedBy.map(link)}</ul> : <p className="muted">Not reused yet — describe a workflow with a similar goal and it will be.</p>}</>}
        {owner && n.team && owner.person && <button className="btn btn-go" onClick={() => onVisit(n.team!, owner.person!)}>🚪 Visit {owner.name}&apos;s desk</button>}
      </>
    );
  }
  if (n.type === "person") {
    const owned = out("owns");
    return (
      <>
        <div className="eyebrow">Person</div>
        <h3>{n.name}</h3>
        <p className="muted">{byId[`role:${n.team}/${n.role}`]?.name} · {team?.name}</p>
        <b>Owns</b>{owned.length ? <ul>{owned.map(link)}</ul> : <p className="muted">No skills yet.</p>}
        <button className="btn btn-go" onClick={() => onVisit(n.team!, n.person!)}>🚪 Visit desk</button>
      </>
    );
  }
  if (n.type === "role") {
    return (
      <>
        <div className="eyebrow">Role · {team?.name}</div>
        <h3>{n.name}</h3>
        <p className="muted">{n.headcount} {n.headcount === 1 ? "person" : "people"}</p>
        {!!n.tasks?.length && <><b>Tasks</b><ul>{n.tasks.map((t) => <li key={t}>{t}</li>)}</ul></>}
        <b>People</b><ul>{out("seat").map(link)}</ul>
      </>
    );
  }
  if (n.type === "team") {
    const roles = out("has_role");
    const skills = graph.nodes.filter((x) => x.type === "skill" && x.team === n.team).length;
    return (
      <>
        <div className="eyebrow">Team</div>
        <h3>{n.name}</h3>
        <p className="muted">{n.headcount} people · {roles.length} roles · {skills} skills</p>
        <ul>{roles.map(link)}</ul>
      </>
    );
  }
  return <><h3>{n.name}</h3><p className="muted">The whole company.</p></>;
}

/* ---------------- layout ---------------- */

function polar(r: number, a: number) { return { x: Math.cos(a) * r, y: Math.sin(a) * r }; }

function layout(graph: Graph | null, org: Org, names: Record<string, string>) {
  const byId: Record<string, Placed> = {};
  if (!graph) return { placed: [] as Placed[], edges: [] as GraphEdge[], byId };
  const nodes = Object.fromEntries(graph.nodes.map((n) => [n.id, n]));
  const kids = (id: string, type?: GraphEdge["type"]) =>
    graph.edges.filter((e) => e.source === id && (!type || e.type === type)).map((e) => nodes[e.target]).filter(Boolean);
  const color = (team?: string) => teamColor(team ?? "", Math.max(0, org.teams.findIndex((t) => t.key === team)));
  const nameOf = (n: GraphNode) => n.type === "person" ? names[`${n.team}/${n.person}`] ?? n.label : n.label;
  const put = (n: GraphNode, r: number, a: number) => {
    byId[n.id] = { ...n, ...polar(r, a), a, color: n.type === "team" || n.type === "role" || n.type === "person" ? color(n.team) : n.type === "skill" ? SKILL : WF, name: nameOf(n) };
  };

  // leaf weights: a person takes as much room as the skills they own (min 1); empty roles still take 1
  const teams = kids("org", "has_team");
  const seatsOf = (roleId: string) => kids(roleId, "seat").filter((p) => Number((p.person ?? "").split("#")[1]) < MAX_DESKS_PER_ROLE);
  const owns = (pid: string) => kids(pid, "owns");
  const wPerson = (pid: string) => Math.max(1, owns(pid).length);
  const wRole = (rid: string) => Math.max(1, seatsOf(rid).reduce((s, p) => s + wPerson(p.id), 0));
  const wTeam = (tid: string) => Math.max(1, kids(tid, "has_role").reduce((s, r) => s + wRole(r.id), 0));
  const GAP = 0.8;
  const total = teams.reduce((s, t) => s + wTeam(t.id), 0) + GAP * teams.length;
  const unit = (Math.PI * 2) / Math.max(1, total);

  put(nodes.org, 0, 0);
  byId.org.x = 0; byId.org.y = 0;
  let a = -Math.PI / 2;
  teams.forEach((t) => {
    a += (GAP * unit) / 2;
    const t0 = a;
    kids(t.id, "has_role").forEach((r) => {
      const r0 = a;
      seatsOf(r.id).forEach((p) => {
        const p0 = a;
        const own = owns(p.id);
        own.forEach((w, i) => put(w, R.wf, p0 + (i + 0.5) * unit));
        a = p0 + wPerson(p.id) * unit;
        put(p, R.person, (p0 + a) / 2);
      });
      if (a === r0) a += unit;
      put(r, R.role, (r0 + a) / 2);
    });
    put(t, R.team, (t0 + a) / 2);
    a += (GAP * unit) / 2;
  });

  const edges = graph.edges.filter((e) => byId[e.source] && byId[e.target] && e.type !== "uses_tool");
  return { placed: Object.values(byId), edges, byId };
}

/** The node, its ancestors up to the org, everything below it, and anything linked by reuse. */
function neighbourhood(id: string, edges: GraphEdge[]) {
  const set = new Set([id]);
  const tree = edges.filter((e) => e.type !== "uses_tool" && e.type !== "reuses");
  let cur = [id];
  while (cur.length) { const nxt = tree.filter((e) => cur.includes(e.target)).map((e) => e.source); nxt.forEach((x) => set.add(x)); cur = nxt; }
  cur = [id];
  while (cur.length) { const nxt = tree.filter((e) => cur.includes(e.source)).map((e) => e.target); nxt.forEach((x) => set.add(x)); cur = nxt; }
  edges.filter((e) => e.type === "reuses" && (set.has(e.source) || set.has(e.target))).forEach((e) => {
    set.add(e.source); set.add(e.target);
    // and the path up from the other end, so you can see whose skill it was
    let up = [e.source, e.target];
    while (up.length) { const nxt = tree.filter((x) => up.includes(x.target)).map((x) => x.source).filter((x) => !set.has(x) || x === "org"); nxt.forEach((x) => set.add(x)); up = nxt.filter((x) => x !== "org"); }
  });
  return set;
}
