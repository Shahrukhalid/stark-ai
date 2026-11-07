"use client";

import { Mic, Minimize2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AvatarState } from "@/lib/particles";
import type { Agent } from "@/lib/types";
import { ParticlePresence } from "./ParticlePresence";

/** Full-screen assistant: the presence, a live waveform, the latest reply and a big talk button. */
export function AssistantFocus({
  agent, state, level, partial, reply, listening, approvals, onTalkStart, onTalkStop, onClose,
}: {
  agent?: Agent;
  state: AvatarState;
  level: number;
  partial: string;
  reply: string;
  listening: boolean;
  approvals: number;
  onTalkStart: () => void;
  onTalkStop: () => void;
  onClose: () => void;
}) {
  const [bars, setBars] = useState<number[]>(() => Array(32).fill(0));
  const lv = useRef(level);
  lv.current = level;
  const color = agent?.color || "#22d3ee";

  useEffect(() => {
    const id = setInterval(() => setBars((b) => [...b.slice(1), lv.current]), 60);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.code === "Space" && !e.repeat && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault();
        onTalkStart();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        e.preventDefault();
        onTalkStop();
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [onClose, onTalkStart, onTalkStop]);

  if (typeof document === "undefined") return null;
  // A portal keeps the overlay full screen even inside panels that create their own stacking context.
  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-4 overflow-y-auto bg-[#05080d]/95 p-6 backdrop-blur" role="dialog" aria-modal="true" aria-label={`${agent?.name || "Assistant"} full screen`}>
      <button type="button" className="btn absolute right-4 top-4" onClick={onClose} title="Exit full screen (Esc)">
        <Minimize2 size={14} /> Exit
      </button>
      <ParticlePresence agent={agent} state={state} level={level} large />
      <div className="flex h-10 items-end gap-[3px]" aria-hidden>
        {bars.map((b, i) => (
          <span key={i} className="w-1.5 rounded-full transition-[height] duration-75" style={{ height: `${6 + Math.min(1, b * 2.2) * 34}px`, background: color, opacity: 0.35 + Math.min(0.65, b * 2) }} />
        ))}
      </div>
      <p dir="auto" className="min-h-[3rem] max-w-2xl text-center text-lg leading-relaxed text-white/90">
        {partial || (reply.length > 320 ? `${reply.slice(0, 320)}…` : reply) || `Hold the button or Space and talk to ${agent?.name || "me"}.`}
      </p>
      {approvals > 0 && (
        <button type="button" className="btn" onClick={onClose}>
          {approvals} approval{approvals > 1 ? "s" : ""} waiting: review on screen
        </button>
      )}
      <button
        type="button"
        className="flex h-20 w-20 items-center justify-center rounded-full border-2 transition"
        style={{ borderColor: color, background: listening ? `${color}33` : "transparent", boxShadow: listening ? `0 0 40px ${color}66` : "none" }}
        onMouseDown={onTalkStart}
        onMouseUp={onTalkStop}
        onMouseLeave={() => listening && onTalkStop()}
        onTouchStart={onTalkStart}
        onTouchEnd={onTalkStop}
        aria-pressed={listening}
        aria-label="Hold to talk"
      >
        <Mic size={28} style={{ color }} />
      </button>
      <p className="text-xs text-[var(--color-mute)]">Hold to talk · Space · Esc to exit · Ctrl/⌘+Shift+Space opens this view · ⌥/Alt+Shift+Space from any app (desktop)</p>
    </div>,
    document.body,
  );
}
