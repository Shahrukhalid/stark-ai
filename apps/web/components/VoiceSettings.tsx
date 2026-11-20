"use client";

import { useEffect, useState } from "react";
import { isDesktop } from "@/lib/api";
import { useStark } from "@/lib/store";
import { micErrorText, voiceSpeak, type VoiceChoices, type VoiceDiagnostics, type VoiceStatus } from "@/lib/voice";
import { AgentGlyph } from "./ui";
import { Section } from "./ui";

const KEEP = "__keep__";
const sample = (name: string) => `Hello, I'm ${name}. السلام علیکم، آپ کی آج کی پوسٹس تیار ہیں۔`;
const STYLE_LABEL: Record<string, string> = { "": "Assistant's own", calm: "Calm", warm: "Warm", energetic: "Energetic", professional: "Professional", gentle: "Gentle", natural: "Natural" };

/** STARK voice: talk to Vision and Wanda in English or Urdu with your own Gemini key. */
export function VoiceSettings() {
  const { client, notify, agents } = useStark();
  const [choices, setChoices] = useState<VoiceChoices | null>(null);
  const [st, setSt] = useState<VoiceStatus | null>(null);
  const [key, setKey] = useState("");
  const [engine, setEngine] = useState("gemini");
  const [language, setLanguage] = useState("auto");
  const [script, setScript] = useState("urdu");
  const [fast, setFast] = useState(false);
  const [busy, setBusy] = useState("");
  const [diag, setDiag] = useState<(VoiceDiagnostics & { browserMics?: string[]; peak?: number; micError?: string }) | null>(null);

  /** Real-device check: what the engine and this page can actually see and hear (records 2 s, keeps nothing). */
  async function check() {
    setBusy("check");
    try {
      const d = await client.get<VoiceDiagnostics>("/api/voice/diagnostics");
      const extra: { browserMics?: string[]; peak?: number; micError?: string } = {};
      if (!d.desktop && navigator.mediaDevices?.getUserMedia) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          const ctx = new AudioContext();
          const an = ctx.createAnalyser();
          ctx.createMediaStreamSource(stream).connect(an);
          const buf = new Float32Array(an.fftSize);
          let peak = 0;
          const until = Date.now() + 2000;
          while (Date.now() < until) {
            an.getFloatTimeDomainData(buf);
            for (const v of buf) peak = Math.max(peak, Math.abs(v));
            await new Promise((r) => setTimeout(r, 50));
          }
          stream.getTracks().forEach((t) => t.stop());
          await ctx.close();
          extra.peak = peak;
          extra.browserMics = (await navigator.mediaDevices.enumerateDevices()).filter((x) => x.kind === "audioinput").map((x) => x.label || "Microphone");
        } catch (e) {
          extra.micError = micErrorText(e);
        }
      }
      setDiag({ ...d, ...extra });
    } catch (e: any) {
      notify("error", e.message);
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    client
      .get<VoiceStatus>("/api/voice/status")
      .then((s) => {
        setSt(s);
        setLanguage(s.language || "auto");
        setScript(s.urdu_script || "urdu");
      })
      .catch(() => setSt(null));
    client.get<VoiceChoices>("/api/voice/voices").then(setChoices).catch(() => setChoices(null));
    if (isDesktop())
      client
        .get<{ secrets: Record<string, boolean>; settings: Record<string, string> }>("/api/desktop/status")
        .then((d) => {
          if (d.secrets.GEMINI_API_KEY) setKey(KEEP);
          setEngine(d.settings.STARK_VOICE_ENGINE || "gemini");
          setFast(d.settings.STARK_VOICE_FAST === "true");
        })
        .catch(() => undefined);
  }, [client]);

  async function save() {
    setBusy("save");
    try {
      await client.put("/api/desktop/settings", {
        values: { GEMINI_API_KEY: key, STARK_VOICE_ENGINE: engine === "gemini" ? "" : engine, STARK_VOICE_LANGUAGE: language === "auto" ? "" : language, STARK_VOICE_URDU_SCRIPT: script === "urdu" ? "" : script, STARK_VOICE_FAST: fast ? "true" : "" },
        restart: true,
      });
      notify("success", "Saved. STARK AI is restarting with the new voice settings.");
    } catch (e: any) {
      notify("error", e.message);
    } finally {
      setBusy("");
    }
  }

  async function choose(agentId: string, voice: string, style: string) {
    try {
      await client.put("/api/voice/prefs", { agent_id: agentId, voice, style });
      setChoices((c) => (c ? { ...c, agents: { ...c.agents, [agentId]: { ...c.agents[agentId], voice: voice || c.agents[agentId].default, style } } } : c));
    } catch (e: any) {
      notify("error", e.message);
    }
  }

  async function test(agentId: string, name: string) {
    const pick = choices?.agents[agentId];
    setBusy(agentId);
    try {
      const agent = agents.find((a) => a.id === agentId);
      await voiceSpeak(client, agentId, sample(name), pick ? { voice: pick.voice, style: pick.style } : undefined, agent?.voice);
    } catch (e: any) {
      notify("error", e.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <Section title="STARK voice">
      <p className="mb-3 text-sm text-[var(--color-mute)]">
        Talk to Vision and Wanda in English or Urdu, each in their own voice. Uses your own Google Gemini API key (the free tier works).
        Recordings are sent to Google only while you hold the talk button; free-tier data may be used by Google to improve its services.
        Approvals always stay on screen.
      </p>
      <p className="mb-3 text-xs">
        Status:{" "}
        {st?.engine === "gemini" ? (
          <span className="text-emerald-400">on{st.local_audio ? " (this computer's microphone and speakers)" : " (this browser records and plays)"}</span>
        ) : (
          <span className="text-[var(--color-mute)]">off: the browser&apos;s own speech is used</span>
        )}
      </p>
      {isDesktop() ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block sm:col-span-2">
            <span className="hud-label text-[var(--color-mute)]">Gemini API key (aistudio.google.com/apikey)</span>
            <input className="input mt-1 w-full font-mono" type="password" autoComplete="off" value={key === KEEP ? "" : key}
              placeholder={key === KEEP ? "saved (leave empty to keep)" : "paste your key"}
              onChange={(e) => setKey(e.target.value || (key === KEEP ? KEEP : ""))} />
          </label>
          <label className="block">
            <span className="hud-label text-[var(--color-mute)]">Voice</span>
            <select className="input mt-1 w-full" value={engine} onChange={(e) => setEngine(e.target.value)}>
              <option value="gemini">STARK voice (Gemini)</option>
              <option value="browser">Browser speech</option>
            </select>
          </label>
          <label className="block">
            <span className="hud-label text-[var(--color-mute)]">Language I speak</span>
            <select className="input mt-1 w-full" value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="auto">English and Urdu (automatic)</option>
              <option value="en">English</option>
              <option value="ur">اردو Urdu</option>
            </select>
          </label>
          <label className="block">
            <span className="hud-label text-[var(--color-mute)]">Write Urdu as</span>
            <select className="input mt-1 w-full" value={script} onChange={(e) => setScript(e.target.value)}>
              <option value="urdu">اردو (Urdu script)</option>
              <option value="roman">Roman Urdu</option>
            </select>
          </label>
          <label className="flex items-start gap-2 text-sm sm:col-span-2">
            <input type="checkbox" className="mt-1" checked={fast} onChange={(e) => setFast(e.target.checked)} />
            <span>
              <b>Fast mode</b>: live voice for near-instant replies (Gemini live). Uses more of your Gemini quota; if it can&apos;t connect, the normal voice is used.
            </span>
          </label>
          <div className="flex items-end">
            <button type="button" className="btn btn-primary" disabled={busy === "save" || (engine === "gemini" && !key)} onClick={save}
              title={engine === "gemini" && !key ? "Paste a Gemini key, or choose Browser speech" : undefined}>
              {busy === "save" ? "Saving…" : "Save and restart"}
            </button>
          </div>
        </div>
      ) : (
        <p className="text-sm text-[var(--color-mute)]">
          Set <code className="font-mono">GEMINI_API_KEY</code> (and optionally <code className="font-mono">STARK_VOICE_LANGUAGE=ur</code>) in .env and restart the API.
        </p>
      )}
      <div className="mt-4 rounded-xl border border-[var(--color-line)] bg-white/[0.02] p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="hud-label text-[var(--color-mute)]">Voice check</span>
          <button type="button" className="btn ml-auto" disabled={busy === "check"} onClick={check}>
            {busy === "check" ? "Listening for 2 seconds…" : "Run voice check"}
          </button>
        </div>
        {diag && (
          <ul className="mt-2 space-y-1 text-xs" aria-live="polite">
            <li>Engine: <b>{diag.engine === "gemini" ? "STARK voice (Gemini)" : "Browser speech"}</b>{diag.key ? "" : " · no Gemini key"}</li>
            <li>Offline speech-to-text: {diag.offline_stt ? <b className="text-emerald-400">ready ({diag.offline_model})</b> : "not installed (download a speech model in Video studio)"}</li>
            {diag.desktop ? (
              <>
                <li className={diag.ok ? "text-emerald-400" : "text-amber-300"}>{diag.ok ? `Microphone: ${diag.default_input} · Speaker: ${diag.default_output}` : diag.reason}</li>
                {diag.inputs.length > 1 && <li className="text-[var(--color-mute)]">Other microphones: {diag.inputs.filter((x) => x !== diag.default_input).join(", ")}</li>}
              </>
            ) : diag.micError ? (
              <li className="text-amber-300">{diag.micError}</li>
            ) : (
              <li className={diag.peak && diag.peak > 0.02 ? "text-emerald-400" : "text-amber-300"}>
                {diag.peak && diag.peak > 0.02 ? "Microphone heard you." : "The microphone is on but heard nothing; check the input level or pick another microphone."}
                {diag.browserMics?.length ? ` (${diag.browserMics.join(", ")})` : ""}
              </li>
            )}
          </ul>
        )}
      </div>
      {st?.engine === "gemini" && choices && (
        <div className="mt-4 space-y-2">
          <p className="hud-label text-[var(--color-mute)]">Voices: pick one for each assistant and press Hear</p>
          {agents
            .filter((a) => choices.agents[a.id])
            .map((a) => {
              const pick = choices.agents[a.id];
              return (
                <div key={a.id} className="flex flex-wrap items-center gap-2 text-sm">
                  <AgentGlyph icon={a.icon} color={a.color} size={16} />
                  <span className="w-24 truncate" style={{ color: a.color }}>{a.name}</span>
                  <select className="input min-w-0 flex-1" aria-label={`${a.name} voice`} value={pick.voice}
                    onChange={(e) => choose(a.id, e.target.value === pick.default ? "" : e.target.value, pick.style)}>
                    {choices.voices.map((v) => (
                      <option key={v.name} value={v.name}>{v.name} · {v.character}{v.name === pick.default ? " (default)" : ""}</option>
                    ))}
                  </select>
                  <select className="input w-36" aria-label={`${a.name} speaking style`} value={pick.style}
                    onChange={(e) => choose(a.id, pick.voice === pick.default ? "" : pick.voice, e.target.value)}>
                    {["", ...choices.styles].map((s) => (
                      <option key={s} value={s}>{STYLE_LABEL[s] ?? s}</option>
                    ))}
                  </select>
                  <button type="button" className="btn" disabled={!!busy} onClick={() => test(a.id, a.name)}>
                    {busy === a.id ? "Speaking…" : "Hear"}
                  </button>
                </div>
              );
            })}
        </div>
      )}
    </Section>
  );
}
