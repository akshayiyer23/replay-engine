# Replay Engine

Finds the 6 best LinkedIn moments hidden inside any Unify webinar, scores them against 861 real GTM posts, and writes ready-to-post content — in under 4 minutes.

**Live:** https://replay-engine-eta.vercel.app

---

## What it does

Every Unify webinar has 6 high-signal moments — a stat, a story, a framework, a hot take — buried inside 30 minutes of conversation. Replay Engine finds them, ranks them by predicted virality, and produces four content formats for each one: a LinkedIn post, a carousel outline, a Unify University lesson, and a clip caption.

---

## How it works

**Step 1 — Transcribe.** yt-dlp downloads the audio; AssemblyAI returns a sentence-level JSON transcript with speaker diarization. Every word is timestamped and attributed.

**Step 2 — Find moments.** A Claude agent (the "moment miner") reads the transcript and labels candidates with moment type (story, stat, framework, contrarian, tactical), pillar, persona, and angle. It runs in extended thinking mode so it reasons before it commits.

**Step 3 — Score vs real posts.** A second Claude agent (the "viral judge") scores the top candidates against a rulebook derived from 861 real GTM LinkedIn posts. Each moment gets a numeric score and a written rationale explaining what makes it viral or why it falls short.

**Step 4 — Write content.** A third Claude agent writes a LinkedIn post, carousel outline, Unify University lesson, and clip caption for each of the top 6 moments. An editor gate checks every post against 21 canonical content rules (CONTENT_RULES.md) and rewrites on failure. Posts that still fail are dropped and replaced by the next-best moment.

**Step 5 — Ship.** Results are returned as structured JSON. The frontend shows each clip with its trimmed timestamp, viral score, why-chips, and all four content formats. Posts can be copied in one click.

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, Tailwind CSS 4, TypeScript |
| Backend | FastAPI (Python), SSE streaming, Railway |
| AI | Anthropic Claude (claude-sonnet-4-6), extended thinking, structured JSON output |
| Transcription | AssemblyAI (speaker diarization, sentence-level timestamps) |
| Video | yt-dlp (audio extraction), Supadata (YouTube metadata fallback) |
| Hosting | Vercel (frontend + static bake), Railway (backend API) |
| Data | 861 real GTM LinkedIn posts, 11 locked golden webinar result sets |

---

## Results

| Metric | Value |
|---|---|
| Webinars processed | 11 |
| Ready-to-post moments | 66 |
| Real GTM posts analyzed | 861 |
| Content formats per moment | 4 (LinkedIn post, carousel, lesson, caption) |
| Avg processing time | ~3 min for a 30-min webinar |
| Backtest | Scoring correlates with real post performance on Austin Hughes's posts (ρ ≈ 0.5 on 168 posts) |

All 66 posts pass a 21-rule quality audit: no leaked reasoning, no gendered pronouns, no Unify product pitches, no count-promise mismatches, no unknown person names, no disclaimer sentences, no repetitive openings.

---

## v2 Roadmap

**Weekly learning loop.** After a post is published, pull the actual LinkedIn engagement (likes, comments, reposts). Feed that signal back into the judge's scoring weights. The model gets smarter every week from Unify's own results rather than the static backtest corpus.

**Rendered 9:16 clips.** Auto-generate a vertical clip for each moment: speaker footage, animated captions, Unify brand frame. Ready to post to LinkedIn video and Reels without any editing.

**Auto-run on new webinars.** Watch the Unify YouTube channel. When a new webinar drops, trigger the full pipeline automatically and deliver results to a Slack channel before the team has had their morning coffee.
