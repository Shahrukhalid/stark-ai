"use client";

import { Clapperboard, Download, DownloadCloud, FileText, Globe, Image as ImageIcon, LoaderCircle, Mic, Scissors, ShieldCheck, Sliders, Trash2, Upload, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useStark } from "@/lib/store";
import { Section } from "./ui";

type VFile = { name: string; bytes: number; kind: "video" | "image" | "captions"; transcript?: boolean };
type Pick = { title: string; hook: string; hashtags: string[]; video: string; thumbnail: string; start: number; end: number; drafts: { platform: string; id?: string; error?: string }[] };
type Job = { clips?: Pick[]; picked_by?: string | null; id: string; kind: string; status: "queued" | "running" | "done" | "failed" | "cancelled"; progress: number; file: string; output: string | null; captions: string | null; error: string | null; preset?: string; format?: string; model?: string; measured_loudness?: { integrated_lufs: number; true_peak_db: number; lra?: number } };
type Model = { name: string; label: string; installed: boolean };
type FF = { available: boolean; message?: string; version?: string; burn_captions?: boolean; transitions?: boolean; transcribe?: boolean };
type Home = { ffmpeg: FF; files: VFile[]; jobs: Job[]; presets: Record<string, { label: string }>; formats: string[]; models: Model[] };
type Seg = { start: number; end: number; text: string };
type Transcript = { file: string; segments: Seg[]; clips: Seg[]; text: string };

const mb = (n: number) => `${(n / 1048576).toFixed(1)} MB`;
const t = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const KIND: Record<string, string> = { export: "Export", merge: "Join", transcribe: "Transcribe", model: "Speech model", package: "Shorts package" };
const PKG_PLATFORMS = ["tiktok", "instagram", "youtube", "x", "threads", "bluesky", "linkedin", "facebook"];

/** Trim, join, speed, captions from speech, platform sizes, thumbnails: all on this computer. */
export function VideoStudio({ talkTo }: { talkTo: (agentId: string, text?: string) => void }) {
  const { client, notify } = useStark();
  const [home, setHome] = useState<Home | null>(null);
  const [busy, setBusy] = useState("");
  const [join, setJoin] = useState<string[]>([]);
  const [tr, setTr] = useState<Transcript | null>(null);
  const [frames, setFrames] = useState<{ a?: string; b?: string }>({});
  const [model, setModel] = useState("base");
  const [pkg, setPkg] = useState({ count: 3, platforms: ["tiktok", "instagram", "youtube"], animation: "pop" });
  const [showDownloadModal, setShowDownloadModal] = useState(false);
  const [dlUrl, setDlUrl] = useState("");
  const [dlRes, setDlRes] = useState("");
  const [dlAudio, setDlAudio] = useState(false);
  const [dlJobs, setDlJobs] = useState<any[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [metaInfo, setMetaInfo] = useState<any>(null);
  const [wmMode, setWmMode] = useState<"text" | "image">("text");
  const lastDoneIdsRef = useRef<Set<string>>(new Set());
  const [o, setO] = useState({
    file: "", presets: ["vertical"] as string[], format: "mp4", fit: "pad", start: "", end: "", mute: false, speed: 1, volume: 1, fade: 0, transition: 0.5,
    captions: "none", caption_source: "script", animation: "none", transition_type: "fade", script: "", srt: "", style: { size: 14, color: "white", position: "bottom", box: false },
    normalize_audio: false, brightness: 0, contrast: 1, saturation: 1, gamma: 1,
    watermark_text: "", watermark_image: "", watermark_opacity: 0.85, watermark_scale: 0.2, watermark_position: "bottom-right", watermark_size: 24, watermark_color: "white",
  });
  const set = (k: keyof typeof o, v: any) => setO((x) => ({ ...x, [k]: v }));

  const load = useCallback(() => {
    client.get<Home>("/api/video").then((h) => {
      setHome(h);
      const first = h.files.find((f) => f.kind === "video");
      setO((x) => (x.file || !first ? x : { ...x, file: first.name }));
    }).catch((e) => notify("error", e.message));
  }, [client, notify]);
  useEffect(load, [load]);
  const running = home?.jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(load, 1000);
    return () => clearInterval(timer);
  }, [running, load]);

  const current = home?.files.find((f) => f.name === o.file);
  useEffect(() => {
    setTr(null);
    if (current?.transcript) client.get<Transcript>(`/api/video/transcript?name=${encodeURIComponent(current.name)}`).then(setTr).catch(() => {});
  }, [client, current?.name, current?.transcript]);

  // timeline: frames at the start and end of the cut
  useEffect(() => {
    if (!o.file) return;
    let live = true;
    const urls: string[] = [];
    const grab = (at: number, key: "a" | "b") =>
      client.blob(`/api/video/preview?name=${encodeURIComponent(o.file)}&at=${at}`).then((b) => {
        const u = URL.createObjectURL(b);
        urls.push(u);
        if (live) setFrames((f) => ({ ...f, [key]: u }));
      }).catch(() => {});
    const timer = setTimeout(() => {
      grab(Number(o.start || 0), "a");
      if (o.end) grab(Math.max(0, Number(o.end) - 0.1), "b");
      else setFrames((f) => ({ ...f, b: undefined }));
    }, 400);
    return () => {
      live = false;
      clearTimeout(timer);
      urls.forEach((u) => setTimeout(() => URL.revokeObjectURL(u), 5000));
    };
  }, [client, o.file, o.start, o.end]);

  async function act(name: string, call: () => Promise<unknown>, ok?: string) {
    setBusy(name);
    try {
      await call();
      if (ok) notify("success", ok);
      load();
    } catch (e: any) {
      notify("error", e.message);
    } finally {
      setBusy("");
    }
  }

  const upload = (f: File) => act("upload", async () => {
    const r = await client.upload<{ name: string }>(`/api/video/upload?name=${encodeURIComponent(f.name)}`, f);
    set("file", r.name);
  }, "Video added");

  const exportVideo = (extra: Record<string, unknown> = {}) => act("export", () => client.post("/api/video/export", {
    ...o, file: o.file, files: join.length >= 2 ? join : null, preset: o.presets[0], presets: o.presets,
    start: o.start ? Number(o.start) : null, end: o.end ? Number(o.end) : null, transition: o.transition,
    script: o.script || null, srt: o.srt || null, caption_source: o.srt ? "srt" : o.caption_source, ...extra,
  }));

  async function exportClips() {
    if (!tr) return;
    for (const c of tr.clips) await exportVideo({ start: c.start, end: c.end, files: null });
    notify("success", `${tr.clips.length} clips queued`);
  }

  async function download(name: string) {
    try {
      const url = URL.createObjectURL(await client.blob(`/api/video/file?name=${encodeURIComponent(name)}`));
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (e: any) {
      notify("error", e.message);
    }
  }

  const pollDlJobs = useCallback(() => {
    client.get<{ jobs: any[] }>("/api/video/download/jobs").then((r) => {
      const jobs = r.jobs || [];
      setDlJobs(jobs);
      let newlyCompleted = false;
      for (const j of jobs) {
        if (j.status === "done" && !lastDoneIdsRef.current.has(j.id)) {
          lastDoneIdsRef.current.add(j.id);
          newlyCompleted = true;
        }
      }
      if (newlyCompleted) {
        load();
      }
    }).catch(() => {});
  }, [client, load]);

  async function analyzeWebUrl() {
    if (!dlUrl.trim()) return;
    setAnalyzing(true);
    setMetaInfo(null);
    try {
      const info = await client.post<any>("/api/video/analyze", { url: dlUrl });
      setMetaInfo(info);
      notify("success", "Media metadata retrieved");
    } catch (e: any) {
      notify("error", e.message);
    } finally {
      setAnalyzing(false);
    }
  }

  useEffect(() => {
    if (!showDownloadModal && !dlJobs.some((j) => j.status === "running" || j.status === "queued")) return;
    pollDlJobs();
    const timer = setInterval(pollDlJobs, 1200);
    return () => clearInterval(timer);
  }, [showDownloadModal, dlJobs, pollDlJobs]);

  async function exportSrt() {
    if (!o.file) return;
    try {
      const res = await client.get<{ file: string; srt: string }>(`/api/video/captions?name=${encodeURIComponent(o.file)}`);
      const blob = new Blob([res.srt], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${o.file.replace(/\.[^.]+$/, "")}.srt`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      notify("success", "Captions exported as SRT");
    } catch (e: any) {
      notify("error", e.message);
    }
  }

  function importSrtFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f || !o.file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      const text = String(reader.result || "");
      try {
        await client.post("/api/video/captions", { file: o.file, content: text });
        setO((x) => ({ ...x, srt: text, caption_source: "srt", captions: x.captions === "none" ? "burn" : x.captions }));
        notify("success", "SRT captions saved");
        load();
      } catch (err: any) {
        notify("error", err.message);
      }
    };
    reader.readAsText(f);
  }

  async function startWebDownload() {
    if (!dlUrl.trim()) return;
    setBusy("dl-start");
    try {
      await client.post<any>("/api/video/download", { url: dlUrl, resolution: dlRes || null, audio_only: dlAudio });
      notify("success", "Download started");
      setDlUrl("");
      pollDlJobs();
    } catch (e: any) {
      notify("error", e.message);
    } finally {
      setBusy("");
    }
  }

  async function cancelWebDownload(id: string) {
    try {
      await client.del(`/api/video/download/jobs/${id}`);
      notify("success", "Download cancelled");
      pollDlJobs();
    } catch (e: any) {
      notify("error", e.message);
    }
  }

  if (!home) return null;
  const ff = home.ffmpeg;
  const videos = home.files.filter((f) => f.kind === "video");
  const installed = home.models.filter((m) => m.installed);
  const label = (txt: string) => <span className="hud-label text-[var(--color-mute)]">{txt}</span>;

  return (
    <Section title="Video studio" right={<Clapperboard size={15} className="text-cyan-300" />}>
      {!ff.available ? (
        <p className="text-sm text-amber-300">{ff.message}</p>
      ) : (
        <div className="grid gap-4 [&>*]:min-w-0 xl:grid-cols-[1.3fr_1fr]">
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <select aria-label="Video" className="input !w-auto min-w-0 flex-1" value={o.file} onChange={(e) => set("file", e.target.value)}>
                {videos.length === 0 && <option value="">Add a video first</option>}
                {videos.map((f) => <option key={f.name} value={f.name}>{f.name} · {mb(f.bytes)}{f.transcript ? " · transcribed" : ""}</option>)}
              </select>
              <label className="btn cursor-pointer">
                {busy === "upload" ? <LoaderCircle size={13} className="animate-spin" /> : <Upload size={13} />} Add video
                <input type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.m4v" className="sr-only" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
              </label>
              <button className="btn" onClick={() => setShowDownloadModal(true)}>
                <Globe size={13} className="text-cyan-300" /> Download from Web
              </button>
            </div>

            {o.file && (
              <div className="grid grid-cols-2 gap-2" aria-label="Timeline">
                {(["a", "b"] as const).map((k) => (
                  <figure key={k} className="overflow-hidden rounded-lg border border-[var(--color-line)] bg-black/40">
                    {frames[k] ? <img src={frames[k]} alt={k === "a" ? "First frame of the cut" : "Last frame of the cut"} className="h-28 w-full object-contain" /> : <div className="grid h-28 place-items-center text-xs text-[var(--color-mute)]">{k === "a" ? "start" : "end of video"}</div>}
                    <figcaption className="px-2 py-1 text-[11px] text-[var(--color-mute)]">{k === "a" ? `Start ${t(Number(o.start || 0))}` : o.end ? `End ${t(Number(o.end))}` : "End"}</figcaption>
                  </figure>
                ))}
              </div>
            )}

            <div className="grid gap-2 sm:grid-cols-3">
              <label>{label("Start (s)")}<input className="input mt-1 w-full" type="number" min={0} step={0.1} value={o.start} onChange={(e) => set("start", e.target.value)} placeholder="0" /></label>
              <label>{label("End (s)")}<input className="input mt-1 w-full" type="number" min={0} step={0.1} value={o.end} onChange={(e) => set("end", e.target.value)} placeholder="end" /></label>
              <label>{label("Format")}<select className="input mt-1 w-full" value={o.format} onChange={(e) => set("format", e.target.value)}>{home.formats.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}</select></label>
              <label>{label(`Speed ${o.speed}×`)}<input className="mt-2 w-full" type="range" min={0.5} max={2} step={0.25} value={o.speed} onChange={(e) => set("speed", Number(e.target.value))} /></label>
              <label>{label(`Volume ${Math.round(o.volume * 100)}%`)}<input className="mt-2 w-full" type="range" min={0} max={2} step={0.1} value={o.volume} onChange={(e) => set("volume", Number(e.target.value))} disabled={o.mute} /></label>
              <label>{label(`Fade in/out ${o.fade}s`)}<input className="mt-2 w-full" type="range" min={0} max={1} step={0.25} value={o.fade} onChange={(e) => set("fade", Number(e.target.value))} /></label>
            </div>

            <fieldset className="space-y-1">
              <legend className="hud-label text-[var(--color-mute)]">Sizes (one export each)</legend>
              <div className="flex flex-wrap gap-2 text-xs">
                {Object.entries(home.presets).map(([id, p]) => (
                  <label key={id} className={`chip cursor-pointer ${o.presets.includes(id) ? "!border-cyan-400 !text-cyan-200" : ""}`}>
                    <input type="checkbox" className="sr-only" checked={o.presets.includes(id)} onChange={(e) => set("presets", e.target.checked ? [...o.presets, id] : o.presets.filter((x) => x !== id).length ? o.presets.filter((x) => x !== id) : o.presets)} />
                    {p.label}
                  </label>
                ))}
              </div>
              <div className="flex flex-wrap gap-3 pt-1 text-xs">
                <label className="flex items-center gap-1"><input type="radio" checked={o.fit === "pad"} onChange={() => set("fit", "pad")} /> Whole picture (bars)</label>
                <label className="flex items-center gap-1"><input type="radio" checked={o.fit === "crop"} onChange={() => set("fit", "crop")} /> Fill the frame (crop)</label>
                <label className="flex items-center gap-1"><input type="checkbox" checked={o.mute} onChange={(e) => set("mute", e.target.checked)} /> Remove sound</label>
                <label className="flex items-center gap-1"><input type="checkbox" checked={o.normalize_audio} onChange={(e) => set("normalize_audio", e.target.checked)} /> Broadcast audio (-14 LUFS, 2-pass)</label>
              </div>
            </fieldset>

            <details className="rounded-lg border border-[var(--color-line)] p-2">
              <summary className="cursor-pointer text-xs font-medium flex items-center gap-1.5"><Sliders size={13} className="text-cyan-300" /> Color & visual grading</summary>
              <div className="mt-2 grid gap-2 grid-cols-2 sm:grid-cols-4 text-xs">
                <label>{label(`Brightness ${Math.round(o.brightness * 100)}%`)}<input className="mt-2 w-full" type="range" min={-0.5} max={0.5} step={0.05} value={o.brightness} onChange={(e) => set("brightness", Number(e.target.value))} /></label>
                <label>{label(`Contrast ${o.contrast.toFixed(2)}×`)}<input className="mt-2 w-full" type="range" min={0.5} max={1.5} step={0.05} value={o.contrast} onChange={(e) => set("contrast", Number(e.target.value))} /></label>
                <label>{label(`Saturation ${o.saturation.toFixed(1)}×`)}<input className="mt-2 w-full" type="range" min={0} max={2} step={0.1} value={o.saturation} onChange={(e) => set("saturation", Number(e.target.value))} /></label>
                <label>{label(`Gamma ${o.gamma.toFixed(2)}`)}<input className="mt-2 w-full" type="range" min={0.5} max={2.0} step={0.05} value={o.gamma} onChange={(e) => set("gamma", Number(e.target.value))} /></label>
              </div>
            </details>

            <details className="rounded-lg border border-[var(--color-line)] p-2">
              <summary className="cursor-pointer text-xs font-medium flex items-center gap-1.5"><ShieldCheck size={13} className="text-cyan-300" /> Watermark & Branding</summary>
              <div className="mt-2 space-y-2 text-xs">
                <div className="flex gap-4">
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input type="radio" checked={wmMode === "text"} onChange={() => setWmMode("text")} />
                    <span>Text Watermark</span>
                  </label>
                  <label className="flex items-center gap-1 cursor-pointer">
                    <input type="radio" checked={wmMode === "image"} onChange={() => setWmMode("image")} />
                    <span>Logo / Image</span>
                  </label>
                </div>

                {wmMode === "text" ? (
                  <>
                    <input className="input w-full" type="text" placeholder="Watermark text (e.g. @yourbrand)" value={o.watermark_text} onChange={(e) => set("watermark_text", e.target.value)} />
                    {o.watermark_text && (
                      <div className="flex flex-wrap items-center gap-2">
                        <label>{label("Position")}
                          <select className="input mt-1 !w-auto !py-1" value={o.watermark_position} onChange={(e) => set("watermark_position", e.target.value)}>
                            <option value="bottom-right">Bottom Right</option>
                            <option value="bottom-left">Bottom Left</option>
                            <option value="top-right">Top Right</option>
                            <option value="top-left">Top Left</option>
                            <option value="center">Center</option>
                          </select>
                        </label>
                        <label>{label("Size")}
                          <input className="input mt-1 !w-16 !py-1" type="number" min={12} max={72} value={o.watermark_size} onChange={(e) => set("watermark_size", Number(e.target.value))} />
                        </label>
                        <label>{label("Color")}
                          <select className="input mt-1 !w-auto !py-1" value={o.watermark_color} onChange={(e) => set("watermark_color", e.target.value)}>
                            <option value="white">White</option>
                            <option value="yellow">Yellow</option>
                            <option value="cyan">Cyan</option>
                          </select>
                        </label>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="space-y-2">
                    <input className="input w-full" type="text" placeholder="Image file in video library (e.g. logo.png)" value={o.watermark_image} onChange={(e) => set("watermark_image", e.target.value)} />
                    {o.watermark_image && (
                      <div className="flex flex-wrap items-center gap-3">
                        <label>{label("Position")}
                          <select className="input mt-1 !w-auto !py-1" value={o.watermark_position} onChange={(e) => set("watermark_position", e.target.value)}>
                            <option value="bottom-right">Bottom Right</option>
                            <option value="bottom-left">Bottom Left</option>
                            <option value="top-right">Top Right</option>
                            <option value="top-left">Top Left</option>
                            <option value="center">Center</option>
                          </select>
                        </label>
                        <label>{label(`Opacity ${Math.round(o.watermark_opacity * 100)}%`)}
                          <input className="mt-1 w-24" type="range" min={0.1} max={1.0} step={0.05} value={o.watermark_opacity} onChange={(e) => set("watermark_opacity", Number(e.target.value))} />
                        </label>
                        <label>{label(`Scale ${Math.round(o.watermark_scale * 100)}%`)}
                          <input className="mt-1 w-24" type="range" min={0.05} max={0.5} step={0.05} value={o.watermark_scale} onChange={(e) => set("watermark_scale", Number(e.target.value))} />
                        </label>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </details>

            <details className="rounded-lg border border-[var(--color-line)] p-2" open={o.captions !== "none"}>
              <summary className="cursor-pointer text-xs font-medium">Captions</summary>
              <div className="mt-2 space-y-2">
                <div className="grid gap-2 sm:grid-cols-2">
                  <label>{label("Captions")}
                    <select className="input mt-1 w-full" value={o.captions} onChange={(e) => set("captions", e.target.value)}>
                      <option value="none">None</option>
                      {ff.burn_captions && <option value="burn">Burned into the picture</option>}
                      <option value="track">Caption track (MP4)</option>
                      <option value="file">Separate .srt file</option>
                    </select>
                  </label>
                  <label>{label("Text from")}
                    <select className="input mt-1 w-full" value={o.caption_source} onChange={(e) => set("caption_source", e.target.value)}>
                      <option value="transcript" disabled={!current?.transcript}>The speech (transcribe first)</option>
                      <option value="script">A script (evenly timed)</option>
                    </select>
                  </label>
                </div>
                {o.caption_source === "script" && o.captions !== "none" && (
                  <>
                    <textarea className="input w-full" rows={2} value={o.script} onChange={(e) => set("script", e.target.value)} placeholder="Script" />
                    <textarea className="input w-full font-mono text-xs" rows={2} value={o.srt} onChange={(e) => set("srt", e.target.value)} placeholder="…or paste exact SRT captions" />
                  </>
                )}
                <div className="flex flex-wrap gap-2 pt-1">
                  <button className="btn !py-1 text-xs" disabled={!o.file || busy === "srt-export"} onClick={exportSrt}>
                    <Download size={11} /> Export SRT
                  </button>
                  <label className="btn !py-1 text-xs cursor-pointer">
                    <Upload size={11} /> Import SRT
                    <input type="file" accept=".srt" className="sr-only" onChange={importSrtFile} />
                  </label>
                </div>
                {o.captions === "burn" && (
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <label className="flex items-center gap-1">Size <input className="input !w-16 !py-1" type="number" min={8} max={40} value={o.style.size} onChange={(e) => set("style", { ...o.style, size: Number(e.target.value) })} /></label>
                    <select className="input !w-auto !py-1" aria-label="Caption colour" value={o.style.color} onChange={(e) => set("style", { ...o.style, color: e.target.value })}><option value="white">White</option><option value="yellow">Yellow</option><option value="cyan">Cyan</option></select>
                    <select className="input !w-auto !py-1" aria-label="Caption position" value={o.style.position} onChange={(e) => set("style", { ...o.style, position: e.target.value })}><option value="bottom">Bottom</option><option value="middle">Middle</option><option value="top">Top</option></select>
                    <label className="flex items-center gap-1"><input type="checkbox" checked={o.style.box} onChange={(e) => set("style", { ...o.style, box: e.target.checked })} /> Dark box</label>
                    <select className="input !w-auto !py-1" aria-label="Caption animation" value={o.animation} onChange={(e) => set("animation", e.target.value)}>
                      <option value="none">No animation</option><option value="fade">Fade in</option><option value="pop">Pop in</option><option value="karaoke">Word by word</option>
                    </select>
                  </div>
                )}
              </div>
            </details>

            {videos.length >= 2 && (
              <details className="rounded-lg border border-[var(--color-line)] p-2" open={join.length > 0}>
                <summary className="cursor-pointer text-xs font-medium">Join videos {join.length >= 2 && `(${join.length} in order)`}</summary>
                <div className="mt-2 space-y-1 text-xs">
                  {videos.map((f) => (
                    <label key={f.name} className="flex items-center gap-2">
                      <input type="checkbox" checked={join.includes(f.name)} onChange={(e) => setJoin((j) => (e.target.checked ? [...j, f.name] : j.filter((x) => x !== f.name)))} />
                      {join.includes(f.name) && <b>{join.indexOf(f.name) + 1}.</b>} <span className="truncate">{f.name}</span>
                    </label>
                  ))}
                  {ff.transitions && (
                    <div className="flex flex-wrap items-center gap-2 pt-1">
                      <select className="input !w-auto !py-1" aria-label="Transition" value={o.transition_type} onChange={(e) => set("transition_type", e.target.value)}>
                        {["fade", "dissolve", "wipeleft", "wiperight", "slideleft", "slideup", "circleopen", "fadeblack", "zoomin"].map((x) => <option key={x} value={x}>{x}</option>)}
                      </select>
                      <label className="flex items-center gap-2">{o.transition}s <input type="range" min={0} max={1.5} step={0.25} value={o.transition} onChange={(e) => set("transition", Number(e.target.value))} /></label>
                    </div>
                  )}
                </div>
              </details>
            )}

            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary" disabled={(!o.file && join.length < 2) || busy === "export"} onClick={() => exportVideo()}>
                {busy === "export" ? <LoaderCircle size={13} className="animate-spin" /> : <Clapperboard size={13} />} {join.length >= 2 ? `Join ${join.length} videos` : o.presets.length > 1 ? `Export ${o.presets.length} sizes` : "Export"}
              </button>
              <button className="btn" disabled={!o.file || busy === "thumb"} onClick={() => act("thumb", () => client.post("/api/video/thumbnail", { file: o.file, at: Number(o.start || 1) }), "Thumbnail saved to your media library")}>
                <ImageIcon size={13} /> Thumbnail at start
              </button>
            </div>
          </div>

          <div className="space-y-3 text-xs">
            <div className="rounded-lg border border-[var(--color-line)] p-2">
              <div className="mb-1 flex items-center gap-2 font-medium"><Mic size={13} className="text-cyan-300" /> Speech to text</div>
              {!ff.transcribe ? (
                <p className="text-[var(--color-mute)]">Not available in this install. The desktop app includes it.</p>
              ) : (
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <select className="input !w-auto !py-1" aria-label="Speech model" value={model} onChange={(e) => setModel(e.target.value)}>
                      {home.models.map((m) => <option key={m.name} value={m.name}>{m.label}{m.installed ? " ✓" : ""}</option>)}
                    </select>
                    {home.models.find((m) => m.name === model)?.installed ? (
                      <button className="btn !py-1" disabled={!o.file || busy === "tr"} onClick={() => act("tr", () => client.post("/api/video/transcribe", { file: o.file, model }))}>Transcribe</button>
                    ) : (
                      <button className="btn !py-1" onClick={() => act("dl", () => client.post(`/api/video/models/${model}`), "Downloading the model")}><Download size={11} /> Download model</button>
                    )}
                  </div>
                  {installed.length === 0 && <p className="text-[var(--color-mute)]">The model is downloaded once from the public whisper.cpp model page and then works offline on this computer.</p>}
                  {tr && (
                    <>
                      <div className="max-h-40 space-y-0.5 overflow-auto rounded border border-[var(--color-line)] p-1">
                        {tr.segments.map((s, i) => (
                          <button key={i} className="block w-full rounded px-1 text-left hover:bg-white/5" title="Use as the cut" onClick={() => setO((x) => ({ ...x, start: String(s.start), end: String(s.end) }))}>
                            <span className="font-mono text-cyan-300">{t(s.start)}</span> {s.text}
                          </button>
                        ))}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[var(--color-mute)]">{tr.clips.length} short clips found at sentence ends</span>
                        <button className="btn !py-0.5" onClick={exportClips}><Scissors size={11} /> Export all clips</button>
                      </div>
                      <div className="space-y-2 rounded border border-cyan-400/30 p-2">
                        <div className="font-medium text-cyan-200">Shorts package</div>
                        <p className="text-[var(--color-mute)]">Picks the best moments, makes vertical clips with animated captions and a thumbnail, and writes a title, hook and hashtags as drafts for each platform. Nothing is posted until you approve it.</p>
                        <div className="flex flex-wrap items-center gap-2">
                          <label className="flex items-center gap-1">Clips <input className="input !w-14 !py-1" type="number" min={1} max={5} value={pkg.count} onChange={(e) => setPkg({ ...pkg, count: Number(e.target.value) })} /></label>
                          <select className="input !w-auto !py-1" aria-label="Package caption animation" value={pkg.animation} onChange={(e) => setPkg({ ...pkg, animation: e.target.value })}>
                            <option value="pop">Pop in</option><option value="karaoke">Word by word</option><option value="fade">Fade in</option><option value="none">No animation</option>
                          </select>
                        </div>
                        <div className="flex flex-wrap gap-1">
                          {PKG_PLATFORMS.map((p) => (
                            <label key={p} className={`chip cursor-pointer ${pkg.platforms.includes(p) ? "!border-cyan-400 !text-cyan-200" : ""}`}>
                              <input type="checkbox" className="sr-only" checked={pkg.platforms.includes(p)} onChange={(e) => setPkg({ ...pkg, platforms: e.target.checked ? [...pkg.platforms, p] : pkg.platforms.filter((x) => x !== p) })} />{p}
                            </label>
                          ))}
                        </div>
                        <button className="btn btn-primary !py-1" disabled={busy === "pkg" || pkg.platforms.length === 0} onClick={() => act("pkg", () => client.post("/api/video/package", { file: o.file, ...pkg }), "Making your Shorts package")}>
                          <Scissors size={11} /> Make Shorts package
                        </button>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <button className="btn !py-0.5" onClick={() => talkTo("u", `U, pick the 3 best moments for Shorts from this transcript and give start/end seconds and a hook for each:\n${tr.segments.map((s) => `[${s.start}-${s.end}] ${s.text}`).join("\n").slice(0, 6000)}`)}>Ask U for the best moments</button>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>

            <div className="hud-label text-[var(--color-mute)]">Jobs</div>
            {home.jobs.length === 0 && <p className="text-[var(--color-mute)]">Nothing yet.</p>}
            {home.jobs.map((j) => (
              <div key={j.id} className="rounded-lg border border-[var(--color-line)] p-2">
                <div className="flex items-center gap-2">
                  <span className={j.status === "done" ? "text-emerald-300" : j.status === "failed" ? "text-red-300" : j.status === "cancelled" ? "text-[var(--color-mute)]" : "text-cyan-300"}>{KIND[j.kind] || j.kind} · {j.status}</span>
                  <span className="min-w-0 flex-1 truncate">{j.output || j.file}</span>
                  {j.measured_loudness && <span className="text-[11px] text-cyan-400" title={`True peak: ${j.measured_loudness.true_peak_db} dB`}>({j.measured_loudness.integrated_lufs} LUFS)</span>}
                  {(j.status === "running" || j.status === "queued") && <button className="btn !py-0.5" onClick={() => act("", () => client.del(`/api/video/jobs/${j.id}`))} aria-label="Cancel"><X size={11} /></button>}
                  {j.status === "done" && j.output && j.kind !== "model" && <button className="btn !py-0.5" onClick={() => download(j.output!)} aria-label={`Download ${j.output}`}><Download size={11} /></button>}
                </div>
                {(j.status === "running" || j.status === "queued") && (
                  <div className="mt-1 h-1.5 overflow-hidden rounded bg-white/10" role="progressbar" aria-valuenow={Math.round(j.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
                    <div className="h-full bg-cyan-400 transition-all" style={{ width: `${Math.round(j.progress * 100)}%` }} />
                  </div>
                )}
                {j.captions && <button className="mt-1 text-cyan-300 underline" onClick={() => download(j.captions!)}>{j.captions}</button>}
                {j.kind === "package" && (j.clips || []).map((c) => (
                  <div key={c.video} className="mt-1 rounded border border-[var(--color-line)] p-1.5">
                    <div className="font-medium">{c.title}</div>
                    <div className="text-[var(--color-mute)]">{t(c.start)}–{t(c.end)} · {c.hashtags.join(" ")}</div>
                    <div className="mt-1 flex flex-wrap gap-2">
                      <button className="text-cyan-300 underline" onClick={() => download(c.video)}>Clip</button>
                      <span className="text-emerald-300">{c.drafts.filter((d) => d.id).length} drafts in Content</span>
                      {c.drafts.filter((d) => d.error).map((d) => <span key={d.platform} className="text-red-300">{d.platform}: {d.error}</span>)}
                    </div>
                  </div>
                ))}
                {j.kind === "package" && j.picked_by && <p className="mt-1 text-[var(--color-mute)]">Moments picked {j.picked_by === "agent" ? "by U" : "by a rule (no AI model connected)"}.</p>}
                {j.error && <p className="mt-1 break-anywhere text-red-300">{j.error}</p>}
              </div>
            ))}

            {videos.length > 0 && (
              <>
                <div className="hud-label pt-1 text-[var(--color-mute)]">Files</div>
                {home.files.map((f) => (
                  <div key={f.name} className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate">{f.name}</span>
                    <span className="text-[var(--color-mute)]">{mb(f.bytes)}</span>
                    <button className="btn !py-0.5" onClick={() => download(f.name)} aria-label={`Download ${f.name}`}><Download size={11} /></button>
                    <button className="btn !py-0.5" onClick={() => act("", () => client.del(`/api/video/file?name=${encodeURIComponent(f.name)}`))} aria-label={`Delete ${f.name}`}><Trash2 size={11} /></button>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      )}

      {showDownloadModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div className="w-full max-w-lg rounded-xl border border-[var(--color-line)] bg-[var(--color-bg)] p-5 shadow-2xl">
            <div className="flex items-center justify-between pb-3 border-b border-[var(--color-line)]">
              <div className="flex items-center gap-2 font-medium text-cyan-200">
                <Globe size={16} /> Download Media from Web
              </div>
              <button className="btn !p-1 text-[var(--color-mute)] hover:text-white" onClick={() => setShowDownloadModal(false)} aria-label="Close">
                <X size={15} />
              </button>
            </div>
            <div className="mt-4 space-y-3 text-xs">
              <p className="text-[var(--color-mute)]">
                Acquire public videos, audio or playlists directly to your local library. Supports direct MP4, public social videos, and audio extractions.
              </p>
              <div>
                <div className="flex items-center justify-between">
                  <span className="hud-label text-[var(--color-mute)]">Source URL</span>
                  <button
                    className="text-[11px] text-cyan-300 hover:text-cyan-200 disabled:opacity-40"
                    disabled={!dlUrl.trim() || analyzing}
                    onClick={analyzeWebUrl}
                  >
                    {analyzing ? "Inspecting..." : "Inspect Metadata"}
                  </button>
                </div>
                <input
                  className="input mt-1 w-full text-xs"
                  placeholder="https://... (video, playlist or post link)"
                  value={dlUrl}
                  onChange={(e) => {
                    setDlUrl(e.target.value);
                    setMetaInfo(null);
                  }}
                />
              </div>

              {metaInfo && (
                <div className="rounded-lg border border-cyan-800/40 bg-cyan-950/20 p-2.5 space-y-1 text-xs">
                  <div className="font-medium text-cyan-200 truncate">{metaInfo.title || "Media Stream"}</div>
                  {metaInfo.author && <div className="text-[var(--color-mute)]">Channel/Author: {metaInfo.author}</div>}
                  {metaInfo.duration > 0 && (
                    <div className="text-[var(--color-mute)]">Duration: {Math.floor(metaInfo.duration / 60)}m {Math.floor(metaInfo.duration % 60)}s</div>
                  )}
                  {metaInfo.is_playlist && (
                    <div className="text-emerald-300 font-mono">Playlist: {metaInfo.entries_count || 0} entries</div>
                  )}
                </div>
              )}
              <div className="grid grid-cols-2 gap-2">
                <label>
                  <span className="hud-label text-[var(--color-mute)]">Max Resolution</span>
                  <select className="input mt-1 w-full text-xs" value={dlRes} onChange={(e) => setDlRes(e.target.value)} disabled={dlAudio}>
                    <option value="">Best Available</option>
                    <option value="1080">1080p Full HD</option>
                    <option value="720">720p HD</option>
                    <option value="480">480p SD</option>
                  </select>
                </label>
                <div className="flex items-end pb-2">
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input type="checkbox" checked={dlAudio} onChange={(e) => setDlAudio(e.target.checked)} />
                    <span>Audio only (.mp3)</span>
                  </label>
                </div>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button className="btn" onClick={() => setShowDownloadModal(false)}>Close</button>
                <button className="btn btn-primary" disabled={!dlUrl.trim() || busy === "dl-start"} onClick={startWebDownload}>
                  {busy === "dl-start" ? <LoaderCircle size={13} className="animate-spin" /> : <DownloadCloud size={13} />} Start Download
                </button>
              </div>

              {dlJobs.length > 0 && (
                <div className="mt-3 border-t border-[var(--color-line)] pt-3 space-y-2">
                  <span className="hud-label text-[var(--color-mute)]">Recent Downloads</span>
                  {dlJobs.map((j) => (
                    <div key={j.id} className="rounded border border-[var(--color-line)] p-2">
                      <div className="flex items-center justify-between">
                        <span className={`font-mono ${j.status === "done" ? "text-emerald-300" : j.status === "failed" ? "text-red-300" : j.status === "interrupted" ? "text-amber-300" : "text-cyan-300"}`}>
                          {j.status} {j.speed ? `· ${j.speed}` : ""} {j.eta ? `· ${j.eta}` : ""}
                        </span>
                        {(j.status === "running" || j.status === "queued") && (
                          <button className="btn !py-0.5 !px-1.5 text-red-300 hover:text-red-200" onClick={() => cancelWebDownload(j.id)}>Cancel</button>
                        )}
                      </div>
                      <div className="truncate text-[var(--color-mute)] mt-0.5">{j.output || j.title || j.url}</div>
                      {j.playlist_count > 0 && (
                        <div className="text-[11px] font-mono text-cyan-400">Playlist: {j.playlist_index || 0} / {j.playlist_count}</div>
                      )}
                      {(j.status === "running" || j.status === "queued") && (
                        <div className="mt-1 h-1 overflow-hidden rounded bg-white/10">
                          <div className="h-full bg-cyan-400 transition-all" style={{ width: `${Math.round(j.progress * 100)}%` }} />
                        </div>
                      )}
                      {j.error && <p className="text-red-300 mt-1 break-words">{j.error}</p>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </Section>
  );
}
