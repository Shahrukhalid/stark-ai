"use client";

import { useEffect, useState } from "react";
import { useStark } from "@/lib/store";
import { Section } from "./ui";

interface Routines {
  quiet: { enabled: boolean; start: string; end: string };
  summary: { enabled: boolean; time: string; agent: "vision" | "wanda"; language: "en" | "ur" };
  quiet_now?: boolean;
}

/** Quiet hours and the spoken daily summary. Routines themselves are made by asking Vision or Wanda. */
export function RoutinesSettings() {
  const { client, notify } = useStark();
  const [r, setR] = useState<Routines | null>(null);

  useEffect(() => {
    client.get<Routines>("/api/assistant/routines").then(setR).catch(() => setR(null));
  }, [client]);

  if (!r) return null;

  async function save(next: Routines) {
    setR(next);
    try {
      setR(await client.put<Routines>("/api/assistant/routines", { quiet: next.quiet, summary: next.summary }));
    } catch (e: any) {
      notify("error", e.message);
    }
  }

  async function hear() {
    try {
      const out = await client.post<{ text: string; spoken: boolean }>("/api/assistant/summary");
      notify("info", out.text);
    } catch (e: any) {
      notify("error", e.message);
    }
  }

  const time = (value: string, on: (v: string) => void, label: string) => (
    <input type="time" className="input w-28" aria-label={label} value={value} onChange={(e) => e.target.value && on(e.target.value)} />
  );

  return (
    <Section title="Routines and quiet hours">
      <p className="mb-3 text-sm text-[var(--color-mute)]">
        Ask for routines by voice or text, e.g. &quot;Wanda, every Monday at 9 draft three posts&quot;. They appear under Automations, where you
        can switch them off. They never publish on their own.
      </p>
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={r.quiet.enabled} onChange={(e) => save({ ...r, quiet: { ...r.quiet, enabled: e.target.checked } })} />
            Quiet hours from
          </label>
          {time(r.quiet.start, (v) => save({ ...r, quiet: { ...r.quiet, start: v } }), "Quiet hours start")}
          to
          {time(r.quiet.end, (v) => save({ ...r, quiet: { ...r.quiet, end: v } }), "Quiet hours end")}
          {r.quiet_now && <span className="text-xs text-amber-300">quiet now</span>}
        </div>
        <p className="text-xs text-[var(--color-mute)]">No pop-ups or spoken summaries during quiet hours; reminders wait until they end.</p>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={r.summary.enabled} onChange={(e) => save({ ...r, summary: { ...r.summary, enabled: e.target.checked } })} />
            Daily summary at
          </label>
          {time(r.summary.time, (v) => save({ ...r, summary: { ...r.summary, time: v } }), "Daily summary time")}
          by
          <select className="input w-28" aria-label="Summary assistant" value={r.summary.agent} onChange={(e) => save({ ...r, summary: { ...r.summary, agent: e.target.value as "vision" | "wanda" } })}>
            <option value="vision">Vision</option>
            <option value="wanda">Wanda</option>
          </select>
          in
          <select className="input w-28" aria-label="Summary language" value={r.summary.language} onChange={(e) => save({ ...r, summary: { ...r.summary, language: e.target.value as "en" | "ur" } })}>
            <option value="en">English</option>
            <option value="ur">اردو</option>
          </select>
          <button type="button" className="btn" onClick={hear}>Hear it now</button>
        </div>
        <p className="text-xs text-[var(--color-mute)]">Spoken in the desktop app when STARK voice is on; always shown as a notification.</p>
      </div>
    </Section>
  );
}
