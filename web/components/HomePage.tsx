"use client";

import { useCallback, useRef, useState } from "react";
import type {
  AgentEvent,
  Moment,
  ContentItem,
  RunInput,
  Webinar,
  WebinarResults,
} from "@/lib/api";
import { getEvents, getResults, fmtDate } from "@/lib/api";
import MomentCard from "./MomentCard";
import HowItWorks from "./HowItWorks";
import Footer from "./Footer";
import RunPanel from "./RunPanel";
import AgentRunView from "./AgentRunView";

// Shared container — every section uses this so left edges align at all widths
const CONTAINER = "max-w-6xl mx-auto px-6";

// ─── Hero preview card ────────────────────────────────────────────────────────

function HeroPreviewCard({
  moment,
  content,
  youtubeId,
  onClick,
}: {
  moment: Moment;
  content: ContentItem;
  youtubeId: string;
  onClick: () => void;
}) {
  const thumbUrl = `https://img.youtube.com/vi/${youtubeId}/hqdefault.jpg`;
  const firstLine = content.linkedin_post.split("\n")[0];

  return (
    <button
      onClick={onClick}
      className="text-left w-full rounded-xl border border-[#E8E4DC] bg-white overflow-hidden hover:shadow-lg hover:-translate-y-0.5 active:translate-y-0 transition-all group"
    >
      {/* Thumbnail */}
      <div className="relative w-full" style={{ aspectRatio: "16/9" }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={thumbUrl}
          alt="Custom AI Signals"
          className="absolute inset-0 w-full h-full object-cover"
        />
        <div className="absolute inset-0 bg-black/10 group-hover:bg-black/20 transition-colors" />
        {/* Score badge */}
        <div
          className="absolute top-3 right-3 w-10 h-10 rounded-full flex items-center justify-center shadow-md"
          style={{ backgroundColor: "#F15A24" }}
        >
          <span className="text-sm font-semibold text-white">
            {moment.score}
          </span>
        </div>
      </div>

      {/* Meta */}
      <div className="p-4 space-y-2">
        <div className="flex flex-wrap gap-1.5">
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border border-[#F15A24] text-[#F15A24] bg-[#FEF0EB] capitalize">
            {moment.moment_type}
          </span>
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs border border-[#E8E4DC] text-[#6B6560]">
            Post for: {moment.post_for}
          </span>
        </div>
        <p className="text-sm font-medium text-[#1A1A1A] leading-snug">
          {firstLine}
        </p>
        <p className="text-xs text-[#6B6560]">
          {content.linkedin_post.split("\n").slice(1, 3).join(" ").slice(0, 100)}…
        </p>
      </div>
    </button>
  );
}

// ─── Hero ────────────────────────────────────────────────────────────────────

function Hero({
  onOpenWebinar,
  featured,
}: {
  onOpenWebinar: (slug: string) => void;
  featured: WebinarResults | null;
}) {
  const topMoment = featured?.top_moments[0];
  const topContent = topMoment
    ? featured?.content.find((c) => c.id === topMoment.id)
    : undefined;

  return (
    <section className="pt-20 pb-16">
      <div className={`${CONTAINER} grid grid-cols-1 lg:grid-cols-2 gap-12 items-center`}>
        {/* Left: headline + subline + CTA */}
        <div>
          <h1
            className="text-5xl md:text-6xl leading-[1.08] tracking-tight text-[#1A1A1A] mb-5"
            style={{ fontFamily: "var(--font-serif)" }}
          >
            Every Unify webinar has{" "}
            <span className="text-[#F15A24]">6 viral moments</span> hiding in
            it.
          </h1>
          <p className="text-lg text-[#6B6560] leading-relaxed mb-8">
            This agent finds them, scores them against 861 real GTM LinkedIn
            posts, and writes the posts.
          </p>
          <button
            onClick={() => onOpenWebinar("custom-ai-signals")}
            className="inline-flex items-center gap-2 bg-[#F15A24] text-white px-6 py-3.5 rounded-lg text-sm font-medium hover:bg-[#d94e1f] active:scale-[0.98] transition-all"
          >
            See it on The Power of Custom AI Signals
            <svg
              className="w-4 h-4"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M19 9l-7 7-7-7"
              />
            </svg>
          </button>
        </div>

        {/* Right: live preview card */}
        {topMoment && topContent && featured && (
          <div className="w-full max-w-sm mx-auto lg:mx-0 lg:ml-auto">
            <p className="text-xs text-[#6B6560] uppercase tracking-wider mb-2 font-medium">
              Top moment · Custom AI Signals
            </p>
            <HeroPreviewCard
              moment={topMoment}
              content={topContent}
              youtubeId={featured.index.youtube_id}
              onClick={() => onOpenWebinar("custom-ai-signals")}
            />
          </div>
        )}
      </div>
    </section>
  );
}

// ─── Stat strip ──────────────────────────────────────────────────────────────

function StatStrip() {
  const stats = [
    { value: "11", label: "webinars" },
    { value: "66", label: "ready-to-post moments" },
    { value: "861", label: "real GTM posts analyzed" },
  ];
  return (
    <div className="border-y border-[#E8E4DC] py-5">
      <div
        className={`${CONTAINER} flex flex-wrap gap-6 items-center justify-center sm:justify-start`}
      >
        {stats.map((s, i) => (
          <div key={s.label} className="flex items-center gap-2">
            {i > 0 && (
              <span className="text-[#E8E4DC] text-xl font-light hidden sm:inline">
                ·
              </span>
            )}
            <span
              className="text-2xl font-semibold text-[#F15A24]"
              style={{ fontFamily: "var(--font-serif)" }}
            >
              {s.value}
            </span>
            <span className="text-sm text-[#6B6560]">{s.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Webinar card ─────────────────────────────────────────────────────────────

function WebinarCard({
  w,
  selected,
  onClick,
  onReplay,
  pinned,
}: {
  w: Webinar;
  selected: boolean;
  onClick: () => void;
  onReplay: (slug: string) => void;
  pinned?: boolean;
}) {
  const thumbUrl = `https://img.youtube.com/vi/${w.youtube_id}/hqdefault.jpg`;
  return (
    <div
      className={`rounded-xl border overflow-hidden ${
        selected ? "border-[#F15A24] shadow-sm" : "border-[#E8E4DC] bg-white"
      }`}
    >
      {/* Thumbnail (clickable) */}
      <button
        onClick={onClick}
        className="text-left w-full hover:opacity-95 transition-opacity"
      >
        <div className="relative w-full" style={{ aspectRatio: "16/9" }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={thumbUrl}
            alt={w.title}
            className="absolute inset-0 w-full h-full object-cover"
          />
          {/* "Start here" badge */}
          {pinned && (
            <div className="absolute top-2 left-2 bg-[#F15A24] text-white text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide shadow">
              Start here
            </div>
          )}
          {/* Score badge with tooltip */}
          <div className="absolute top-2 right-2 group/score">
            <div className="w-9 h-9 rounded-full bg-[#F15A24] flex items-center justify-center shadow">
              <span className="text-xs font-semibold text-white">
                {w.top_score}
              </span>
            </div>
            <div className="absolute right-0 top-full mt-1 hidden group-hover/score:block z-10">
              <div className="bg-[#1A1A1A] text-white text-[10px] px-2 py-1 rounded whitespace-nowrap">
                Top moment score
              </div>
            </div>
          </div>
        </div>
      </button>

      {/* Meta */}
      <div className="p-4">
        <button onClick={onClick} className="text-left w-full">
          <p className="text-xs text-[#6B6560] mb-1">{fmtDate(w.date)}</p>
          <p className="text-sm font-medium text-[#1A1A1A] leading-snug line-clamp-2 mb-2">
            {w.title}
          </p>
          <div className="flex flex-wrap gap-1 mb-3">
            {w.speakers.map((s) => (
              <span
                key={s}
                className="text-xs px-2 py-0.5 rounded-full border border-[#E8E4DC] text-[#6B6560]"
              >
                {s}
              </span>
            ))}
          </div>
        </button>
        {/* Replay link */}
        <button
          onClick={(e) => { e.stopPropagation(); onReplay(w.slug); }}
          className="text-xs text-[#6B6560] hover:text-[#F15A24] transition-colors flex items-center gap-1"
        >
          <span>▶</span>
          <span>Watch the agent run</span>
        </button>
      </div>
    </div>
  );
}

// ─── Results section ─────────────────────────────────────────────────────────

function ResultsSection({
  results,
  loading,
  onClose,
}: {
  results: WebinarResults | null;
  loading: boolean;
  onClose: () => void;
}) {
  if (!loading && !results) return null;

  return (
    <section className={`${CONTAINER} pb-16`}>
      {/* Header */}
      <div className="flex items-start justify-between gap-4 mb-8">
        <div>
          {results && (
            <>
              <p className="text-xs text-[#6B6560] mb-1">
                {fmtDate(results.index.date)} · {results.index.duration}
              </p>
              <h2
                className="text-2xl md:text-3xl text-[#1A1A1A] leading-tight"
                style={{ fontFamily: "var(--font-serif)" }}
              >
                {results.index.title}
              </h2>
              <p className="text-sm text-[#6B6560] mt-1">
                {results.top_moments?.length} moments · sorted by viral score
              </p>
            </>
          )}
          {loading && (
            <p className="text-sm text-[#6B6560]">Loading results…</p>
          )}
        </div>
        <button
          onClick={onClose}
          className="shrink-0 mt-1 text-xs text-[#6B6560] hover:text-[#F15A24] border border-[#E8E4DC] rounded-lg px-3 py-1.5 transition-colors"
        >
          Close ✕
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-3 py-16 justify-center">
          <div className="w-5 h-5 border-2 border-[#F15A24] border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-[#6B6560]">Fetching results…</span>
        </div>
      )}

      {results && (
        <div className="space-y-6">
          {results.top_moments.map((moment, i) => {
            const content = results.content?.find((c) => c.id === moment.id)!;
            return (
              <MomentCard
                key={moment.id}
                moment={moment}
                content={content}
                youtubeId={results.index.youtube_id}
                rank={i + 1}
              />
            );
          })}
        </div>
      )}
    </section>
  );
}

// ─── Library ─────────────────────────────────────────────────────────────────

const PINNED_SLUG = "custom-ai-signals";

function Library({
  webinars,
  selectedSlug,
  onSelect,
  onReplay,
}: {
  webinars: Webinar[];
  selectedSlug: string | null;
  onSelect: (slug: string) => void;
  onReplay: (slug: string) => void;
}) {
  const pinned = webinars.find((w) => w.slug === PINNED_SLUG);
  const rest = webinars.filter((w) => w.slug !== PINNED_SLUG);
  const ordered = pinned ? [pinned, ...rest] : rest;

  return (
    <section className={`${CONTAINER} py-16`}>
      <h2
        className="text-3xl md:text-4xl mb-2 text-[#1A1A1A]"
        style={{ fontFamily: "var(--font-serif)" }}
      >
        All webinars
      </h2>
      <p className="text-sm text-[#6B6560] mb-8">
        {webinars.length} episodes · click any card to see the moment breakdown
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
        {ordered.map((w) => (
          <WebinarCard
            key={w.slug}
            w={w}
            selected={w.slug === selectedSlug}
            onClick={() => onSelect(w.slug)}
            onReplay={onReplay}
            pinned={w.slug === PINNED_SLUG}
          />
        ))}
      </div>
    </section>
  );
}

// ─── Error messages ───────────────────────────────────────────────────────────

function friendlyError(raw: string): string {
  if (raw === "wrong_passcode") return "Wrong passcode — double-check and try again.";
  if (raw === "rate_limit") return "Daily limit reached (3 runs per day). Come back tomorrow.";
  if (raw === "youtube_server_blocked")
    return "YouTube blocked this download from our server. Use the \"Upload file\" tab or paste the transcript instead.";
  if (raw.toLowerCase().includes("too short") || raw.toLowerCase().includes("minimum"))
    return "Video too short — needs at least 15 minutes of content.";
  if (raw.toLowerCase().includes("download") || raw.toLowerCase().includes("blocked"))
    return "Could not download that video. Try the \"Upload file\" tab instead.";
  return raw;
}

// ─── HomePage (root client component) ────────────────────────────────────────

type AppMode = "idle" | "running" | "replaying";

export default function HomePage({
  webinars,
  featured,
}: {
  webinars: Webinar[];
  featured: WebinarResults | null;
}) {
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [results, setResults] = useState<WebinarResults | null>(null);
  const [loading, setLoading] = useState(false);

  const [appMode, setAppMode] = useState<AppMode>("idle");
  const [runInput, setRunInput] = useState<RunInput | null>(null);
  const [replayEvents, setReplayEvents] = useState<AgentEvent[]>([]);
  const [replaySlug, setReplaySlug] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const resultsRef = useRef<HTMLDivElement>(null);
  const agentRef = useRef<HTMLDivElement>(null);

  // ── Open a webinar's precomputed results ──────────────────────────────────
  const openWebinar = useCallback(
    async (slug: string) => {
      if (slug === selectedSlug && results) {
        resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }

      if (slug === PINNED_SLUG && featured) {
        setSelectedSlug(slug);
        setResults(featured);
        setLoading(false);
        setTimeout(
          () => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
          50
        );
        return;
      }

      setSelectedSlug(slug);
      setResults(null);
      setLoading(true);
      setTimeout(
        () => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
        50
      );

      const data = await getResults(slug);
      setResults(data);
      setLoading(false);
    },
    [selectedSlug, results, featured]
  );

  const closeResults = useCallback(() => {
    setSelectedSlug(null);
    setResults(null);
    setLoading(false);
  }, []);

  // ── Start a live run ──────────────────────────────────────────────────────
  const handleRun = useCallback((input: RunInput) => {
    setRunError(null);
    setRunInput(input);
    setAppMode("running");
    setTimeout(
      () => agentRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      50
    );
  }, []);

  // ── Start a replay ────────────────────────────────────────────────────────
  const handleReplay = useCallback(async (slug: string) => {
    setRunError(null);
    const events = await getEvents(slug);
    if (!events.length) {
      // Fallback: just open the results normally
      openWebinar(slug);
      return;
    }
    setReplayEvents(events);
    setReplaySlug(slug);
    setAppMode("replaying");
    setTimeout(
      () => agentRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      50
    );
  }, [openWebinar]);

  // ── Agent run / replay done ───────────────────────────────────────────────
  const handleAgentDone = useCallback(
    async (agentResults: WebinarResults | null, slug: string | null) => {
      setAppMode("idle");
      setRunInput(null);

      if (agentResults) {
        // Live run returned results inline
        const r = agentResults as unknown as WebinarResults & { slug?: string };
        setResults(agentResults);
        setSelectedSlug(slug ?? r.slug ?? null);
        setLoading(false);
        setTimeout(
          () => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
          100
        );
      } else if (slug) {
        // Replay ended — load the precomputed results
        openWebinar(slug);
      }
    },
    [openWebinar]
  );

  // ── Agent error ───────────────────────────────────────────────────────────
  const handleAgentError = useCallback((msg: string) => {
    setAppMode("idle");
    setRunInput(null);
    setRunError(friendlyError(msg));
    // Scroll back to RunPanel
    setTimeout(
      () => agentRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      50
    );
  }, []);

  const closeAgent = useCallback(() => {
    setAppMode("idle");
    setRunInput(null);
    setReplayEvents([]);
    setReplaySlug(null);
  }, []);

  return (
    <main className="flex-1 flex flex-col">
      <Hero onOpenWebinar={openWebinar} featured={featured} />
      <StatStrip />
      <Library
        webinars={webinars}
        selectedSlug={selectedSlug}
        onSelect={openWebinar}
        onReplay={handleReplay}
      />

      {/* Scroll anchor + results panel */}
      <div ref={resultsRef}>
        {(loading || results) && (
          <div className="border-t border-[#E8E4DC] mt-2 pt-12">
            <ResultsSection
              results={results}
              loading={loading}
              onClose={closeResults}
            />
          </div>
        )}
      </div>

      {/* Agent run panel / replay */}
      <div ref={agentRef} className={`${CONTAINER} py-8`}>
        {appMode === "idle" && (
          <>
            {runError && (
              <div className="mb-4 px-4 py-3 rounded-lg bg-red-50 border border-red-200">
                <p className="text-sm text-red-700 font-medium">{runError}</p>
              </div>
            )}
            <RunPanel onRun={handleRun} />
          </>
        )}

        {(appMode === "running" || appMode === "replaying") && (
          <AgentRunView
            mode={appMode === "running" ? "live" : "replay"}
            input={runInput ?? undefined}
            replayEvents={replayEvents}
            replaySlug={replaySlug ?? undefined}
            onDone={handleAgentDone}
            onError={handleAgentError}
            onClose={closeAgent}
            isReplay={appMode === "replaying"}
          />
        )}
      </div>

      <HowItWorks />
      <Footer />
    </main>
  );
}
