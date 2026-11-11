"use client";

import { useRef, useState } from "react";
import type { RunInput } from "@/lib/api";

type TabId = "url" | "transcript" | "file";

interface RunPanelProps {
  onRun: (input: RunInput) => void;
}

const TAB_LABELS: Record<TabId, string> = {
  url: "YouTube link",
  transcript: "Paste transcript",
  file: "Upload file",
};

const ETA_ESTIMATES: Record<TabId, string> = {
  url: "Takes ~2–4 min for a 30-min video",
  transcript: "Takes ~90 sec (no download)",
  file: "Takes ~2–3 min for audio files",
};

export default function RunPanel({ onRun }: RunPanelProps) {
  const [tab, setTab] = useState<TabId>("url");
  const [url, setUrl] = useState("");
  const [transcript, setTranscript] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!passcode.trim()) {
      setError("Enter the passcode to run the agent.");
      return;
    }

    let input: RunInput;
    if (tab === "url") {
      if (!url.trim()) { setError("Paste a YouTube URL."); return; }
      input = { type: "url", url: url.trim(), passcode: passcode.trim() };
    } else if (tab === "transcript") {
      if (!transcript.trim()) { setError("Paste the transcript text."); return; }
      if (transcript.trim().length < 500) {
        setError("Transcript too short — needs at least a few minutes of content.");
        return;
      }
      input = { type: "transcript", text: transcript.trim(), passcode: passcode.trim() };
    } else {
      if (!file) { setError("Choose a file first."); return; }
      input = { type: "file", file, passcode: passcode.trim() };
    }

    onRun(input);
  }

  return (
    <div className="rounded-2xl border border-[#E8E4DC] bg-white overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[#E8E4DC] flex items-center justify-between">
        <div>
          <h3
            className="text-lg font-medium text-[#1A1A1A]"
            style={{ fontFamily: "var(--font-serif)" }}
          >
            Run the agent on a new webinar
          </h3>
          <p className="text-xs text-[#6B6560] mt-0.5">
            Requires passcode · {ETA_ESTIMATES[tab]}
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-[#E8E4DC]">
        {(["url", "transcript", "file"] as TabId[]).map((t) => (
          <button
            key={t}
            onClick={() => { setTab(t); setError(null); }}
            className={`flex-1 py-2.5 text-xs font-medium transition-colors ${
              tab === t
                ? "text-[#F15A24] border-b-2 border-[#F15A24]"
                : "text-[#6B6560] hover:text-[#1A1A1A]"
            }`}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="p-6 space-y-4">
        {tab === "url" && (
          <div className="space-y-1.5">
            <input
              type="url"
              placeholder="https://youtube.com/watch?v=..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg border border-[#E8E4DC] text-sm text-[#1A1A1A] placeholder-[#B0A99F] focus:outline-none focus:border-[#F15A24] transition-colors"
            />
            <p className="text-xs text-[#B0A99F]">
              If YouTube blocks it, use the &ldquo;Upload file&rdquo; tab instead.
            </p>
          </div>
        )}

        {tab === "transcript" && (
          <textarea
            placeholder="Paste the raw transcript text here…"
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            rows={5}
            className="w-full px-4 py-2.5 rounded-lg border border-[#E8E4DC] text-sm text-[#1A1A1A] placeholder-[#B0A99F] focus:outline-none focus:border-[#F15A24] transition-colors resize-none"
          />
        )}

        {tab === "file" && (
          <div>
            <input
              ref={fileRef}
              type="file"
              accept="audio/*,video/*,.mp3,.mp4,.m4a,.wav,.webm,.json,.txt"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="hidden"
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="w-full py-6 rounded-lg border-2 border-dashed border-[#E8E4DC] hover:border-[#F15A24] transition-colors text-sm text-[#6B6560] hover:text-[#F15A24]"
            >
              {file ? (
                <span className="font-medium text-[#1A1A1A]">{file.name}</span>
              ) : (
                <>
                  <span className="font-medium">Choose file</span>
                  <span className="text-[#B0A99F]"> · audio, video, or transcript</span>
                </>
              )}
            </button>
          </div>
        )}

        {/* Passcode */}
        <div className="flex gap-3">
          <input
            type="password"
            placeholder="Passcode"
            value={passcode}
            onChange={(e) => setPasscode(e.target.value)}
            className="flex-1 px-4 py-2.5 rounded-lg border border-[#E8E4DC] text-sm text-[#1A1A1A] placeholder-[#B0A99F] focus:outline-none focus:border-[#F15A24] transition-colors"
          />
          <button
            type="submit"
            className="shrink-0 bg-[#F15A24] text-white px-5 py-2.5 rounded-lg text-sm font-medium hover:bg-[#d94e1f] active:scale-[0.98] transition-all"
          >
            Run the agent
          </button>
        </div>

        {/* Error */}
        {error && (
          <p className="text-sm text-red-600 font-medium">{error}</p>
        )}
      </form>
    </div>
  );
}
