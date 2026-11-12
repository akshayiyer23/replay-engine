export type Webinar = {
  slug: string;
  title: string;
  date: string;
  youtube_id: string;
  duration: string;
  speakers: string[];
  top_score: number;
  quality_checked?: boolean;
};

export type WhyChip = { label: string; mult?: number };

export type Moment = {
  id: string;
  score: number;
  _raw_score: number;
  speaker_name: string;
  post_for: string;
  moment_type: string;
  angle: string;
  top8: boolean;
  why: string;
  trimmed: boolean;
  start_ms: number;
  end_ms: number;
  duration_s: number;
  hook_line: string;
};

export type ContentItem = {
  id: string;
  moment_type: string;
  speaker_name: string;
  post_for: string;
  angle: string;
  linkedin_post: string;
  carousel: string[];
  lesson: { title: string; takeaways: string[]; exercise: string };
  caption: string;
};

export type WebinarResults = {
  index: Webinar;
  top_moments: Moment[];
  content: ContentItem[];
};

export type AgentEvent = {
  ts: number;       // seconds since pipeline start
  step: string;
  message: string;
  percent: number;
  slug?: string;
};

export type RunInput =
  | { type: "url"; url: string; passcode: string }
  | { type: "transcript"; text: string; passcode: string }
  | { type: "file"; file: File; passcode: string };

// Backend is only called for live runs (POST /run). All precomputed data
// is served from /data/* (baked into the Vercel build by scripts/bake_static.js).
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function getWebinars(): Promise<Webinar[]> {
  try {
    const res = await fetch("/data/webinars.json", { cache: "no-store" });
    if (res.ok) return res.json();
  } catch {}
  // Fallback: backend (dev mode or first build before bake runs)
  try {
    const res = await fetch(`${API_BASE}/webinars`, { cache: "no-store" });
    if (res.ok) return res.json();
  } catch {}
  return [];
}

export async function getResults(slug: string): Promise<WebinarResults | null> {
  try {
    const res = await fetch(`/data/${slug}/results.json`, { cache: "no-store" });
    if (res.ok) return res.json();
  } catch {}
  // Fallback: backend
  try {
    const res = await fetch(`${API_BASE}/results/${slug}`, { cache: "no-store" });
    if (res.ok) return res.json();
  } catch {}
  return null;
}

export async function getEvents(slug: string): Promise<AgentEvent[]> {
  try {
    const res = await fetch(`/data/${slug}/events.json`, { cache: "no-store" });
    if (res.ok) return res.json();
  } catch {}
  // Fallback: backend
  try {
    const res = await fetch(`${API_BASE}/events/${slug}`, { cache: "no-store" });
    if (res.ok) return res.json();
  } catch {}
  return [];
}

/** Parse "story hook (1.70×) + other topic (1.38×); names Unify" into chips.
 * Allowlist: only chips with a positive multiplier (N.NN×) are shown. */
export function parseWhy(why: string): WhyChip[] {
  if (!why) return [];
  return why
    .split(/[+;]/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((part) => {
      const m = part.match(/^(.+?)\s*\((\d+\.\d+)[×x]\)$/);
      if (m) return { label: m[1].trim(), mult: parseFloat(m[2]) } as WhyChip;
      return null;
    })
    .filter((chip): chip is WhyChip => chip !== null);
}

export function fmtDate(iso: string): string {
  const d = new Date(iso + "T12:00:00Z");
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function fmtDuration(ms: number): string {
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}
