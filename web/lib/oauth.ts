"use client";
import { api, post } from "./api";

export type PopupResult = { ok: boolean; message: string };

/**
 * Open the provider's own sign-in / "Allow access" screen in a popup and wait until it is done.
 * The popup is opened right away (inside the click) so browsers don't block it, then pointed at the URL.
 */
export function connectWithPopup(provider: string): Promise<PopupResult> {
  const w = 520, h = 680;
  const left = window.screenX + Math.max(0, (window.outerWidth - w) / 2);
  const top = window.screenY + Math.max(0, (window.outerHeight - h) / 2);
  const popup = window.open("", `orgtwin-${provider}`, `width=${w},height=${h},left=${left},top=${top}`);
  if (popup) popup.document.write("<p style='font:16px system-ui;padding:24px'>Opening sign-in…</p>");
  return new Promise((resolve) => {
    let done = false;
    const finish = (r: PopupResult) => {
      if (done) return;
      done = true; window.removeEventListener("message", onMsg); clearInterval(timer); resolve(r);
    };
    const onMsg = (e: MessageEvent) => {
      const d = e.data;
      if (d && d.source === "orgtwin-oauth" && d.provider === provider) finish({ ok: !!d.ok, message: d.message });
    };
    window.addEventListener("message", onMsg);
    // if the popup is closed without finishing, stop waiting (the caller re-reads the state anyway)
    const timer = setInterval(() => { if (popup && popup.closed) finish({ ok: false, message: "" }); }, 600);
    post<{ url: string }>(`/api/oauth/${provider}/start`).then(({ url }) => {
      if (popup && !popup.closed) popup.location.href = url;
      else window.location.assign(url); // popups blocked: go there in this tab
    }).catch((e) => { popup?.close(); finish({ ok: false, message: e.message }); });
  });
}

/** Open a help page (where to create a key) in a small window next to the app. */
export function openHelp(url: string) {
  window.open(url, "orgtwin-help", "width=1000,height=760,noopener");
}

/**
 * Telegram has no sign-in popup: open a t.me link to OrgTwin's bot with a one-time code, then wait
 * until the user presses Start (in a group or a direct chat) and the API sees it.
 */
export async function connectTelegram(where: "group" | "dm", signal: { cancelled: boolean }): Promise<PopupResult> {
  const tab = window.open("", "orgtwin-telegram");
  try {
    const st = await post<{ code: string; url: string; dm_url: string }>("/api/connect/telegram/start");
    const link = where === "group" ? st.url : st.dm_url;
    if (tab && !tab.closed) tab.location.href = link; else window.open(link, "_blank");
    const until = Date.now() + 9 * 60_000;
    while (!signal.cancelled && Date.now() < until) {
      await new Promise((r) => setTimeout(r, 2500));
      const r = await api<{ connected: boolean; account?: string }>(`/api/connect/telegram/check?code=${encodeURIComponent(st.code)}`);
      if (r.connected) return { ok: true, message: `Connected to ${r.account} — agents will post there.` };
    }
    return { ok: false, message: signal.cancelled ? "" : "Didn't see the Start message — click Connect to try again." };
  } catch (e: any) {
    tab?.close();
    return { ok: false, message: e.message };
  }
}
