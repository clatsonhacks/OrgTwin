"use client";
import { useEffect, useState } from "react";
import { api } from "./api";

export type ConnectorParam = { name: string; default: string; label: string; long: boolean };
export type ConnectorAction = { key: string; label: string; doc: string; external: boolean; params: ConnectorParam[] };
export type Connector = {
  key: string; name: string; icon: string; category: string; mode: "live" | "mock";
  env: string[]; setup: string; actions: ConnectorAction[]; core?: boolean;
};

// One fetch shared by every component (step editor, connectors panel).
let cache: Connector[] | null = null;
let inflight: Promise<Connector[]> | null = null;
const listeners = new Set<(c: Connector[]) => void>();

export function loadConnectors(force = false): Promise<Connector[]> {
  if (cache && !force) return Promise.resolve(cache);
  if (!inflight || force) {
    inflight = api<Connector[]>("/api/connectors").then((c) => {
      cache = c; inflight = null; listeners.forEach((f) => f(c)); return c;
    }).catch((e) => { inflight = null; throw e; });
  }
  return inflight;
}

export function useConnectors(): Connector[] {
  const [list, setList] = useState<Connector[]>(cache ?? []);
  useEffect(() => {
    listeners.add(setList);
    loadConnectors().catch(() => {});
    return () => { listeners.delete(setList); };
  }, []);
  return list;
}

/** Connector tools the generic engine can run (those with actions). */
export function connectorTools(list: Connector[]) {
  return Object.fromEntries(list.filter((c) => c.actions.length).map((c) => [c.key, c]));
}
