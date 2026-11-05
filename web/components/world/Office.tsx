"use client";
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { Team } from "@/lib/types";
import { MAX_DESKS_PER_ROLE, peopleOf, type Person } from "@/lib/people";
import * as tex from "./textures";

/* ---------------- layout ---------------- */

const DESK_DX = 1.8;   // desk spacing along a row
const ROW_DZ = 2.8;    // distance between role rows
const AISLE = 2.4;     // left aisle that holds the role signs

export type OfficeLayout = {
  W: number; D: number;
  desks: { person: Person; x: number; z: number }[];
  rows: { role: string; name: string; z: number; x0: number; x1: number; extra: number; tint: string }[];
};

export function buildOffice(team: Team, color: string): OfficeLayout {
  const people = peopleOf(team);
  const cols = Math.max(2, ...team.roles.map((r) => Math.min(MAX_DESKS_PER_ROLE, Math.max(1, r.headcount))));
  const W = Math.max(9, AISLE + cols * DESK_DX + 1.6);
  const D = Math.max(7, team.roles.length * ROW_DZ + 2.6);
  const left = -W / 2 + AISLE + DESK_DX / 2;
  const desks: OfficeLayout["desks"] = [];
  const rows: OfficeLayout["rows"] = [];
  team.roles.forEach((r, i) => {
    const z = -D / 2 + 2.1 + i * ROW_DZ;
    const mine = people.filter((p) => p.role === r.key);
    mine.forEach((p, k) => desks.push({ person: p, x: left + k * DESK_DX, z }));
    rows.push({
      role: r.key, name: r.name, z, x0: left - DESK_DX / 2 - 0.2, x1: left + (mine.length - 0.5) * DESK_DX + 0.2,
      extra: Math.max(0, r.headcount - mine.length), tint: tint(color, i),
    });
  });
  return { W, D, desks, rows };
}

function tint(hex: string, i: number) {
  const c = new THREE.Color(hex);
  c.offsetHSL(0, -0.15, 0.22 + (i % 2) * 0.06);
  return `#${c.getHexString()}`;
}

/* ---------------- office ---------------- */

type OfficeProps = {
  team: Team; color: string; layout: OfficeLayout; active: boolean; activePerson: string | null;
  selectedPerson: string | null; skilled: Record<string, number>;
  onSelectPerson: (id: string) => void;
};

export default function Office({ team, color, layout, active, activePerson, selectedPerson, skilled, onSelectPerson }: OfficeProps) {
  const { W, D } = layout;
  const floor = useMemo(() => new THREE.MeshLambertMaterial({ map: tex.planks("#c9a06a", [W / 2, D / 2]) }), [W, D]);
  const wall = useMemo(() => new THREE.MeshLambertMaterial({ map: tex.planks(color, [W / 2, 2]) }), [color, W]);
  const wallSide = useMemo(() => new THREE.MeshLambertMaterial({ map: tex.planks(color, [D / 2, 2]) }), [color, D]);
  const stone = useMemo(() => new THREE.MeshLambertMaterial({ map: tex.stoneBricks([W / 2, 1]) }), [W]);
  const win = useMemo(() => new THREE.MeshLambertMaterial({ map: tex.windowTex(), emissive: "#ffd36b", emissiveIntensity: 0.25 }), []);
  const H = 2.6;
  const winsBack = Math.max(2, Math.floor(W / 3));
  const winsSide = Math.max(2, Math.floor(D / 3));

  useEffect(() => () => { document.body.style.cursor = ""; }, []);
  // shared by every desk so only a few people are up and about at once
  const crowd = useRef({ away: 0, max: Math.max(1, Math.round(layout.desks.length / 4)) });
  crowd.current.max = Math.max(1, Math.round(layout.desks.length / 4));
  const seats = useMemo(() => layout.desks.map((d) => [d.x, d.z] as Pt), [layout]);
  const boardX = W / 2 - 2.4;

  return (
    <group>
      {/* floor slab with a stone foundation, like a diorama */}
      <mesh position={[0, -0.25, 0]} material={floor} receiveShadow><boxGeometry args={[W, 0.5, D]} /></mesh>
      <mesh position={[0, -0.9, 0]} material={stone}><boxGeometry args={[W - 0.4, 0.8, D - 0.4]} /></mesh>

      {/* two back walls (cutaway: the walls facing the camera are open) */}
      <mesh position={[0, H / 2, -D / 2 - 0.15]} material={wall} castShadow receiveShadow><boxGeometry args={[W + 0.3, H, 0.3]} /></mesh>
      <mesh position={[-W / 2 - 0.15, H / 2, 0]} material={wallSide} castShadow receiveShadow><boxGeometry args={[0.3, H, D]} /></mesh>
      <mesh position={[0, H + 0.1, -D / 2 - 0.15]}><boxGeometry args={[W + 0.5, 0.2, 0.5]} /><meshLambertMaterial color="#5a4a42" /></mesh>
      <mesh position={[-W / 2 - 0.15, H + 0.1, 0]}><boxGeometry args={[0.5, 0.2, D + 0.3]} /><meshLambertMaterial color="#5a4a42" /></mesh>
      {Array.from({ length: winsBack }).map((_, i) => (
        <mesh key={`wb${i}`} position={[-W / 2 + ((i + 0.5) * W) / winsBack, 1.55, -D / 2 + 0.01]} material={win}>
          <planeGeometry args={[1.1, 0.9]} />
        </mesh>
      ))}
      {Array.from({ length: winsSide }).map((_, i) => (
        <mesh key={`ws${i}`} position={[-W / 2 + 0.01, 1.55, -D / 2 + ((i + 0.5) * D) / winsSide]} rotation={[0, Math.PI / 2, 0]} material={win}>
          <planeGeometry args={[1.1, 0.9]} />
        </mesh>
      ))}

      {/* department board on the back wall */}
      <Html position={[-W / 2 + AISLE + 1.2, 2.25, -D / 2 + 0.05]} center zIndexRange={[9, 0]}>
        <div className="sign sign-office" style={{ ["--team" as any]: color }}>
          <b>{team.name}</b>
          <span>{team.headcount} people · {team.roles.length} roles</span>
          {active && <em className="sign-live">agent working…</em>}
        </div>
      </Html>

      {/* role rows: a rug + a floor sign in the aisle */}
      {layout.rows.map((r) => (
        <group key={r.role}>
          <mesh position={[(r.x0 + r.x1) / 2, 0.02, r.z + 0.25]} receiveShadow>
            <boxGeometry args={[r.x1 - r.x0, 0.04, 2.1]} /><meshLambertMaterial color={r.tint} />
          </mesh>
          <Html position={[-W / 2 + 1.15, 0.5, r.z + 0.3]} center zIndexRange={[9, 0]}>
            <div className="rolesign" style={{ ["--team" as any]: color }}>
              {r.name}{r.extra > 0 && <small>+{r.extra}</small>}
            </div>
          </Html>
        </group>
      ))}

      {/* props */}
      <Plant x={-W / 2 + 0.6} z={-D / 2 + 0.6} />
      <Plant x={W / 2 - 0.6} z={-D / 2 + 0.6} />
      <Plant x={-W / 2 + 0.6} z={D / 2 - 0.6} />
      <Cooler x={W / 2 - 0.7} z={-D / 2 + 1.9} />
      <Whiteboard x={boardX} z={-D / 2} color={color} />

      {layout.desks.map(({ person, x, z }, i) => (
        <Desk key={person.id} x={x} z={z} person={person} color={color} phase={i * 0.7}
              room={{ W, D, boardX }} seats={seats} index={i} crowd={crowd.current}
              skin={SKIN[(i + team.key.length) % SKIN.length]} hair={HAIR[(i * 3 + team.key.length) % HAIR.length]}
              busy={active} working={activePerson === person.id} selected={selectedPerson === person.id}
              skills={skilled[person.id] ?? 0} onClick={() => onSelectPerson(person.id)} />
      ))}
    </group>
  );
}

/* ---------------- a desk with a person who sits, stands and walks around ---------------- */

const SKIN = ["#f1c7a0", "#d9a27a", "#a8724f", "#7c4f34", "#e8b892"];
const HAIR = ["#3a2a1e", "#1b1b1f", "#7a4a22", "#c9a35a", "#5a3a2a", "#8a2f1e", "#2b2b38"];
const deskTop = new THREE.MeshLambertMaterial({ color: "#8a5a34" });
const deskLeg = new THREE.MeshLambertMaterial({ color: "#5a3a22" });
const chairM = new THREE.MeshLambertMaterial({ color: "#2d3550" });
const pants = new THREE.MeshLambertMaterial({ color: "#2f3b55" });
const laptopM = new THREE.MeshLambertMaterial({ color: "#c8ccd6" });
const eyeM = new THREE.MeshBasicMaterial({ color: "#1b1f2a" });
const cupM = new THREE.MeshLambertMaterial({ color: "#ffffff" });

type Pt = [number, number];
type Crowd = { away: number; max: number };
type Errand = "cooler" | "board" | "chat";
type Room = { W: number; D: number; boardX: number };

// Leg segments with their pivot at the top (hip / knee), so one rig can sit, stand and walk.
const THIGH = (() => { const g = new THREE.BoxGeometry(0.22, 0.44, 0.22); g.translate(0, -0.22, 0); return g; })();
const SHIN = (() => { const g = new THREE.BoxGeometry(0.2, 0.48, 0.2); g.translate(0, -0.24, 0); return g; })();
const HIP_SIT = 0.55;
const HIP_STAND = 0.92;
const WALK_SPEED = 1.5; // units / second
const SIDE = 0.7;       // step out beside the chair (the gap between desks is too narrow to pass)
const LANE = 1.15;      // free lane behind each row of chairs

/** Office-space route from a seat to an errand, along the free lanes and the right-hand aisle. */
function planErrand(me: Pt, kind: Errand, room: Room, seats: Pt[], index: number) {
  const [mx, mz] = me;
  const aisle = room.W / 2 - 1.25;
  const lane = (zz: number) => zz - LANE;
  const route: Pt[] = [[mx, mz], [mx + SIDE, mz], [mx + SIDE, lane(mz)]];
  let face = 0;
  if (kind === "cooler") {
    route.push([aisle, lane(mz)], [aisle, -room.D / 2 + 1.9]);
    face = Math.PI / 2;
  } else if (kind === "board") {
    const zz = -room.D / 2 + 0.7;
    route.push([aisle, lane(mz)], [aisle, zz], [room.boardX, zz]);
    face = Math.PI;
  } else {
    const others = seats.filter((_, j) => j !== index);
    const [ox, oz] = others[Math.floor(Math.random() * others.length)];
    const spot: Pt = [ox + 0.6, oz - 0.85];
    if (Math.abs(oz - mz) > 0.1) route.push([aisle, lane(mz)], [aisle, lane(oz)]);
    route.push([spot[0], lane(oz)], spot);
    face = Math.atan2(ox - spot[0], oz - spot[1]);
  }
  // drop points that don't move (e.g. already in the aisle)
  const out: Pt[] = [];
  for (const p of route) {
    const q = out[out.length - 1];
    if (!q || Math.hypot(p[0] - q[0], p[1] - q[1]) > 0.05) out.push(p);
  }
  return { route: out.map(([px, pz]) => [px - mx, pz - mz] as Pt), face }; // desk-local
}

const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
function turn(a: number, b: number, k: number) {
  let d = (b - a) % (Math.PI * 2);
  if (d > Math.PI) d -= Math.PI * 2;
  if (d < -Math.PI) d += Math.PI * 2;
  return a + d * k;
}

function Desk({ x, z, person, color, skin, hair, phase, busy, working, selected, skills, onClick, room, seats, index, crowd }: {
  x: number; z: number; person: Person; color: string; skin: string; hair: string; phase: number;
  busy: boolean; working: boolean; selected: boolean; skills: number; onClick: () => void;
  room: Room; seats: Pt[]; index: number; crowd: Crowd;
}) {
  const [hover, setHover] = useState(false);
  const who = useRef<THREE.Group>(null);   // the whole person: moves around the room
  const upper = useRef<THREE.Group>(null); // torso, arms, head: rides on the hips
  const thighL = useRef<THREE.Group>(null);
  const thighR = useRef<THREE.Group>(null);
  const shinL = useRef<THREE.Group>(null);
  const shinR = useRef<THREE.Group>(null);
  const armL = useRef<THREE.Mesh>(null);
  const armR = useRef<THREE.Mesh>(null);
  const head = useRef<THREE.Mesh>(null);
  const cup = useRef<THREE.Mesh>(null);
  const chair = useRef<THREE.Group>(null);
  const screen = useRef<THREE.MeshBasicMaterial>(null);
  const shirt = useMemo(() => new THREE.MeshLambertMaterial({ color }), [color]);
  const skinM = useMemo(() => new THREE.MeshLambertMaterial({ color: skin }), [skin]);
  const hairM = useMemo(() => new THREE.MeshLambertMaterial({ color: hair }), [hair]);

  const st = useRef({
    mode: "sit" as "sit" | "rise" | "walk" | "do" | "sink",
    next: -1, t0: 0, until: 0, stand: 0, stride: 0,
    route: [[0, 0]] as Pt[], i: 0, back: false, kind: "chat" as Errand, face: 0,
    pos: [0, 0] as Pt, rot: 0, away: false,
  });
  // free the slot if this desk disappears while its person is out
  useEffect(() => () => { if (st.current.away) crowd.away = Math.max(0, crowd.away - 1); }, [crowd]);

  useFrame(({ clock }, delta) => {
    const s = st.current;
    const now = clock.elapsedTime;
    const dt = Math.min(delta, 0.1);
    const t = now + phase;
    const ease = 1 - Math.exp(-dt * 10);
    if (s.next < 0) s.next = now + 3 + Math.random() * 18;

    // walk back to the desk along the way we came
    const goBack = () => {
      const done = s.mode === "walk" ? s.i : s.route.length;
      s.route = [[s.pos[0], s.pos[1]], ...s.route.slice(0, done).reverse()];
      s.i = 1; s.back = true; s.mode = "walk";
    };

    // --- decide what to do ---
    if (s.mode === "sit") {
      if (now > s.next) {
        const free = !working && !selected && crowd.away < crowd.max && seats.length > 0;
        if (free && (!busy || Math.random() < 0.3)) {
          const r = Math.random();
          s.kind = seats.length > 1 && r < 0.45 ? "chat" : r < 0.8 ? "cooler" : "board";
          const plan = planErrand([x, z], s.kind, room, seats, index);
          s.route = plan.route; s.face = plan.face; s.i = 1; s.back = false;
          s.mode = "rise"; s.t0 = now; s.away = true; crowd.away++;
        } else {
          s.next = now + 3 + Math.random() * 6;
        }
      }
    } else if (s.mode === "rise") {
      s.stand = Math.min(1, (now - s.t0) / 0.45);
      if (working) { s.mode = "sink"; s.t0 = now - (1 - s.stand) * 0.45; }
      else if (s.stand >= 1) s.mode = "walk";
    } else if (s.mode === "walk") {
      if (working && !s.back) goBack();
      const [tx, tz] = s.route[s.i];
      const dx = tx - s.pos[0], dz = tz - s.pos[1];
      const d = Math.hypot(dx, dz);
      const step = WALK_SPEED * (working ? 1.6 : 1) * dt;
      if (d > 1e-3) s.rot = turn(s.rot, Math.atan2(dx, dz), 1 - Math.exp(-dt * 14));
      if (d <= step) {
        s.pos = [tx, tz]; s.i++;
        if (s.i >= s.route.length) {
          if (s.back) { s.mode = "sink"; s.t0 = now; }
          else { s.mode = "do"; s.until = now + 3 + Math.random() * 4; }
        }
      } else {
        s.pos = [s.pos[0] + (dx / d) * step, s.pos[1] + (dz / d) * step];
      }
      s.stride += step * 5.5;
    } else if (s.mode === "do") {
      s.rot = turn(s.rot, s.face, ease);
      if (working || now > s.until) goBack();
    } else if (s.mode === "sink") {
      s.rot = turn(s.rot, 0, ease);
      s.stand = Math.max(0, 1 - (now - s.t0) / 0.45);
      if (s.stand <= 0) {
        s.mode = "sit"; s.pos = [0, 0]; s.rot = 0; s.stride = 0;
        if (s.away) { s.away = false; crowd.away = Math.max(0, crowd.away - 1); }
        s.next = now + 10 + Math.random() * 25;
      }
    }

    // --- pose ---
    const walking = s.mode === "walk";
    const k = s.stand;
    const swing = walking ? Math.sin(s.stride) : 0;
    const hip = lerp(HIP_SIT, HIP_STAND, k) + (walking ? Math.abs(Math.cos(s.stride)) * 0.04 : 0);
    if (who.current) { who.current.position.set(s.pos[0], 0, s.pos[1]); who.current.rotation.y = s.rot; }
    if (upper.current) upper.current.position.y = hip + (working && s.mode === "sit" ? Math.abs(Math.sin(t * 8)) * 0.05 : 0);
    if (thighL.current) { thighL.current.position.y = hip; thighL.current.rotation.x = lerp(-Math.PI / 2, 0, k) - swing * 0.55; }
    if (thighR.current) { thighR.current.position.y = hip; thighR.current.rotation.x = lerp(-Math.PI / 2, 0, k) + swing * 0.55; }
    if (shinL.current) shinL.current.rotation.x = lerp(Math.PI / 2, 0, k) + Math.max(0, swing) * 0.7;
    if (shinR.current) shinR.current.rotation.x = lerp(Math.PI / 2, 0, k) + Math.max(0, -swing) * 0.7;
    if (chair.current) chair.current.position.z = lerp(chair.current.position.z, s.mode === "sit" ? 0 : -0.25, ease);

    // arms: type at the desk, swing when walking, and act out the errand
    let aL = 0, aR = 0, nod = 0;
    if (s.mode === "sit" || k < 0.5) {
      const speed = working ? 22 : busy ? 12 : 5;
      const amp = working ? 0.25 : busy ? 0.14 : 0.06;
      aL = -1.25 + Math.sin(t * speed) * amp;
      aR = -1.25 + Math.sin(t * speed + 1.7) * amp;
    } else if (walking) {
      aL = swing * 0.5;
      aR = s.back && s.kind === "cooler" ? -1.1 : -swing * 0.5; // carrying the cup back
    } else if (s.mode === "do") {
      if (s.kind === "cooler") aR = -1.5 - Math.max(0, Math.sin(t * 1.3)) * 0.9;
      else if (s.kind === "board") { aR = -2.5 + Math.sin(t * 6) * 0.2; aL = -0.2; }
      else { aL = -0.7 + Math.sin(t * 3.1) * 0.35; aR = -0.7 + Math.sin(t * 2.3 + 1) * 0.35; nod = Math.sin(t * 4) * 0.08; }
    }
    if (armL.current) armL.current.rotation.x = lerp(armL.current.rotation.x, aL, ease);
    if (armR.current) {
      armR.current.rotation.x = lerp(armR.current.rotation.x, aR, ease);
      armR.current.rotation.z = s.mode === "do" && s.kind === "board" ? Math.sin(t * 3) * 0.25 : 0;
    }
    if (head.current) {
      head.current.rotation.y = s.mode === "sit" ? Math.sin(t * 0.4) * (working ? 0.05 : 0.25) : 0;
      head.current.rotation.x = nod;
    }
    if (cup.current) cup.current.visible = s.kind === "cooler" && (s.mode === "do" || (walking && s.back));
    if (screen.current) {
      const flick = working ? 0.75 + Math.sin(t * 30) * 0.25 : busy ? 0.85 : s.mode === "sit" ? 0.65 : 0.4;
      screen.current.color.setRGB(0.55 * flick, 0.85 * flick, 1 * flick);
    }
  });

  const over = (e: any) => { e.stopPropagation(); setHover(true); document.body.style.cursor = "pointer"; };
  const out = () => { setHover(false); document.body.style.cursor = ""; };

  return (
    <group position={[x, 0, z]} onClick={(e) => { e.stopPropagation(); onClick(); }} onPointerOver={over} onPointerOut={out}>
      {/* chair: slides back a little when its person gets up */}
      <group ref={chair}>
        <mesh position={[0, 0.42, -0.05]} material={chairM} castShadow><boxGeometry args={[0.62, 0.1, 0.6]} /></mesh>
        <mesh position={[0, 0.85, -0.38]} material={chairM} castShadow><boxGeometry args={[0.62, 0.8, 0.1]} /></mesh>
        <mesh position={[0, 0.2, -0.05]} material={deskLeg}><boxGeometry args={[0.1, 0.4, 0.1]} /></mesh>
      </group>

      {/* desk in front of the person */}
      <mesh position={[0, 0.76, 0.62]} material={deskTop} castShadow receiveShadow><boxGeometry args={[1.45, 0.08, 0.72]} /></mesh>
      {[-0.65, 0.65].map((lx) => (
        <mesh key={lx} position={[lx, 0.36, 0.62]} material={deskLeg} castShadow><boxGeometry args={[0.08, 0.72, 0.66]} /></mesh>
      ))}
      {/* laptop: keyboard + screen facing the person */}
      <mesh position={[0, 0.82, 0.52]} material={laptopM}><boxGeometry args={[0.6, 0.04, 0.38]} /></mesh>
      <group position={[0, 0.82, 0.72]} rotation={[0.25, 0, 0]}>
        <mesh position={[0, 0.2, 0]} material={laptopM} castShadow><boxGeometry args={[0.6, 0.4, 0.04]} /></mesh>
        <mesh position={[0, 0.2, -0.025]} rotation={[0, Math.PI, 0]}>
          <planeGeometry args={[0.52, 0.32]} /><meshBasicMaterial ref={screen} color="#8cd9ff" />
        </mesh>
        <mesh position={[0, 0.2, 0.025]}><planeGeometry args={[0.1, 0.1]} /><meshBasicMaterial color={color} /></mesh>
      </group>
      {/* a mug and a few papers so desks feel lived in */}
      <mesh position={[0.5, 0.86, 0.5]}><boxGeometry args={[0.12, 0.14, 0.12]} /><meshLambertMaterial color={person.seat % 2 ? "#e0533f" : "#ffffff"} /></mesh>
      <mesh position={[-0.48, 0.81, 0.66]} rotation={[0, 0.2, 0]}><boxGeometry args={[0.3, 0.02, 0.36]} /><meshLambertMaterial color="#fffaf0" /></mesh>

      {/* the person (faces +z): hips on the seat when sitting, legs straighten to stand */}
      <group ref={who}>
        <group ref={thighL} position={[-0.13, HIP_SIT, -0.1]} rotation={[-Math.PI / 2, 0, 0]}>
          <mesh geometry={THIGH} material={pants} castShadow />
          <group ref={shinL} position={[0, -0.44, 0]} rotation={[Math.PI / 2, 0, 0]}><mesh geometry={SHIN} material={pants} castShadow /></group>
        </group>
        <group ref={thighR} position={[0.13, HIP_SIT, -0.1]} rotation={[-Math.PI / 2, 0, 0]}>
          <mesh geometry={THIGH} material={pants} castShadow />
          <group ref={shinR} position={[0, -0.44, 0]} rotation={[Math.PI / 2, 0, 0]}><mesh geometry={SHIN} material={pants} castShadow /></group>
        </group>
        <group ref={upper} position={[0, HIP_SIT, 0]}>
          <mesh position={[0, 0.43, -0.12]} material={shirt} castShadow><boxGeometry args={[0.5, 0.64, 0.28]} /></mesh>
          <mesh ref={armL} position={[-0.34, 0.65, -0.08]} rotation={[-1.25, 0, 0]} material={shirt} castShadow geometry={ARM} />
          <mesh ref={armR} position={[0.34, 0.65, -0.08]} rotation={[-1.25, 0, 0]} material={shirt} castShadow geometry={ARM}>
            <mesh ref={cup} position={[0, -0.6, 0.06]} material={cupM} visible={false}><boxGeometry args={[0.13, 0.16, 0.13]} /></mesh>
          </mesh>
          <mesh ref={head} position={[0, 1.01, -0.12]} material={skinM} castShadow>
            <boxGeometry args={[0.48, 0.48, 0.48]} />
            <mesh position={[0, 0.28, 0]} material={hairM}><boxGeometry args={[0.52, 0.12, 0.52]} /></mesh>
            <mesh position={[0, 0.1, -0.25]} material={hairM}><boxGeometry args={[0.52, 0.3, 0.06]} /></mesh>
            <mesh position={[-0.1, 0.02, 0.245]} material={eyeM}><planeGeometry args={[0.07, 0.07]} /></mesh>
            <mesh position={[0.1, 0.02, 0.245]} material={eyeM}><planeGeometry args={[0.07, 0.07]} /></mesh>
          </mesh>
        </group>

        {skills > 0 && <SkillBook phase={phase} y={2.4} />}
        {(selected || hover) && (
          <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.05, 0.1]}>
            <ringGeometry args={[0.6, 0.72, 4, 1, Math.PI / 4]} />
            <meshBasicMaterial color={selected ? "#ffffff" : "#ffe27a"} />
          </mesh>
        )}
        {(selected || hover || working) && (
          <Html position={[0, skills > 0 ? 2.9 : 2.45, 0]} center zIndexRange={[11, 10]}>
            <div className={`nametag ${selected ? "nametag-on" : ""}`} onClick={onClick}>
              {person.name}<small>{person.roleName}{skills > 0 ? ` · 📘${skills}` : ""}</small>
            </div>
          </Html>
        )}
      </group>
    </group>
  );
}

function Whiteboard({ x, z, color }: { x: number; z: number; color: string }) {
  return (
    <group position={[x, 0, z + 0.06]}>
      <mesh position={[0, 1.45, 0]}><boxGeometry args={[1.7, 1.05, 0.06]} /><meshLambertMaterial color="#9aa3b2" /></mesh>
      <mesh position={[0, 1.45, 0.035]}><planeGeometry args={[1.56, 0.92]} /><meshLambertMaterial color="#fbfcff" /></mesh>
      {/* scribbles: a little bar chart and some notes */}
      {[0.18, 0.32, 0.5, 0.26].map((h, i) => (
        <mesh key={i} position={[-0.55 + i * 0.16, 1.1 + h / 2, 0.04]}><planeGeometry args={[0.1, h]} /><meshBasicMaterial color={color} /></mesh>
      ))}
      {[0, 1, 2].map((i) => (
        <mesh key={`l${i}`} position={[0.35, 1.7 - i * 0.17, 0.04]}><planeGeometry args={[0.6 - i * 0.12, 0.04]} /><meshBasicMaterial color="#3a4a6a" /></mesh>
      ))}
      <mesh position={[0, 0.88, 0.06]}><boxGeometry args={[1.5, 0.05, 0.1]} /><meshLambertMaterial color="#9aa3b2" /></mesh>
    </group>
  );
}

function Plant({ x, z }: { x: number; z: number }) {
  const leaf = useMemo(() => new THREE.MeshLambertMaterial({ map: tex.leaves() }), []);
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.25, 0]} castShadow><boxGeometry args={[0.5, 0.5, 0.5]} /><meshLambertMaterial color="#b0603a" /></mesh>
      <mesh position={[0, 0.85, 0]} material={leaf} castShadow><boxGeometry args={[0.7, 0.7, 0.7]} /></mesh>
      <mesh position={[0, 1.35, 0]} material={leaf} castShadow><boxGeometry args={[0.4, 0.4, 0.4]} /></mesh>
    </group>
  );
}

function Cooler({ x, z }: { x: number; z: number }) {
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.5, 0]} castShadow><boxGeometry args={[0.5, 1, 0.5]} /><meshLambertMaterial color="#eef1f6" /></mesh>
      <mesh position={[0, 1.25, 0]} castShadow><boxGeometry args={[0.4, 0.5, 0.4]} /><meshLambertMaterial color="#7cc8ff" transparent opacity={0.8} /></mesh>
    </group>
  );
}

/* ---------------- shared bits ---------------- */

/** A small floating book above someone who has learned a recorded skill. */
export function SkillBook({ phase, y = 2.05 }: { phase: number; y?: number }) {
  const g = useRef<THREE.Group>(null);
  useFrame(({ clock }) => {
    if (!g.current) return;
    const t = clock.elapsedTime + phase;
    g.current.position.y = y + Math.sin(t * 2.4) * 0.08;
    g.current.rotation.y = t * 1.2;
  });
  return (
    <group ref={g} position={[0, y, 0]}>
      <mesh><boxGeometry args={[0.42, 0.1, 0.32]} /><meshLambertMaterial color="#5b3fd6" emissive="#2a1680" emissiveIntensity={0.4} /></mesh>
      <mesh position={[0.02, 0, 0]}><boxGeometry args={[0.36, 0.07, 0.3]} /><meshLambertMaterial color="#fff6dc" /></mesh>
      <mesh position={[0, 0.06, 0]}><boxGeometry args={[0.42, 0.03, 0.32]} /><meshLambertMaterial color="#5b3fd6" /></mesh>
    </group>
  );
}

export const ARM = (() => {
  const g = new THREE.BoxGeometry(0.2, 0.6, 0.22);
  g.translate(0, -0.25, 0); // pivot at the shoulder
  return g;
})();
