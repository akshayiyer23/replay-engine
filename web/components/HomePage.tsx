"use client";

import { useRef, useState, useCallback } from "react";
import type { Webinar, WebinarResults } from "@/lib/api";
import { getResults, fmtDate } from "@/lib/api";
import MomentCard from "./MomentCard";
import HowItWorks from "./HowItWorks";
import Footer from "./Footer";

// ─── Hero ────────────────────────────────────────────────────────────────────

function Hero({ onOpenWebinar }: { onOpenWebinar: (slug: string) => void }) {
  return (
    <section className="pt-24 pb-20 px-6 max-w-6xl mx-auto">
      <div className="max-w-3xl">
        <h1
          className="text-5xl md:text-6xl lg:text-7xl leading-[1.08] tracking-tight text-[#1A1A1A] mb-6"
          style={{ fontFamily: "var(--font-serif)" }}
        >
          Unify&apos;s webinars get under 200 views.
          <br />
          <span className="text-[#F15A24]">They deserve millions.</span>
        </h1>
        <p className="text-lg text-[#6B6560] leading-relaxed mb-8 max-w-2xl">
          An AI agent that finds the moments that should go viral — scored
          against{" "}
          <span className="text-[#1A1A1A] font-medium">
            861 real GTM LinkedIn posts
          </span>
          .
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
    </section>
  );
}

// ─── Stat strip ──────────────────────────────────────────────────────────────

function StatStrip() {
  const stats = [
    { value: "11", label: "webinars" },
    { value: "66", label: "ready-to-post moments" },
    { value: "861", label: "real GTM posts scored against" },
  ];
  return (
    <div className="border-y border-[#E8E4DC] py-5 px-6">
      <div className="max-w-6xl mx-auto flex flex-wrap gap-6 items-center justify-center sm:justify-start">
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
}: {
  w: Webinar;
  selected: boolean;
  onClick: () => void;
}) {
  const thumbUrl = `https://img.youtube.com/vi/${w.youtube_id}/hqdefault.jpg`;
  return (
    <button
      onClick={onClick}
      className={`text-left w-full rounded-xl border overflow-hidden hover:shadow-md hover:-translate-y-0.5 active:translate-y-0 transition-all ${
        selected
          ? "border-[#F15A24] shadow-sm"
          : "border-[#E8E4DC] bg-white"
      }`}
    >
      {/* Thumbnail */}
      <div className="relative w-full" style={{ aspectRatio: "16/9" }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={thumbUrl}
          alt={w.title}
          className="absolute inset-0 w-full h-full object-cover"
        />
        {/* Score badge */}
        <div className="absolute top-2 right-2 w-9 h-9 rounded-full bg-[#F15A24] flex items-center justify-center shadow">
          <span className="text-xs font-semibold text-white">{w.top_score}</span>
        </div>
      </div>

      {/* Meta */}
      <div className="p-4">
        <p className="text-xs text-[#6B6560] mb-1">{fmtDate(w.date)}</p>
        <p className="text-sm font-medium text-[#1A1A1A] leading-snug line-clamp-2 mb-2">
          {w.title}
        </p>
        <div className="flex flex-wrap gap-1">
          {w.speakers.map((s) => (
            <span
              key={s}
              className="text-xs px-2 py-0.5 rounded-full border border-[#E8E4DC] text-[#6B6560]"
            >
              {s}
            </span>
          ))}
        </div>
      </div>
    </button>
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
    <section className="px-6 max-w-6xl mx-auto pb-16">
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
                {results.top_moments?.length} top moments · sorted by viral
                score
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
            const content = results.content?.find((c) => c.id === moment.id);
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

function Library({
  webinars,
  selectedSlug,
  onSelect,
}: {
  webinars: Webinar[];
  selectedSlug: string | null;
  onSelect: (slug: string) => void;
}) {
  return (
    <section className="py-16 px-6 max-w-6xl mx-auto">
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
        {webinars.map((w) => (
          <WebinarCard
            key={w.slug}
            w={w}
            selected={w.slug === selectedSlug}
            onClick={() => onSelect(w.slug)}
          />
        ))}
      </div>
    </section>
  );
}

// ─── HomePage (root client component) ────────────────────────────────────────

export default function HomePage({ webinars }: { webinars: Webinar[] }) {
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [results, setResults] = useState<WebinarResults | null>(null);
  const [loading, setLoading] = useState(false);
  const resultsRef = useRef<HTMLDivElement>(null);

  const openWebinar = useCallback(
    async (slug: string) => {
      if (slug === selectedSlug && results) {
        // already loaded — just scroll
        resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      setSelectedSlug(slug);
      setResults(null);
      setLoading(true);

      // Scroll to results section immediately so the spinner is visible
      setTimeout(
        () =>
          resultsRef.current?.scrollIntoView({
            behavior: "smooth",
            block: "start",
          }),
        50
      );

      const data = await getResults(slug);
      setResults(data);
      setLoading(false);
    },
    [selectedSlug, results]
  );

  const closeResults = useCallback(() => {
    setSelectedSlug(null);
    setResults(null);
    setLoading(false);
  }, []);

  return (
    <main className="flex-1 flex flex-col">
      <Hero onOpenWebinar={openWebinar} />
      <StatStrip />
      <Library
        webinars={webinars}
        selectedSlug={selectedSlug}
        onSelect={openWebinar}
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

      <HowItWorks />
      <Footer />
    </main>
  );
}
