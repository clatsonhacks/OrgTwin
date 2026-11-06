"use client";
import { useEffect, useState } from "react";
import { api } from "./api";

export type Notify = { slack: boolean; discord: boolean; slack_id: string; discord_id: string };
export type Person = {
  id: number; name: string; email: string; role: "admin" | "member"; departments: string[]; notify: Partial<Notify>;
  joined: boolean; token?: string; invite_url?: string;
};
export type Me = { people_mode: boolean; setup_needed: boolean; user: Person | null; admin: boolean; can_edit: string[] };
export type Quest = { key: string; title: string; done: boolean; hint: string };

// one shared copy of "who am I", like the connectors list
let cache: Me | null = null;
let inflight: Promise<Me> | null = null;
let seq = 0;  // a newer load (e.g. right after signing in) wins over an older one still in flight
const listeners = new Set<(m: Me) => void>();

export function loadMe(force = false): Promise<Me> {
  if (cache && !force) return Promise.resolve(cache);
  if (!inflight || force) {
    const mine = ++seq;
    inflight = api<Me>("/api/me").then((m) => {
      if (mine !== seq) return cache ?? m;
      cache = m; inflight = null; listeners.forEach((f) => f(m)); return m;
    }).catch((e) => { if (mine === seq) inflight = null; throw e; });
  }
  return inflight;
}

export function useMe(): Me | null {
  const [me, setMe] = useState<Me | null>(cache);
  useEffect(() => {
    listeners.add(setMe);
    loadMe().catch(() => {});
    return () => { listeners.delete(setMe); };
  }, []);
  return me;
}

/** May this person create / edit skills in this department? (Everyone can before an admin exists.) */
export function canEdit(me: Me | null, team: string): boolean {
  if (!me || !me.people_mode) return true;
  return me.admin || me.can_edit.includes("*") || me.can_edit.includes(team);
}

/** Signed in at all (or no people mode yet). */
export function canAct(me: Me | null): boolean {
  return !me || !me.people_mode || !!me.user || me.admin;
}
