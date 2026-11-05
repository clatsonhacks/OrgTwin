"use client";
import { useState } from "react";
import { post } from "@/lib/api";
import type { Org } from "@/lib/types";

const EXAMPLES = [
  { label: "B2B SaaS · 60 people", text: "We are Brightpath, a 60-person B2B SaaS company selling analytics to retailers. Sales, finance, customer success, engineering and marketing." },
  { label: "Logistics · 120 people", text: "Kite Logistics: freight broker with 120 staff — sales, operations, procurement, finance, HR and support." },
];

const SAMPLE_CSV = `team,role,headcount,tasks,hours_per_week
Sales,SDR,4,Research leads;Send outreach;Update CRM,9
Sales,Account Executive,3,Run demos;Write proposals,5
Finance,AR Specialist,2,Create invoices;Chase payments,8
Customer Success,Onboarding Specialist,2,Kick-off calls;Send onboarding emails,6
Engineering,Software Engineer,8,Build features;Fix bugs,3`;

export default function StartCard({ onBuilt, onError, onClose }: {
  onBuilt: (o: Org) => void; onError: (m: string) => void; onClose?: () => void;
}) {
  const [description, setDescription] = useState("");
  const [url, setUrl] = useState("");
  const [csv, setCsv] = useState("");
  const [csvName, setCsvName] = useState("");
  const [busy, setBusy] = useState(false);

  const build = async () => {
    setBusy(true);
    try { onBuilt(await post<Org>("/api/org/generate", { description, url, csv })); }
    catch (e: any) { onError(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="start">
      <div className="start-card block">
        {onClose && <button className="x" onClick={onClose} aria-label="Close">✕</button>}
        <div className="eyebrow">Step 1 · Onboard your company</div>
        <h1>Turn your company into a living world</h1>
        <p className="lead">Every team becomes a building, every role an AI agent. Beacons show where agents can take work off people&apos;s plates.</p>

        <label className="lbl" htmlFor="desc">Describe your company</label>
        <textarea id="desc" rows={4} value={description} onChange={(e) => setDescription(e.target.value)}
                  placeholder="What you do, how many people, which teams…" />
        <div className="chips-row">
          {EXAMPLES.map((ex) => (
            <button key={ex.label} className="chip" onClick={() => setDescription(ex.text)}>{ex.label}</button>
          ))}
        </div>

        <div className="two">
          <div>
            <label className="lbl" htmlFor="url">Website <i>optional</i></label>
            <input id="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="yourcompany.com" />
          </div>
          <div>
            <label className="lbl">Org chart file <i>optional</i></label>
            <label className="drop">
              <input type="file" accept=".csv,text/csv" onChange={async (e) => {
                const f = e.target.files?.[0]; if (!f) return; setCsvName(f.name); setCsv(await f.text());
              }} />
              {csvName ? `📄 ${csvName}` : "Drop or pick a .csv"}
            </label>
            <button className="linkbtn" onClick={() => { setCsv(SAMPLE_CSV); setCsvName("sample-org.csv"); }}>use sample file</button>
          </div>
        </div>

        <button className="btn btn-go btn-big" disabled={busy || !(description || url || csv)} onClick={build}>
          {busy ? "Placing blocks…" : "Build my world ▶"}
        </button>
      </div>
    </div>
  );
}
