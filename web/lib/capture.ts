/**
 * Browser-side capture for the Skill Recorder.
 * - Screen recording via getDisplayMedia; the video stays in the browser (object URL for playback).
 * - Keyframes: one JPEG every `intervalMs`, kept only when the screen actually changed.
 * - Narration: live speech-to-text via the Web Speech API when the browser has it.
 */

const MAX_W = 1024;
const SIG = { w: 96, h: 54 };
export const MAX_KEYFRAMES = 40;

export type Keyframe = { url: string; t: number };

function signature(src: CanvasImageSource, w: number, h: number): Uint8ClampedArray {
  const c = document.createElement("canvas");
  c.width = SIG.w; c.height = SIG.h;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(src, 0, 0, w, h, 0, 0, SIG.w, SIG.h);
  return ctx.getImageData(0, 0, SIG.w, SIG.h).data;
}

function changed(a: Uint8ClampedArray | null, b: Uint8ClampedArray): boolean {
  if (!a) return true;
  // Count pixels whose brightness moved noticeably: small UI edits (a filter, typed text) change few
  // pixels a lot, which an average over the whole frame would miss.
  let moved = 0;
  for (let i = 0; i < a.length; i += 4) {
    const d = Math.abs((a[i] + a[i + 1] + a[i + 2]) - (b[i] + b[i + 1] + b[i + 2])) / 3;
    if (d > 18) moved++;
  }
  return moved / (a.length / 4) > 0.002; // > 0.2% of the thumbnail (~10 pixels)
}

function toJpeg(src: CanvasImageSource, w: number, h: number): string {
  const scale = Math.min(1, MAX_W / w);
  const c = document.createElement("canvas");
  c.width = Math.round(w * scale); c.height = Math.round(h * scale);
  c.getContext("2d")!.drawImage(src, 0, 0, c.width, c.height);
  return c.toDataURL("image/jpeg", 0.7);
}

export type ScreenSession = {
  stop: () => Promise<{ frames: Keyframe[]; videoUrl: string | null; blob: Blob | null; seconds: number }>;
};

export function canRecordScreen(): boolean {
  return typeof navigator !== "undefined" && !!navigator.mediaDevices?.getDisplayMedia;
}

export async function startScreenCapture(onFrame: (f: Keyframe, count: number) => void,
                                         onEnded: () => void, intervalMs = 2000): Promise<ScreenSession> {
  const stream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 5 }, audio: false });
  const video = document.createElement("video");
  video.muted = true; video.playsInline = true; video.srcObject = stream;
  await video.play();
  const started = performance.now();
  const frames: Keyframe[] = [];
  let last: Uint8ClampedArray | null = null;

  const grab = () => {
    const w = video.videoWidth, h = video.videoHeight;
    if (!w || !h) return;
    const sig = signature(video, w, h);
    const t = (performance.now() - started) / 1000;
    if (!changed(last, sig) && t - (frames[frames.length - 1]?.t ?? -99) < 6) return;
    last = sig;
    const f = { url: toJpeg(video, w, h), t };
    frames.push(f);
    if (frames.length > MAX_KEYFRAMES) frames.splice(1, 1); // keep the first frame, drop the oldest after it
    onFrame(f, frames.length);
  };
  const timer = window.setInterval(grab, intervalMs);
  setTimeout(grab, 300);

  const chunks: Blob[] = [];
  let recorder: MediaRecorder | null = null;
  try {
    recorder = new MediaRecorder(stream, { mimeType: MediaRecorder.isTypeSupported("video/webm;codecs=vp9") ? "video/webm;codecs=vp9" : "video/webm" });
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.start(1000);
  } catch { recorder = null; }
  stream.getVideoTracks()[0].addEventListener("ended", onEnded);

  return {
    stop: async () => {
      grab();
      window.clearInterval(timer);
      const done = new Promise<void>((res) => {
        if (!recorder || recorder.state === "inactive") return res();
        recorder.onstop = () => res();
        recorder.stop();
      });
      await done;
      stream.getTracks().forEach((t) => t.stop());
      const blob = chunks.length ? new Blob(chunks, { type: "video/webm" }) : null;
      const videoUrl = blob ? URL.createObjectURL(blob) : null;
      return { frames, videoUrl, blob, seconds: (performance.now() - started) / 1000 };
    },
  };
}

/**
 * Pull changed keyframes out of an uploaded video file, entirely in the browser.
 * Plays the video muted at high speed and samples it, which works even for recordings without a
 * duration index (common for browser-made WebM files, where seeking stalls).
 */
export async function framesFromVideo(file: File, onProgress: (p: number) => void, sampleS = 1):
  Promise<{ frames: Keyframe[]; videoUrl: string; seconds: number }> {
  const videoUrl = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.muted = true; video.playsInline = true; video.preload = "auto"; video.src = videoUrl;
  await new Promise<void>((res, rej) => {
    video.onloadeddata = () => res();
    video.onerror = () => rej(new Error("This video format can't be read by the browser — try MP4 or WebM"));
  });
  const known = isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
  if (known) {
    const seeked = await bySeeking(video, known, onProgress);
    if (seeked) return { frames: evenly(seeked, MAX_KEYFRAMES), videoUrl, seconds: known };
  }
  const rate = known > 600 ? 16 : known > 120 ? 8 : 4;
  video.playbackRate = rate;
  const frames: Keyframe[] = [];
  let last: Uint8ClampedArray | null = null;
  let lastT = -Infinity;

  const grab = () => {
    const w = video.videoWidth, h = video.videoHeight;
    if (!w || !h || video.currentTime - lastT < sampleS) return;
    lastT = video.currentTime;
    const sig = signature(video, w, h);
    if (!changed(last, sig) && video.currentTime - (frames[frames.length - 1]?.t ?? -99) < 6) return;
    last = sig;
    frames.push({ url: toJpeg(video, w, h), t: video.currentTime });
  };

  await new Promise<void>((resolve, reject) => {
    const tick = window.setInterval(() => {
      grab();
      if (known) onProgress(Math.min(1, video.currentTime / known));
    }, Math.max(60, (sampleS * 1000) / rate / 2));
    const finish = () => { window.clearInterval(tick); grab(); resolve(); };
    video.onended = finish;
    // Safety net: never wait longer than the real-time length (or 3 minutes when unknown).
    window.setTimeout(finish, (known ? (known / rate) * 1000 + 5000 : 180000));
    video.play().catch((e) => { window.clearInterval(tick); reject(e); });
  });
  video.pause();
  onProgress(1);
  return { frames: evenly(frames, MAX_KEYFRAMES), videoUrl, seconds: video.currentTime || known };
}

/** Sample by jumping to timestamps. Returns null if the browser can't seek this file reliably. */
async function bySeeking(video: HTMLVideoElement, duration: number, onProgress: (p: number) => void): Promise<Keyframe[] | null> {
  const step = Math.max(1, duration / 90);
  const frames: Keyframe[] = [];
  let last: Uint8ClampedArray | null = null;
  for (let t = 0.05; t < duration; t += step) {
    const ok = await new Promise<boolean>((res) => {
      const to = window.setTimeout(() => res(false), 3000);
      video.onseeked = () => { window.clearTimeout(to); res(true); };
      video.currentTime = t;
    });
    if (!ok) return frames.length >= 3 ? frames : null;
    const w = video.videoWidth, h = video.videoHeight;
    const sig = signature(video, w, h);
    if (changed(last, sig) || t - (frames[frames.length - 1]?.t ?? -99) >= 6) {
      last = sig;
      frames.push({ url: toJpeg(video, w, h), t });
    }
    onProgress(Math.min(1, t / duration));
  }
  return frames;
}

export function evenly<T>(arr: T[], n: number): T[] {
  if (arr.length <= n) return arr;
  const s = (arr.length - 1) / (n - 1);
  return Array.from({ length: n }, (_, i) => arr[Math.round(i * s)]);
}

/* ---------------- narration (speech-to-text) ---------------- */

export type Speech = { stop: () => void };

export function canTranscribe(): boolean {
  return typeof window !== "undefined" && !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
}

export function startSpeech(onText: (finalText: string, interim: string) => void, onError: (m: string) => void): Speech | null {
  const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  if (!SR) return null;
  const rec = new SR();
  rec.continuous = true; rec.interimResults = true; rec.lang = navigator.language || "en-US";
  let finalText = "";
  let stopped = false;
  rec.onresult = (e: any) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) finalText += r[0].transcript.trim() + ". ";
      else interim += r[0].transcript;
    }
    onText(finalText, interim);
  };
  rec.onerror = (e: any) => { if (e.error !== "no-speech" && e.error !== "aborted") onError(`Speech-to-text: ${e.error}`); };
  rec.onend = () => { if (!stopped) { try { rec.start(); } catch { /* already started */ } } };
  try { rec.start(); } catch (e: any) { onError(e.message); return null; }
  return { stop: () => { stopped = true; try { rec.stop(); } catch { /* not running */ } } };
}
