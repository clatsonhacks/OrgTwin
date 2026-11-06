export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

function token(): string {
  try { return localStorage.getItem("orgtwin_token") ?? ""; } catch { return ""; }
}
export function setToken(t: string) {
  try { localStorage.setItem("orgtwin_token", t); } catch { /* storage unavailable */ }
}

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Access-Token": token(), ...(init.headers || {}) },
  });
  if (!res.ok) {
    let msg = `${res.status}`;
    try { msg = (await res.json()).detail ?? msg; } catch { /* not json */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T = any>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body) });

/** automatable hours/week → color from calm teal to hot orange-red */
export function heatColor(hours: number, max: number): string {
  const t = max > 0 ? Math.min(1, hours / max) : 0;
  const stops = [[45, 212, 191], [250, 204, 21], [249, 115, 22], [239, 68, 68]];
  const x = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  const c = stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

export const TOOL_ICON: Record<string, string> = {
  sheets: "📊", sandbox: "🧪", llm: "🧠", email: "✉️", stripe: "🧾", slack: "💬",
  approval: "✋", a2a: "📦", mcp: "⚙️", http: "🌐",
};
export const TOOL_NAME: Record<string, string> = {
  llm: "AI writes", sheets: "Spreadsheet", email: "Email", slack: "Team chat", approval: "Human approval",
  sandbox: "Sandbox code", stripe: "Stripe", a2a: "Hand to agent", mcp: "MCP tool", http: "HTTP call",
};
export const ACTIONS: Record<string, { key: string; label: string }[]> = {
  sheets: [{ key: "read_rows", label: "read rows" }, { key: "update_row", label: "update row" }],
  llm: [{ key: "write", label: "write" }], email: [{ key: "send", label: "send" }],
  approval: [{ key: "request", label: "ask" }], slack: [{ key: "post", label: "post" }],
};
export function stepIcon(s: { tool: string; action?: string }) {
  if (s.tool === "sheets" && s.action === "update_row") return "✏️";
  return TOOL_ICON[s.tool] ?? "•";
}
