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

      {layout.desks.map(({ person, x, z }, i) => (
        <Desk key={person.id} x={x} z={z} person={person} color={color} phase={i * 0.7}
              skin={SKIN[(i + team.key.length) % SKIN.length]} hair={HAIR[(i * 3 + team.key.length) % HAIR.length]}
              busy={active} working={activePerson === person.id} selected={selectedPerson === person.id}
              skills={skilled[person.id] ?? 0} onClick={() => onSelectPerson(person.id)} />
      ))}
    </group>
  );
}

/* ---------------- a desk with a seated person ---------------- */

const SKIN = ["#f1c7a0", "#d9a27a", "#a8724f", "#7c4f34", "#e8b892"];
const HAIR = ["#3a2a1e", "#1b1b1f", "#7a4a22", "#c9a35a", "#5a3a2a", "#8a2f1e", "#2b2b38"];
const deskTop = new THREE.MeshLambertMaterial({ color: "#8a5a34" });
const deskLeg = new THREE.MeshLambertMaterial({ color: "#5a3a22" });
const chairM = new THREE.MeshLambertMaterial({ color: "#2d3550" });
const pants = new THREE.MeshLambertMaterial({ color: "#2f3b55" });
const laptopM = new THREE.MeshLambertMaterial({ color: "#c8ccd6" });
const eyeM = new THREE.MeshBasicMaterial({ color: "#1b1f2a" });

function Desk({ x, z, person, color, skin, hair, phase, busy, working, selected, skills, onClick }: {
  x: number; z: number; person: Person; color: string; skin: string; hair: string; phase: number;
  busy: boolean; working: boolean; selected: boolean; skills: number; onClick: () => void;
}) {
  const [hover, setHover] = useState(false);
  const body = useRef<THREE.Group>(null);
  const armL = useRef<THREE.Mesh>(null);
  const armR = useRef<THREE.Mesh>(null);
  const head = useRef<THREE.Mesh>(null);
  const screen = useRef<THREE.MeshBasicMaterial>(null);
  const shirt = useMemo(() => new THREE.MeshLambertMaterial({ color }), [color]);
  const skinM = useMemo(() => new THREE.MeshLambertMaterial({ color: skin }), [skin]);
  const hairM = useMemo(() => new THREE.MeshLambertMaterial({ color: hair }), [hair]);

  useFrame(({ clock }) => {
    const t = clock.elapsedTime + phase;
    const speed = working ? 22 : busy ? 12 : 5;
    const amp = working ? 0.25 : busy ? 0.14 : 0.06;
    // arms point forward to the keyboard and tap
    if (armL.current) armL.current.rotation.x = -1.25 + Math.sin(t * speed) * amp;
    if (armR.current) armR.current.rotation.x = -1.25 + Math.sin(t * speed + 1.7) * amp;
    if (body.current) body.current.position.y = working ? Math.abs(Math.sin(t * 8)) * 0.05 : 0;
    if (head.current) head.current.rotation.y = Math.sin(t * 0.4) * (working ? 0.05 : 0.25);
    if (screen.current) {
      const flick = working ? 0.75 + Math.sin(t * 30) * 0.25 : busy ? 0.85 : 0.65;
      screen.current.color.setRGB(0.55 * flick, 0.85 * flick, 1 * flick);
    }
  });

  const over = (e: any) => { e.stopPropagation(); setHover(true); document.body.style.cursor = "pointer"; };
  const out = () => { setHover(false); document.body.style.cursor = ""; };

  return (
    <group position={[x, 0, z]} onClick={(e) => { e.stopPropagation(); onClick(); }} onPointerOver={over} onPointerOut={out}>
      {/* chair */}
      <mesh position={[0, 0.42, -0.05]} material={chairM} castShadow><boxGeometry args={[0.62, 0.1, 0.6]} /></mesh>
      <mesh position={[0, 0.85, -0.38]} material={chairM} castShadow><boxGeometry args={[0.62, 0.8, 0.1]} /></mesh>
      <mesh position={[0, 0.2, -0.05]} material={deskLeg}><boxGeometry args={[0.1, 0.4, 0.1]} /></mesh>

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

      {/* seated person, facing +z toward the desk */}
      <group ref={body}>
        {/* thighs on the seat, shins down */}
        <mesh position={[-0.13, 0.55, 0.12]} material={pants} castShadow><boxGeometry args={[0.22, 0.2, 0.5]} /></mesh>
        <mesh position={[0.13, 0.55, 0.12]} material={pants} castShadow><boxGeometry args={[0.22, 0.2, 0.5]} /></mesh>
        <mesh position={[-0.13, 0.25, 0.32]} material={pants}><boxGeometry args={[0.2, 0.46, 0.2]} /></mesh>
        <mesh position={[0.13, 0.25, 0.32]} material={pants}><boxGeometry args={[0.2, 0.46, 0.2]} /></mesh>
        {/* torso, arms, head */}
        <mesh position={[0, 0.98, -0.12]} material={shirt} castShadow><boxGeometry args={[0.5, 0.64, 0.28]} /></mesh>
        <mesh ref={armL} position={[-0.34, 1.2, -0.08]} material={shirt} castShadow geometry={ARM} />
        <mesh ref={armR} position={[0.34, 1.2, -0.08]} material={shirt} castShadow geometry={ARM} />
        <mesh ref={head} position={[0, 1.56, -0.12]} material={skinM} castShadow><boxGeometry args={[0.48, 0.48, 0.48]} /></mesh>
        <mesh position={[0, 1.84, -0.12]} material={hairM}><boxGeometry args={[0.52, 0.12, 0.52]} /></mesh>
        <mesh position={[0, 1.66, -0.37]} material={hairM}><boxGeometry args={[0.52, 0.3, 0.06]} /></mesh>
        <mesh position={[-0.1, 1.58, 0.125]} material={eyeM}><planeGeometry args={[0.07, 0.07]} /></mesh>
        <mesh position={[0.1, 1.58, 0.125]} material={eyeM}><planeGeometry args={[0.07, 0.07]} /></mesh>
      </group>

      {skills > 0 && <SkillBook phase={phase} y={2.25} />}
      {(selected || hover) && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.05, 0.25]}>
          <ringGeometry args={[0.85, 0.98, 4, 1, Math.PI / 4]} />
          <meshBasicMaterial color={selected ? "#ffffff" : "#ffe27a"} />
        </mesh>
      )}
      {(selected || hover || working) && (
        <Html position={[0, skills > 0 ? 2.75 : 2.3, 0]} center zIndexRange={[11, 10]}>
          <div className={`nametag ${selected ? "nametag-on" : ""}`} onClick={onClick}>
            {person.name}<small>{person.roleName}{skills > 0 ? ` · 📘${skills}` : ""}</small>
          </div>
        </Html>
      )}
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
