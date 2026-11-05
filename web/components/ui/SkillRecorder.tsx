"use client";
import { useEffect, useRef, useState } from "react";
import { post } from "@/lib/api";
import { MAX_KEYFRAMES, canRecordScreen, canTranscribe, evenly, framesFromVideo, startScreenCapture, startSpeech,
         type Keyframe, type ScreenSession, type Speech } from "@/lib/capture";
import type { Role, SkillAnalysis, Step, Team, Workflow } from "@/lib/types";
import StepEditor, { newStep } from "./StepEditor";
import { teamColor } from "@/components/world/layout";
import type { Person } from "@/lib/people";

type Stage = "intro" | "recording" | "extracting" | "captured" | "analyzing" | "review";

const SAMPLE_NARRATION = "I open the leads spreadsheet and look at the rows where status is New. For each one I write a short follow up email and send it, then I mark the row as contacted. Finally I tell the team on Slack.";

export default function SkillRecorder({ team, teamIndex, role, person, onClose, onSaved, onError }: {
  team: Team; teamIndex: number; role: Role; person?: Person | null; onClose: () => void; onSaved: (w: Workflow) => void; onError: (m: string) => void;
}) {
  const [stage, setStage] = useState<Stage>("intro");
  const [frames, setFrames] = useState<Keyframe[]>([]);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [narration, setNarration] = useState("");
  const [interim, setInterim] = useState("");
  const [progress, setProgress] = useState(0);
  const [analysis, setAnalysis] = useState<SkillAnalysis | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [phase, setPhase] = useState(0);
  const session = useRef<ScreenSession | null>(null);
  const speech = useRef<Speech | null>(null);
  const t0 = useRef(0);
  const color = teamColor(team.key, teamIndex);

  useEffect(() => {
    if (stage !== "recording") return;
    const id = window.setInterval(() => setSeconds((performance.now() - t0.current) / 1000), 250);
    return () => window.clearInterval(id);
  }, [stage]);
  useEffect(() => {
    if (stage !== "analyzing") return;
    setPhase(0);
    const id = window.setInterval(() => setPhase((p) => Math.min(2, p + 1)), 2200);
    return () => window.clearInterval(id);
  }, [stage]);
  useEffect(() => () => { speech.current?.stop(); }, []);

  const record = async () => {
    try {
      session.current = await startScreenCapture((f) => setFrames((fs) => [...fs, f].slice(-MAX_KEYFRAMES)), () => stop());
      setFrames([]); t0.current = performance.now(); setStage("recording");
      speech.current = startSpeech((fin, int) => { setNarration((n) => (fin ? fin : n)); setInterim(int); }, onError);
    } catch (e: any) {
      if (e?.name !== "NotAllowedError") onError(`Screen recording failed: ${e.message}`);
    }
  };

  const stop = async () => {
    speech.current?.stop(); speech.current = null; setInterim("");
    const s = session.current; session.current = null;
    if (!s) return;
    const out = await s.stop();
    setVideoUrl(out.videoUrl); setSeconds(out.seconds);
    // Live sampling is throttled while this tab is in the background, so re-read keyframes from the
    // full recording (it is complete regardless) and keep whichever set is richer.
    if (out.blob) {
      setStage("extracting"); setProgress(0);
      try {
        const again = await framesFromVideo(new File([out.blob], "recording.webm", { type: "video/webm" }), setProgress);
        setFrames(again.frames.length > out.frames.length ? again.frames : out.frames);
      } catch { setFrames(out.frames); }
    } else {
      setFrames(out.frames);
    }
    setStage("captured");
  };

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setStage("extracting"); setProgress(0);
    try {
      const out = await framesFromVideo(file, setProgress);
      setFrames(out.frames); setVideoUrl(out.videoUrl); setSeconds(out.seconds); setStage("captured");
    } catch (e: any) { onError(e.message); setStage("intro"); }
  };

  const learn = async () => {
    setStage("analyzing");
    try {
      const a = await post<SkillAnalysis>("/api/skills/analyze", {
        team_key: team.key, role_key: role.key, transcript: narration.trim(), title: name.trim(),
        frames: evenly(frames, 24).map((f) => f.url),
      });
      setAnalysis(a); setSteps(a.steps); setName(name.trim() || a.name); setStage("review");
    } catch (e: any) { onError(e.message); setStage("captured"); }
  };

  const save = async () => {
    setSaving(true);
    try {
      const w = await post<Workflow>("/api/skills", { team_key: team.key, role_key: role.key, name, steps, meta: { ...(analysis?.meta ?? {}), ...(person ? { person: person.id, person_name: person.name } : {}) } });
      onSaved(w);
    } catch (e: any) { onError(e.message); } finally { setSaving(false); }
  };

  const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

  return (
    <div className="modal-bg" role="dialog" aria-modal aria-label="Skill recorder">
      <div className="modal block" style={{ ["--team" as any]: color }}>
        <header className="drawer-head">
          <div className="swatch swatch-rec">{stage === "recording" ? <span className="recdot" /> : "🎥"}</div>
          <div className="grow">
            <div className="eyebrow">Skill recorder · {team.name}</div>
            <h2>{person ? `Teach ${person.name} (${role.name}) a skill` : `Teach the ${role.name} agent a skill`}</h2>
          </div>
          <button className="x" onClick={() => { speech.current?.stop(); session.current?.stop(); onClose(); }} aria-label="Close">✕</button>
        </header>

        <ol className="stages">
          {["Show it", "Learn", "Review & save"].map((l, i) => {
            const idx = stage === "review" ? 2 : stage === "analyzing" ? 1 : 0;
            return <li key={l} className={i < idx ? "done" : i === idx ? "on" : ""}><b>{i + 1}</b>{l}</li>;
          })}
        </ol>

        {stage === "intro" && (
          <div className="modal-body">
            <p className="lead">Do the task once, the way you always do it. Talk through what you&apos;re doing.
              OrgTwin watches, writes the workflow, and the {role.name} agent does it from then on — with your approval.</p>
            <div className="choice">
              <button className="choice-card" onClick={record} disabled={!canRecordScreen()}>
                <span className="big">🎥</span><b>Record my screen</b>
                <span className="muted">{canRecordScreen() ? `Pick a window or tab. ${canTranscribe() ? "Your narration is transcribed live." : "Type your narration after."}` : "Not supported in this browser"}</span>
              </button>
              <label className="choice-card">
                <input type="file" accept="video/*" onChange={(e) => upload(e.target.files?.[0])} />
                <span className="big">📁</span><b>Upload a video</b>
                <span className="muted">A screen recording you already have (MP4 / WebM).</span>
              </label>
            </div>
            <div className="tip">
              <b>Example:</b> open your leads spreadsheet, filter status = New, write a follow-up email to the first lead,
              send it, mark the row as Contacted. <button className="linkbtn" onClick={() => setNarration(SAMPLE_NARRATION)}>use this as narration</button>
            </div>
            <p className="privacy">🔒 The video never leaves your browser. Only a few still frames and your words are sent to
              the model on Vultr to learn the steps — frames are not stored.</p>
          </div>
        )}

        {stage === "recording" && (
          <div className="modal-body">
            <div className="rec-bar"><span className="recdot" /> Recording <b>{mmss(seconds)}</b><span className="muted">· {frames.length} keyframes</span>
              <span className="grow" /><button className="btn btn-bad" onClick={stop}>■ Stop &amp; continue</button></div>
            <Thumbs frames={frames.slice(-8)} />
            <label className="lbl">What you&apos;re saying {canTranscribe() ? <i>live transcript</i> : <i>type it here</i>}</label>
            <textarea rows={4} value={narration + (interim ? interim : "")} onChange={(e) => setNarration(e.target.value)}
                      placeholder="“I open the leads sheet, take the rows marked New, email each one…”" />
          </div>
        )}

        {stage === "extracting" && (
          <div className="modal-body center">
            <p>Pulling keyframes out of the video — in your browser…</p>
            <div className="meter"><i style={{ width: `${Math.round(progress * 100)}%` }} /></div>
          </div>
        )}

        {stage === "captured" && (
          <div className="modal-body">
            <div className="captured">
              {videoUrl ? <video src={videoUrl} controls muted className="preview" /> : <div className="preview" />}
              <div className="grow">
                <p><b>{frames.length}</b> keyframes from <b>{mmss(seconds)}</b> of video.</p>
                <Thumbs frames={evenly(frames, 8)} />
              </div>
            </div>
            <label className="lbl" htmlFor="narr">Narration <i>what you did, in your words — makes learning far more accurate</i></label>
            <textarea id="narr" rows={3} value={narration} onChange={(e) => setNarration(e.target.value)}
                      placeholder="“I open the leads sheet, take the rows marked New, email each one a follow-up, mark them Contacted.”" />
            <div className="row">
              <input className="grow" value={name} onChange={(e) => setName(e.target.value)} placeholder="Skill name (optional)" />
              <button className="btn" onClick={() => { setFrames([]); setVideoUrl(null); setStage("intro"); }}>Start over</button>
              <button className="btn btn-go" disabled={!frames.length && !narration.trim()} onClick={learn}>✦ Learn this skill</button>
            </div>
          </div>
        )}

        {stage === "analyzing" && (
          <div className="modal-body center">
            <div className="learning">
              {[`Watching ${Math.min(frames.length, 24)} keyframes`, "Reading what you said", "Writing the workflow"].map((t, i) => (
                <div key={t} className={`learn-step ${i < phase ? "done" : i === phase ? "on" : ""}`}>
                  <span>{i < phase ? "✓" : i === phase ? <span className="spinner" /> : "·"}</span>{t}
                </div>
              ))}
            </div>
          </div>
        )}

        {stage === "review" && analysis && (
          <div className="modal-body review">
            <div className="saw">
              <h3>What I picked up</h3>
              <div className="how">
                <span className="tag tag-go">{analysis.meta.how}</span>
                <span className="muted">{analysis.meta.vision_model ? `vision: ${analysis.meta.vision_model}` : "no vision model"} · planner: {analysis.meta.planner}</span>
              </div>
              {analysis.meta.note && <p className="note">{analysis.meta.note}</p>}
              {analysis.meta.observations.length > 0 && (
                <ul className="obs">{analysis.meta.observations.slice(0, 12).map((o, i) => <li key={i}>{o}</li>)}</ul>
              )}
              {analysis.meta.transcript && <blockquote>“{analysis.meta.transcript.slice(0, 400)}”</blockquote>}
              <Thumbs frames={evenly(frames, 6)} />
            </div>
            <div className="wrote">
              <h3>The workflow I wrote</h3>
              <input value={name} onChange={(e) => setName(e.target.value)} aria-label="Skill name" />
              <StepEditor steps={steps} onChange={setSteps} />
              <div className="row">
                <button className="btn" onClick={() => setSteps([...steps, newStep()])}>+ Step</button>
                <span className="grow" />
                <button className="btn" onClick={() => setStage("captured")}>Back</button>
                <button className="btn btn-go" disabled={saving || steps.length === 0 || name.trim().length < 2} onClick={save}>
                  {saving ? "Saving…" : `📘 Save skill to ${person?.name ?? role.name}`}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Thumbs({ frames }: { frames: Keyframe[] }) {
  if (!frames.length) return <div className="thumbs empty">Keyframes appear here as the screen changes…</div>;
  return (
    <div className="thumbs">
      {frames.map((f, i) => (
        // eslint-disable-next-line @next/next/no-img-element
        <img key={`${f.t}-${i}`} src={f.url} alt={`Keyframe at ${f.t.toFixed(1)}s`} />
      ))}
    </div>
  );
}
