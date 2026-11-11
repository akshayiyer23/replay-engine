"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentEvent, RunInput, WebinarResults } from "@/lib/api";
import { API_BASE } from "@/lib/api";

// ─── Step definitions ─────────────────────────────────────────────────────────

const DISPLAY_STEPS = [
  { key: "download",     icon: "🎧", label: "Downloading" },
  { key: "transcribe",   icon: "✍️",  label: "Transcribing" },
  { key: "speakers",     icon: "👥", label: "Identifying speakers" },
  { key: "housekeeping", icon: "🧹", label: "Filtering content" },
  { key: "mining",       icon: "🔍", label: "Scanning for moments" },
  { key: "judge",        icon: "⚖️",  label: "Scoring vs 861 real GTM posts" },
  { key: "content",      icon: "✍️",  label: "Writing posts" },
  { key: "done",         icon: "✅", label: "Done" },
] as const;

type StepKey = typeof DISPLAY_STEPS[number]["key"];

// SSE event → which display step becomes active (the step AFTER the completed one)
const SSE_ACTIVATES: Partial<Record<string, StepKey>> = {
  start: "download",
  download: "transcribe",
  transcribe: "speakers",
  speakers: "housekeeping",
  housekeeping: "mining",
  judge: "judge",
  content: "content",
  cache: "done",
};

// SSE event → which display step is now DONE
const SSE_COMPLETES: Partial<Record<string, StepKey>> = {
  download: "download",
  transcribe: "transcribe",
  speakers: "speakers",
  housekeeping: "housekeeping",
  judge: "mining",
  content: "judge",
  cache: "content",
  done: "done",
};

// ─── Run state ────────────────────────────────────────────────────────────────

type StepStatus = "pending" | "active" | "done";

interface StepState {
  status: StepStatus;
  message: string;
}

const INITIAL_STEPS: Record<StepKey, StepState> = Object.fromEntries(
  DISPLAY_STEPS.map((s) => [s.key, { status: "pending" as StepStatus, message: "" }])
) as Record<StepKey, StepState>;

function applyEvent(
  steps: Record<StepKey, StepState>,
  evt: AgentEvent
): Record<StepKey, StepState> {
  const next = { ...steps };

  const completes = SSE_COMPLETES[evt.step] as StepKey | undefined;
  if (completes) {
    next[completes] = { status: "done", message: evt.message };
  }

  const activates = SSE_ACTIVATES[evt.step] as StepKey | undefined;
  if (activates && next[activates].status === "pending") {
    next[activates] = { status: "active", message: "" };
  }

  return next;
}

// ─── Typewriter hook ──────────────────────────────────────────────────────────

function useTypewriter(lines: string[]) {
  const [displayed, setDisplayed] = useState<string[]>([]);
  const [typingIdx, setTypingIdx] = useState(0);
  const prevLen = useRef(0);

  useEffect(() => {
    if (lines.length <= prevLen.current) return;
    prevLen.current = lines.length;

    // New line to type
    const newLine = lines[lines.length - 1];
    setTypingIdx(0);

    let i = 0;
    const id = setInterval(() => {
      i += 3; // chars per tick
      setTypingIdx(i);
      if (i >= newLine.length) {
        clearInterval(id);
        setDisplayed((prev) => [...prev, newLine].slice(-4)); // keep last 4
        setTypingIdx(newLine.length + 1); // mark complete
      }
    }, 25);

    return () => clearInterval(id);
  }, [lines.length]); // eslint-disable-line

  const currentLine =
    lines.length > 0 && typingIdx <= lines[lines.length - 1].length
      ? lines[lines.length - 1].slice(0, typingIdx)
      : null;

  return { displayed, currentLine };
}

// ─── Component ────────────────────────────────────────────────────────────────

interface AgentRunViewProps {
  mode: "live" | "replay";
  // live mode
  input?: RunInput;
  // replay mode
  replayEvents?: AgentEvent[];
  replaySlug?: string;
  // callbacks
  onDone: (results: WebinarResults | null, slug: string | null) => void;
  onError: (msg: string) => void;
  onClose: () => void;
  isReplay?: boolean;
}

const REPLAY_DURATION_MS = 25000; // compress replay to 25 seconds

export default function AgentRunView({
  mode,
  input,
  replayEvents,
  replaySlug,
  onDone,
  onError,
  onClose,
  isReplay = false,
}: AgentRunViewProps) {
  const [steps, setSteps] = useState<Record<StepKey, StepState>>(INITIAL_STEPS);
  const [percent, setPercent] = useState(0);
  const [hookLines, setHookLines] = useState<string[]>([]);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [etaMs, setEtaMs] = useState<number | null>(null);
  const [isDone, setIsDone] = useState(false);

  const startTimeRef = useRef(Date.now());
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const slugRef = useRef<string | null>(replaySlug ?? null);
  const resultsRef = useRef<WebinarResults | null>(null);

  // Live elapsed timer
  useEffect(() => {
    timerRef.current = setInterval(() => {
      setElapsedMs(Date.now() - startTimeRef.current);
    }, 1000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const gotTerminalRef = useRef(false);

  const processEvent = useCallback(
    (evt: AgentEvent) => {
      if (evt.step === "stream_done") return;
      if (evt.step !== "mining_hook") console.log("[SSE]", evt.step, evt.message ?? "");

      setPercent(evt.percent);

      if (evt.step === "mining_hook") {
        setHookLines((prev) => [...prev, evt.message]);
        return;
      }

      if (evt.step === "done") {
        gotTerminalRef.current = true;
        if (evt.slug) slugRef.current = evt.slug;
        setSteps((prev) => applyEvent(prev, evt));
        setIsDone(true);
        return;
      }

      if (evt.step === "results") {
        const data = (evt as unknown as { data: WebinarResults & { slug: string } }).data;
        resultsRef.current = data as unknown as WebinarResults;
        return;
      }

      if (evt.step === "error") {
        gotTerminalRef.current = true;
        onError(evt.message || "Pipeline error");
        return;
      }

      setSteps((prev) => applyEvent(prev, evt));

      // Update ETA from event if available
      if ("eta_seconds" in evt && typeof (evt as unknown as Record<string, unknown>).eta_seconds === "number") {
        setEtaMs((evt as unknown as { eta_seconds: number }).eta_seconds * 1000);
      }
    },
    [onError]
  );

  // Trigger onDone after done state
  useEffect(() => {
    if (!isDone) return;
    const tid = setTimeout(() => {
      onDone(resultsRef.current, slugRef.current);
    }, 800);
    return () => clearTimeout(tid);
  }, [isDone, onDone]);

  // ── LIVE mode: SSE stream ─────────────────────────────────────────────────
  useEffect(() => {
    if (mode !== "live" || !input) return;

    const ctrl = new AbortController();

    async function stream() {
      try {
        const form = new FormData();
        if (input!.type === "url") form.set("url", input!.url);
        else if (input!.type === "transcript") form.set("transcript", (input as { type: "transcript"; text: string }).text);
        else form.set("file", (input as { type: "file"; file: File }).file);

        const res = await fetch(`${API_BASE}/run`, {
          method: "POST",
          headers: { "X-Passcode": input!.passcode },
          body: form,
          signal: ctrl.signal,
        });

        if (!res.ok) {
          const data = await res.json().catch(() => null);
          const detail = data?.detail ?? `HTTP ${res.status}`;
          if (res.status === 401) onError("wrong_passcode");
          else if (res.status === 429) onError("rate_limit");
          else onError(detail);
          return;
        }

        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buf = "";

        while (!ctrl.signal.aborted) {
          let chunk: ReadableStreamReadResult<Uint8Array>;
          try {
            chunk = await reader.read();
          } catch {
            break; // stream aborted or network error
          }
          if (chunk.done) break;
          buf += decoder.decode(chunk.value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop() ?? "";
          for (const part of parts) {
            // Skip SSE comments (keep-alive pings start with ":")
            if (part.trimStart().startsWith(":")) continue;
            const line = part.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            try {
              processEvent(JSON.parse(line.slice(6)));
            } catch {}
          }
        }

        // Stream ended without a done/error event — connection was dropped
        if (!ctrl.signal.aborted && !gotTerminalRef.current) {
          onError("stream_interrupted");
        }
      } catch (e: unknown) {
        const name = (e as Error)?.name;
        if (name !== "AbortError") {
          onError((e as Error).message || "Connection error");
        }
      }
    }

    stream();
    return () => ctrl.abort();
  }, []); // eslint-disable-line

  // ── REPLAY mode: schedule pre-recorded events ─────────────────────────────
  useEffect(() => {
    if (mode !== "replay" || !replayEvents || replayEvents.length === 0) return;

    const totalTs = replayEvents[replayEvents.length - 1].ts;
    const scale = REPLAY_DURATION_MS / Math.max(totalTs * 1000, 1);

    const timeouts: ReturnType<typeof setTimeout>[] = [];

    replayEvents.forEach((evt) => {
      const delay = Math.round(evt.ts * 1000 * scale);
      const tid = setTimeout(() => processEvent(evt), delay);
      timeouts.push(tid);
    });

    return () => timeouts.forEach(clearTimeout);
  }, []); // eslint-disable-line

  const { displayed: hookDisplayed, currentLine: hookCurrent } = useTypewriter(hookLines);

  // Format timers
  const elapsedSec = Math.floor(elapsedMs / 1000);
  const etaSec = etaMs !== null ? Math.max(0, Math.floor(etaMs / 1000)) : null;
  const timerLabel =
    etaSec !== null
      ? `${fmtTimer(elapsedSec)} elapsed · ~${fmtTimer(etaSec)} remaining`
      : `${fmtTimer(elapsedSec)} elapsed`;

  const showHooks = hookLines.length > 0;
  const activeKey = DISPLAY_STEPS.find((s) => steps[s.key].status === "active")?.key;

  return (
    <div className="rounded-2xl border border-[#E8E4DC] bg-white overflow-hidden">
      {/* Progress bar */}
      <div className="relative h-1 bg-[#F5F0E8]">
        <div
          className="absolute inset-y-0 left-0 bg-[#F15A24] transition-all duration-700"
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="px-6 py-5">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div>
            <p
              className="text-base font-medium text-[#1A1A1A]"
              style={{ fontFamily: "var(--font-serif)" }}
            >
              {isReplay ? "Replay of real run" : "Agent running…"}
            </p>
            <p className="text-xs text-[#6B6560] mt-0.5">{timerLabel}</p>
          </div>
          {isReplay && (
            <span className="text-[10px] uppercase tracking-wider font-semibold text-[#F15A24] border border-[#F15A24]/30 bg-[#FEF0EB] px-2 py-0.5 rounded-full">
              Replay
            </span>
          )}
          <button
            onClick={onClose}
            className="text-xs text-[#6B6560] hover:text-[#F15A24] border border-[#E8E4DC] rounded-lg px-3 py-1 transition-colors ml-3"
          >
            Close ✕
          </button>
        </div>

        {/* Step list */}
        <div className="space-y-1">
          {DISPLAY_STEPS.map((step) => {
            const state = steps[step.key];
            return (
              <div
                key={step.key}
                className={`flex items-center gap-3 px-3 py-2 rounded-lg transition-all ${
                  state.status === "active"
                    ? "bg-[#FEF0EB] shimmer-row"
                    : state.status === "done"
                    ? "opacity-100"
                    : "opacity-40"
                }`}
              >
                {/* Status indicator */}
                <div className="shrink-0 w-5 h-5 flex items-center justify-center">
                  {state.status === "done" ? (
                    <div className="w-4 h-4 rounded-full bg-[#F15A24] flex items-center justify-center">
                      <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                  ) : state.status === "active" ? (
                    <div className="w-3 h-3 rounded-full bg-[#F15A24] animate-pulse" />
                  ) : (
                    <div className="w-3 h-3 rounded-full border border-[#D0C8BE]" />
                  )}
                </div>

                {/* Icon + label */}
                <span className="text-base leading-none">{step.icon}</span>
                <span
                  className={`text-sm flex-1 ${
                    state.status === "done"
                      ? "text-[#1A1A1A] font-medium"
                      : state.status === "active"
                      ? "text-[#1A1A1A] font-semibold"
                      : "text-[#6B6560]"
                  }`}
                >
                  {step.label}
                </span>

                {/* Done message */}
                {state.status === "done" && state.message && (
                  <span className="text-xs text-[#6B6560] truncate max-w-[200px]">
                    {state.message}
                  </span>
                )}
              </div>
            );
          })}
        </div>

        {/* Hook lines feed */}
        {showHooks && (activeKey === "mining" || activeKey === "judge" || isDone) && (
          <div className="mt-4 rounded-lg border border-[#E8E4DC] bg-[#FAFAF7] p-3">
            <p className="text-[10px] uppercase tracking-wider text-[#6B6560] font-medium mb-2">
              Moments discovered
            </p>
            <div className="space-y-1.5">
              {hookDisplayed.map((line, i) => (
                <p key={i} className="text-xs text-[#1A1A1A] leading-snug">
                  &ldquo;{line}&rdquo;
                </p>
              ))}
              {hookCurrent !== null && (
                <p className="text-xs text-[#1A1A1A] leading-snug">
                  &ldquo;{hookCurrent}
                  <span className="inline-block w-0.5 h-3 bg-[#F15A24] align-middle ml-0.5 animate-pulse" />
                  &rdquo;
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Shimmer keyframes */}
      <style>{`
        @keyframes shimmer {
          0%   { background-position: 200% center; }
          100% { background-position: -200% center; }
        }
        .shimmer-row {
          background: linear-gradient(
            90deg,
            #FEF0EB 0%,
            #FDDDD0 50%,
            #FEF0EB 100%
          );
          background-size: 200% auto;
          animation: shimmer 2s linear infinite;
        }
      `}</style>
    </div>
  );
}

function fmtTimer(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}:${String(s).padStart(2, "0")}` : `${s}s`;
}
