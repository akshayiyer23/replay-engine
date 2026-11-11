"use client";

import { useState } from "react";
import type { Moment, ContentItem, WhyChip } from "@/lib/api";
import { parseWhy, fmtDuration } from "@/lib/api";

const TABS = ["LinkedIn post", "Carousel", "Unify University", "Caption"] as const;
type Tab = (typeof TABS)[number];

const MOMENT_TYPE_LABEL: Record<string, string> = {
  stat: "Stat",
  story: "Story",
  contrarian: "Contrarian",
  framework: "Framework",
  hot_take: "Hot take",
  insight: "Insight",
  "how-to": "How-to",
  prediction: "Prediction",
};

function ScoreBadge({ score }: { score: number }) {
  return (
    <span
      className="inline-flex items-center justify-center w-10 h-10 rounded-full text-sm font-semibold text-white shrink-0"
      style={{ backgroundColor: "#F15A24" }}
      title={`Viral score: ${score}`}
    >
      {score}
    </span>
  );
}

function Chip({
  children,
  orange,
}: {
  children: React.ReactNode;
  orange?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${
        orange
          ? "border-[#F15A24] text-[#F15A24] bg-[#FEF0EB]"
          : "border-[#E8E4DC] text-[#6B6560] bg-white"
      }`}
    >
      {children}
    </span>
  );
}

function WhyChips({
  chips,
  trimmed,
}: {
  chips: WhyChip[];
  trimmed: boolean;
}) {
  if (!chips.length && !trimmed) return null;
  return (
    <div className="mt-3">
      <p className="text-[11px] uppercase tracking-wider text-[#6B6560] mb-1.5 font-medium">
        Why it scored
      </p>
      <div className="flex flex-wrap gap-1.5">
        {chips.map((c, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border border-[#E8E4DC] bg-white text-[#1A1A1A]"
          >
            {c.label}
            {c.mult !== undefined && (
              <span className="text-[#F15A24] font-semibold">
                {c.mult.toFixed(2)}×
              </span>
            )}
          </span>
        ))}
        {trimmed && (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs border border-[#E8E4DC] bg-[#FFFDF6] text-[#6B6560] italic">
            Trimmed to hook
          </span>
        )}
      </div>
    </div>
  );
}

function LinkedInTab({ content }: { content: ContentItem | undefined }) {
  const [copied, setCopied] = useState(false);
  if (!content) return <p className="text-sm text-[#6B6560]">No content generated.</p>;

  const handleCopy = () => {
    navigator.clipboard.writeText(content.linkedin_post).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="relative">
      <pre className="whitespace-pre-wrap text-sm leading-relaxed text-[#1A1A1A] font-sans pr-16">
        {content.linkedin_post}
      </pre>
      <button
        onClick={handleCopy}
        className="absolute top-0 right-0 text-xs px-3 py-1.5 rounded-lg border border-[#E8E4DC] text-[#6B6560] hover:border-[#F15A24] hover:text-[#F15A24] transition-colors"
      >
        {copied ? "Copied!" : "Copy"}
      </button>
    </div>
  );
}

function CarouselTab({ content }: { content: ContentItem | undefined }) {
  if (!content?.carousel?.length)
    return <p className="text-sm text-[#6B6560]">No carousel generated.</p>;
  return (
    <ol className="space-y-2">
      {content.carousel.map((slide, i) => (
        <li key={i} className="flex gap-3 text-sm">
          <span className="shrink-0 w-6 h-6 rounded-full bg-[#F15A24] text-white text-xs flex items-center justify-center font-medium">
            {i + 1}
          </span>
          <span className="text-[#1A1A1A] leading-snug">{slide}</span>
        </li>
      ))}
    </ol>
  );
}

function LessonTab({ content }: { content: ContentItem | undefined }) {
  if (!content?.lesson)
    return <p className="text-sm text-[#6B6560]">No lesson generated.</p>;
  const { lesson } = content;
  return (
    <div className="space-y-4 text-sm">
      <h4 className="font-semibold text-[#1A1A1A]">{lesson.title}</h4>
      {lesson.takeaways?.length > 0 && (
        <ul className="space-y-1.5">
          {lesson.takeaways.map((t, i) => (
            <li key={i} className="flex gap-2 text-[#1A1A1A]">
              <span className="text-[#F15A24] shrink-0">→</span>
              {t}
            </li>
          ))}
        </ul>
      )}
      {lesson.exercise && (
        <div className="rounded-lg border border-[#E8E4DC] p-3 bg-white">
          <p className="text-[11px] uppercase tracking-wider text-[#6B6560] mb-1 font-medium">
            Exercise
          </p>
          <p className="text-[#1A1A1A]">{lesson.exercise}</p>
        </div>
      )}
    </div>
  );
}

function CaptionTab({ content }: { content: ContentItem | undefined }) {
  const [copied, setCopied] = useState(false);
  if (!content?.caption)
    return <p className="text-sm text-[#6B6560]">No caption generated.</p>;
  const handleCopy = () => {
    navigator.clipboard.writeText(content.caption).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <div className="relative">
      <p className="text-sm text-[#1A1A1A] pr-16">{content.caption}</p>
      <button
        onClick={handleCopy}
        className="absolute top-0 right-0 text-xs px-3 py-1.5 rounded-lg border border-[#E8E4DC] text-[#6B6560] hover:border-[#F15A24] hover:text-[#F15A24] transition-colors"
      >
        {copied ? "Copied!" : "Copy"}
      </button>
    </div>
  );
}

function YouTubeEmbed({
  youtubeId,
  startMs,
  endMs,
  thumbUrl,
}: {
  youtubeId: string;
  startMs: number;
  endMs: number;
  thumbUrl: string;
}) {
  const [playing, setPlaying] = useState(false);
  const startSec = Math.floor(startMs / 1000);
  const endSec = Math.floor(endMs / 1000);

  if (playing) {
    return (
      <div className="relative w-full" style={{ aspectRatio: "16/9" }}>
        <iframe
          className="absolute inset-0 w-full h-full rounded-lg"
          src={`https://www.youtube.com/embed/${youtubeId}?start=${startSec}&end=${endSec}&autoplay=1&rel=0&modestbranding=1`}
          allow="autoplay; encrypted-media"
          allowFullScreen
        />
      </div>
    );
  }

  return (
    <div
      className="relative w-full cursor-pointer group rounded-lg overflow-hidden"
      style={{ aspectRatio: "16/9" }}
      onClick={() => setPlaying(true)}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={thumbUrl}
        alt="Webinar thumbnail"
        className="absolute inset-0 w-full h-full object-cover"
      />
      <div className="absolute inset-0 bg-black/20 group-hover:bg-black/30 transition-colors" />
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="w-16 h-16 rounded-full bg-[#F15A24] flex items-center justify-center shadow-lg group-hover:scale-105 transition-transform">
          <svg
            className="w-7 h-7 text-white ml-1"
            fill="currentColor"
            viewBox="0 0 24 24"
          >
            <path d="M8 5v14l11-7z" />
          </svg>
        </div>
      </div>
      <div className="absolute bottom-2 right-2 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded">
        {fmtDuration(endMs - startMs)}
      </div>
    </div>
  );
}

export default function MomentCard({
  moment,
  content,
  youtubeId,
  rank,
}: {
  moment: Moment;
  content: ContentItem | undefined;
  youtubeId: string;
  rank: number;
}) {
  const [activeTab, setActiveTab] = useState<Tab>("LinkedIn post");
  const whyChips = parseWhy(moment.why);
  const thumbUrl = `https://img.youtube.com/vi/${youtubeId}/hqdefault.jpg`;
  const typeLabel =
    MOMENT_TYPE_LABEL[moment.moment_type] ?? moment.moment_type;

  return (
    <article className="border border-[#E8E4DC] rounded-xl bg-white overflow-hidden hover:shadow-md transition-shadow">
      {/* Header */}
      <div className="px-5 pt-5 pb-3 flex items-start gap-3">
        <span className="shrink-0 w-7 h-7 rounded-full border border-[#E8E4DC] text-xs text-[#6B6560] flex items-center justify-center font-medium mt-0.5">
          {rank}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-[13px] text-[#6B6560] leading-snug mb-1">
            {moment.hook_line}
          </p>
          <p className="text-sm font-medium text-[#1A1A1A] leading-snug">
            {moment.angle}
          </p>
        </div>
        <ScoreBadge score={moment.score} />
      </div>

      {/* Body: video left, details right */}
      <div className="px-5 pb-4 grid grid-cols-1 lg:grid-cols-2 gap-5 items-start">
        {/* Video column */}
        <div>
          <YouTubeEmbed
            youtubeId={youtubeId}
            startMs={moment.start_ms}
            endMs={moment.end_ms}
            thumbUrl={thumbUrl}
          />
        </div>

        {/* Meta + tabs column */}
        <div className="flex flex-col gap-3">
          {/* Chips */}
          <div className="flex flex-wrap gap-1.5">
            <Chip orange>{typeLabel}</Chip>
            {moment.post_for && (
              <Chip>Post for: {moment.post_for}</Chip>
            )}
            <Chip>{Math.round(moment.duration_s)}s</Chip>
          </div>

          {/* Why chips */}
          <WhyChips chips={whyChips} trimmed={moment.trimmed} />

          {/* Tabs */}
          <div className="mt-1">
            <div className="flex gap-1 border-b border-[#E8E4DC] mb-3 overflow-x-auto">
              {TABS.map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`shrink-0 px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
                    activeTab === tab
                      ? "border-[#F15A24] text-[#F15A24]"
                      : "border-transparent text-[#6B6560] hover:text-[#1A1A1A]"
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>

            <div className="max-h-64 overflow-y-auto pr-1">
              {activeTab === "LinkedIn post" && (
                <LinkedInTab content={content} />
              )}
              {activeTab === "Carousel" && <CarouselTab content={content} />}
              {activeTab === "Unify University" && (
                <LessonTab content={content} />
              )}
              {activeTab === "Caption" && <CaptionTab content={content} />}
            </div>
          </div>
        </div>
      </div>
    </article>
  );
}
