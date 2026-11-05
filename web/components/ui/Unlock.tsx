"use client";
import { useState } from "react";
import { api, setToken } from "@/lib/api";

/** Asks for this server's access code (APP_ACCESS_TOKEN) once, in the app instead of a browser prompt. */
export default function Unlock({ onDone, onClose }: { onDone: () => void; onClose: () => void }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [bad, setBad] = useState("");
  const submit = async () => {
    setBusy(true); setBad("");
    setToken(code.trim());
    try { await api("/api/auth/check"); onDone(); }
    catch { setToken(""); setBad("That code didn't work — check with whoever shared this demo."); }
    finally { setBusy(false); }
  };
  return (
    <div className="modal-bg" role="dialog" aria-modal aria-label="Enter access code">
      <form className="modal block unlock" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <header className="drawer-head">
          <div className="swatch connect-icon" aria-hidden>🔑</div>
          <div className="grow"><div className="eyebrow">Private demo</div><h2>Enter the access code</h2></div>
          <button type="button" className="x" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="modal-body connect-body">
          <p className="muted">You can look around without it. To build, run workflows or connect apps, enter the code
            the owner of this demo gave you. It&apos;s remembered on this device.</p>
          <input autoFocus type="password" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Access code" aria-label="Access code" />
          {bad && <p className="bad-line">✕ {bad}</p>}
          <button className="btn btn-go btn-big" disabled={busy || !code.trim()}>{busy ? "Checking…" : "Unlock"}</button>
        </div>
      </form>
    </div>
  );
}
