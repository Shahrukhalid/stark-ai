"use client";

import { useEffect, useState } from "react";
import { isDesktop } from "@/lib/api";
import { useStark } from "@/lib/store";
import { Section } from "./ui";

interface Status {
  available: boolean;
  enabled: boolean;
  allowed: boolean;
}

/** Computer control and the shortcut from any app (desktop app only). */
export function ComputerSettings() {
  const { client, notify } = useStark();
  const [st, setSt] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const mac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform || navigator.userAgent);

  const load = () => client.get<Status>("/api/computer/status").then(setSt).catch(() => setSt(null));
  useEffect(() => {
    if (isDesktop()) load();
  }, [client]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!isDesktop()) return null;

  async function toggle(on: boolean) {
    setBusy(true);
    try {
      await client.put("/api/desktop/settings", { values: { STARK_COMPUTER_CONTROL: on ? "true" : "" }, restart: true });
      notify("success", on ? "Computer control is on. STARK AI is restarting." : "Computer control is off. STARK AI is restarting.");
    } catch (e: any) {
      notify("error", e.message);
    } finally {
      setBusy(false);
    }
  }

  async function allow() {
    try {
      const r = await client.post<{ allowed: boolean }>("/api/computer/allow");
      if (!r.allowed) notify("info", "In System Settings → Privacy & Security → Accessibility, turn on STARK AI, then come back.");
      load();
    } catch (e: any) {
      notify("error", e.message);
    }
  }

  return (
    <Section title="Computer control">
      <p className="mb-3 text-sm text-[var(--color-mute)]">
        Let Vision and Wanda use your apps: see an app&apos;s buttons and fields, click, type and press shortcuts. Every single action
        shows an approval card first, and E-STOP blocks them all. They never type passwords or payment details.
      </p>
      <p className="mb-3 text-sm">
        Shortcut from any app: <span className="rounded bg-white/10 px-1.5 py-0.5 font-mono text-xs">{mac ? "⌥ Option + ⇧ Shift + Space" : "Alt + Shift + Space"}</span> opens
        the full-screen assistant.
      </p>
      {st && !st.available ? (
        <p className="text-sm text-[var(--color-mute)]">Not available on this computer.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className={`btn ${st?.enabled ? "" : "btn-primary"}`} disabled={busy || !st} onClick={() => toggle(!st?.enabled)}>
            {st?.enabled ? "Turn off" : "Turn on computer control"}
          </button>
          {st?.enabled && mac && !st.allowed && (
            <button type="button" className="btn" onClick={allow}>Allow in System Settings</button>
          )}
          {st?.enabled && (
            <span className={`text-sm ${st.allowed ? "text-emerald-400" : "text-amber-300"}`}>
              {st.allowed ? "On and allowed" : "On: allow STARK AI under Accessibility"}
            </span>
          )}
        </div>
      )}
    </Section>
  );
}
