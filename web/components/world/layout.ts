import type { Team } from "@/lib/types";

export const TEAM_COLORS: Record<string, string> = {
  sales: "#e0533f", finance: "#2fae9b", support: "#4f86e8", tech: "#7a5cd6",
  marketing: "#ef8a3c", hr: "#e0679f", procurement: "#86b63a",
};
const FALLBACK = ["#c9733a", "#3c9bc4", "#b0568f", "#5c9e52", "#8a6fd1", "#d1a13a"];

export function teamColor(key: string, i = 0) {
  return TEAM_COLORS[key] ?? FALLBACK[i % FALLBACK.length];
}

export type Plot = { key: string; x: number; z: number; floors: number; color: string; face: number };
export type WorldLayout = {
  radius: number;
  plots: Plot[];
  pathTiles: Set<string>;
  trees: [number, number][];
  route: (from: string, to: string) => [number, number, number][];
};

const RING = 7;

export function buildLayout(teams: Team[]): WorldLayout {
  const radius = 12;
  const plots: Plot[] = teams.map((t, i) => {
    const n = Math.max(1, teams.length);
    const a = ((i + 0.5) / n) * Math.PI * 2 - Math.PI * 0.75;
    const x = Math.round(Math.cos(a) * RING);
    const z = Math.round(Math.sin(a) * RING);
    return {
      key: t.key, x, z, color: teamColor(t.key, i),
      floors: Math.min(5, 1 + Math.ceil(t.headcount / 3)),
      // front faces the HQ
      face: Math.atan2(-x, -z),
    };
  });

  const pathTiles = new Set<string>();
  const lpath = (p: Plot) => {
    const pts: [number, number][] = [];
    const sx = Math.sign(p.x), sz = Math.sign(p.z);
    for (let x = 0; x !== p.x; x += sx || 1) { pts.push([x, 0]); if (!sx) break; }
    for (let z = 0; z !== p.z; z += sz || 1) { pts.push([p.x, z]); if (!sz) break; }
    return pts;
  };
  plots.forEach((p) => lpath(p).forEach(([x, z]) => pathTiles.add(`${x},${z}`)));

  const occupied = (x: number, z: number) =>
    Math.abs(x) <= 2 && Math.abs(z) <= 2 ||
    plots.some((p) => Math.abs(p.x - x) <= 2 && Math.abs(p.z - z) <= 2) ||
    [...pathTiles].some((k) => { const [px, pz] = k.split(",").map(Number); return Math.abs(px - x) <= 1 && Math.abs(pz - z) <= 1; });

  const trees: [number, number][] = [];
  let seed = 7 + teams.length * 13;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  for (let i = 0; i < 600 && trees.length < 9; i++) {
    const a = rnd() * Math.PI * 2, d = radius - 1.5 - rnd() * 1.5;
    const x = Math.round(Math.cos(a) * d), z = Math.round(Math.sin(a) * d);
    if (x + z > -3) continue; // camera looks from +x+z: keep trees behind everything
    if (occupied(x, z) || trees.some(([tx, tz]) => Math.abs(tx - x) < 3 && Math.abs(tz - z) < 3)) continue;
    trees.push([x, z]);
  }

  const byKey = Object.fromEntries(plots.map((p) => [p.key, p]));
  const route = (from: string, to: string): [number, number, number][] => {
    const a = byKey[from], b = byKey[to];
    if (!a || !b) return [];
    const y = 0.9;
    return [[a.x, y, a.z], [a.x, y, 0], [0, y, 0], [b.x, y, 0], [b.x, y, b.z]];
  };

  return { radius, plots, pathTiles, trees, route };
}
