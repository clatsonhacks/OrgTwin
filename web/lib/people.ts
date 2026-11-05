import type { Org, Team, Workflow } from "./types";

/** A person sitting at a desk inside a department. One role with headcount 3 → 3 people. */
export type Person = {
  id: string;        // unique inside the team: `${roleKey}#${seat}`
  team: string;
  role: string;
  roleName: string;
  name: string;
  seat: number;
};

/** Desks drawn per role; extra headcount is shown as "+N" on the role sign. */
export const MAX_DESKS_PER_ROLE = 6;

const FIRST = [
  "Maya", "Leo", "Aisha", "Tom", "Priya", "Jonas", "Sofia", "Kenji", "Elena", "Omar", "Grace", "Mateo",
  "Nina", "Ravi", "Chloe", "Sam", "Lina", "Diego", "Hana", "Ben", "Zara", "Felix", "Iris", "Kwame",
  "Ana", "Yusuf", "Mila", "Theo", "Noor", "Luca", "Ivy", "Arjun", "Ella", "Marco", "Sana", "Hugo",
];
const LAST = ["K.", "R.", "M.", "S.", "T.", "L.", "P.", "D.", "B.", "W.", "N.", "O.", "H.", "C.", "V.", "A."];

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

export function peopleOf(team: Team): Person[] {
  const used = new Set<string>();
  const out: Person[] = [];
  team.roles.forEach((r) => {
    const n = Math.max(1, Math.min(MAX_DESKS_PER_ROLE, r.headcount || 1));
    for (let seat = 0; seat < n; seat++) {
      let h = hash(`${team.key}/${r.key}/${seat}`);
      let name = "";
      for (let tries = 0; tries < FIRST.length; tries++, h = hash(`${h}`)) {
        name = `${FIRST[h % FIRST.length]} ${LAST[(h >>> 8) % LAST.length]}`;
        if (!used.has(name)) break;
      }
      used.add(name);
      out.push({ id: `${r.key}#${seat}`, team: team.key, role: r.key, roleName: r.name, name, seat });
    }
  });
  return out;
}

/** Which desk owns a workflow. Older workflows without an assignee belong to the role's first desk. */
export function ownerOf(w: Workflow): string {
  const p = w.meta?.person;
  return typeof p === "string" && p.startsWith(`${w.role_key}#`) ? p : `${w.role_key}#0`;
}

export function findPerson(org: Org | null, team: string, id: string | null): Person | null {
  const t = org?.teams.find((x) => x.key === team);
  return t && id ? peopleOf(t).find((p) => p.id === id) ?? null : null;
}
