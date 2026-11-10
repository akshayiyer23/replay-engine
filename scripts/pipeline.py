#!/usr/bin/env python3
"""
scripts/pipeline.py — End-to-end webinar → clip candidates pipeline.

Usage:
    python scripts/pipeline.py <youtube-url>
    python scripts/pipeline.py <video-or-audio-file>  (mp4 mov mp3 m4a wav webm)
    python scripts/pipeline.py <transcript.txt>       (plain text, no timestamps)
    python scripts/pipeline.py <sentences.json>
    python scripts/pipeline.py <url> --speakers "A=Alice,B=Bob" --slug my-webinar
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import anthropic
import assemblyai as aai
from dotenv import load_dotenv

# Allow importing build_candidates from the same scripts/ directory
sys.path.insert(0, str(Path(__file__).parent))
from build_candidates import build_candidates

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
SKILLS_DIR = ROOT / "skills" / "unify-moment-miner"
JUDGE_DIR = ROOT / "skills" / "gtm-viral-judge"
CACHE_FILE = DATA_DIR / ".transcript_cache.json"

MODEL = "claude-sonnet-5-5"
CHUNK_MS = 600_000  # 10 minutes
WPM = 150           # words per minute for estimated timestamps (txt input)

AUDIO_VIDEO_EXTS = {".mp4", ".mov", ".mp3", ".m4a", ".wav", ".webm"}

load_dotenv(ROOT / ".env")


# ── helpers ──────────────────────────────────────────────────────────────────

def parse_speakers(s: str) -> dict:
    result = {}
    for part in s.split(","):
        k, _, v = part.partition("=")
        result[k.strip()] = v.strip()
    return result


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")[:60]


def detect_input_type(s: str) -> str:
    """Return 'youtube' | 'file' | 'txt' | 'json'."""
    if s.startswith(("http://", "https://")) or "youtube.com" in s or "youtu.be" in s:
        return "youtube"
    p = Path(s)
    if p.exists():
        if p.suffix.lower() in AUDIO_VIDEO_EXTS:
            return "file"
        if p.suffix.lower() == ".txt":
            return "txt"
        if p.suffix.lower() == ".json":
            return "json"
    return "unknown"


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()[:16]


def parse_txt_transcript(path: Path) -> list[dict]:
    """Split a plain-text transcript into sentences with estimated timestamps."""
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\r\n", "\n", text)
    # Split on sentence-ending punctuation + whitespace, or on paragraph breaks
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    sentences = []
    current_ms = 0
    for raw in parts:
        raw = raw.strip()
        if not raw:
            continue
        word_count = max(1, len(raw.split()))
        duration_ms = int(word_count / WPM * 60 * 1000)
        sentences.append({
            "speaker": "A",
            "start": current_ms,
            "end": current_ms + duration_ms,
            "text": raw,
            "estimated": True,
        })
        current_ms += duration_ms + 500  # 0.5 s pause between sentences
    return sentences


def get_video_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else "unknown"


def get_title_slug(url: str) -> str:
    try:
        result = subprocess.run(
            ["yt-dlp", "--get-title", "--no-playlist", url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return slugify(result.stdout.strip())
    except Exception:
        pass
    return get_video_id(url)


def strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```$", "", text.strip())
    return text.strip()


def _extract_json_array(text: str) -> list | None:
    """Find the outermost [...] block in text using bracket-depth tracking."""
    # Try the whole text first (fastest path)
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("candidates", [])
    except json.JSONDecodeError:
        pass

    # Walk forward to find the first '[', then track depth to find its matching ']'
    start = text.find("[")
    if start == -1:
        return None

    depth, in_str, escape = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ── transcript cache (per-URL) ────────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def migrate_legacy_cache() -> None:
    """Migrate the old single .transcript_id file to the per-URL cache."""
    legacy = DATA_DIR / ".transcript_id"
    if not legacy.exists():
        return
    tid = legacy.read_text().strip()
    cache = load_cache()
    url = "https://www.youtube.com/watch?v=2u4ERdJiEcg"
    if url not in cache:
        cache[url] = {
            "transcript_id": tid,
            "slug": "custom-ai-signals",
            "sentences_file": str(DATA_DIR / "custom-ai-signals-sentences.json"),
        }
        save_cache(cache)


# ── download + transcribe ─────────────────────────────────────────────────────

def download_audio(url: str, out_dir: str) -> str:
    out_template = os.path.join(out_dir, "audio.%(ext)s")
    base_cmd = ["yt-dlp", "-f", "bestaudio", "--no-post-overwrites", "-o", out_template]

    # Up to 2 normal attempts
    for attempt in range(1, 3):
        result = subprocess.run(base_cmd + [url], capture_output=True, text=True)
        if result.returncode == 0:
            break
        if attempt < 2:
            print(f"   ⚠️  Download attempt {attempt} failed, retrying…")
    else:
        # Both normal attempts failed — try once with Chrome cookies
        print("   ⚠️  Standard download blocked, trying with Chrome cookies…")
        result = subprocess.run(
            base_cmd + ["--cookies-from-browser", "chrome", url],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print("YouTube blocked the download — upload the file instead.")
            sys.exit(1)

    matches = list(Path(out_dir).glob("audio.*"))
    if not matches:
        raise FileNotFoundError("yt-dlp produced no audio file")
    return str(matches[0])


def transcribe_file(audio_path: str) -> tuple[list[dict], int, str]:
    aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]
    config = aai.TranscriptionConfig(speaker_labels=True)
    transcript = aai.Transcriber().transcribe(audio_path, config=config)
    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"Transcription failed: {transcript.error}")
    sentences = [
        {"speaker": s.speaker, "start": s.start, "end": s.end, "text": s.text}
        for s in transcript.get_sentences()
    ]
    n_speakers = len({s["speaker"] for s in sentences})
    return sentences, n_speakers, transcript.id


def get_sentences_for_url(url: str) -> tuple[list[dict], str]:
    migrate_legacy_cache()
    cache = load_cache()

    if url in cache:
        entry = cache[url]
        sentences_path = Path(entry["sentences_file"])
        if sentences_path.exists():
            print(f"📋 Reusing cached transcript {entry['transcript_id'][:8]}…")
            return json.loads(sentences_path.read_text()), entry["slug"]

    print("🎧 Downloading audio…")
    slug = get_title_slug(url)

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = download_audio(url, tmp)
        print("✍️  Transcribing…")
        sentences, n_speakers, tid = transcribe_file(audio_path)

    print(f"✍️  Transcribing… found {n_speakers} speakers")

    sentences_path = DATA_DIR / f"{slug}-sentences.json"
    sentences_path.write_text(json.dumps(sentences, indent=2))

    cache[url] = {
        "transcript_id": tid,
        "slug": slug,
        "sentences_file": str(sentences_path),
    }
    save_cache(cache)
    return sentences, slug


def get_sentences_for_file(file_path: Path) -> tuple[list[dict], str]:
    """Transcribe an uploaded audio/video file. Caches by file hash."""
    cache = load_cache()
    key = f"file:{file_hash(file_path)}"

    if key in cache:
        entry = cache[key]
        sp = Path(entry["sentences_file"])
        if sp.exists():
            print(f"📋 Reusing cached transcript for {file_path.name}…")
            return json.loads(sp.read_text()), entry["slug"]

    slug = slugify(file_path.stem)
    print(f"✍️  Transcribing {file_path.name}…")
    sentences, n_speakers, tid = transcribe_file(str(file_path))
    print(f"   → {n_speakers} speakers detected")

    sp = DATA_DIR / f"{slug}-sentences.json"
    sp.write_text(json.dumps(sentences, indent=2))
    cache[key] = {"transcript_id": tid, "slug": slug, "sentences_file": str(sp),
                  "source": str(file_path)}
    save_cache(cache)
    return sentences, slug


def get_sentences_for_txt(txt_path: Path) -> tuple[list[dict], str]:
    """Parse a plain-text transcript, estimate timestamps. Caches by file hash."""
    cache = load_cache()
    key = f"txt:{file_hash(txt_path)}"

    if key in cache:
        entry = cache[key]
        sp = Path(entry["sentences_file"])
        if sp.exists():
            print(f"📋 Reusing cached transcript for {txt_path.name}…")
            return json.loads(sp.read_text()), entry["slug"]

    slug = slugify(txt_path.stem)
    print(f"📝 Parsing plain-text transcript (timestamps estimated at {WPM} wpm)…")
    sentences = parse_txt_transcript(txt_path)
    duration_min = sentences[-1]["end"] / 60_000 if sentences else 0
    print(f"   → {len(sentences)} sentences · {duration_min:.1f} min (estimated)")

    sp = DATA_DIR / f"{slug}-sentences.json"
    sp.write_text(json.dumps(sentences, indent=2))
    cache[key] = {"slug": slug, "sentences_file": str(sp), "source": str(txt_path),
                  "estimated_timestamps": True}
    save_cache(cache)
    return sentences, slug


# ── chunk ─────────────────────────────────────────────────────────────────────

def chunk_sentences(sentences: list[dict], chunk_ms: int) -> list[list[tuple[int, dict]]]:
    """Return chunks; each element is (absolute_index, sentence)."""
    if not sentences:
        return []
    chunks: list[list[tuple[int, dict]]] = []
    current: list[tuple[int, dict]] = []
    chunk_start = sentences[0]["start"]
    for i, s in enumerate(sentences):
        if current and s["start"] - chunk_start >= chunk_ms:
            chunks.append(current)
            current = [(i, s)]
            chunk_start = s["start"]
        else:
            current.append((i, s))
    if current:
        chunks.append(current)
    return chunks


# ── Claude moment mining ──────────────────────────────────────────────────────

def build_skill_prompt() -> str:
    parts = []
    for fname in ("SKILL.md", "moment-types.md", "unify-context.md"):
        text = (SKILLS_DIR / fname).read_text()
        parts.append(f"# {fname}\n\n{text}")
    return "\n\n---\n\n".join(parts)


def mine_chunk(
    chunk: list[tuple[int, dict]],
    speaker_map: dict,
    skill_prompt: str,
) -> list[dict]:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    speaker_map_str = ", ".join(f"{k} = {v}" for k, v in speaker_map.items())
    sentences_payload = json.dumps([{"i": i, **s} for i, s in chunk], indent=2)

    base_instruction = (
        "Find 5–8 strong clip candidates from these sentences only.\n\n"
        "Return a JSON array where each element has EXACTLY these fields — "
        "no transcript, no hook_line, no timestamps:\n"
        "  id, start_i, end_i, moment_type, pillar, connects_to, "
        "persona, post_for, angle, standalone_check, risks\n\n"
        "start_i and end_i must be the 'i' values from the sentences above.\n"
        "Output only the JSON array — no prose, no markdown fences."
    )
    strict_suffix = (
        "\n\nCRITICAL: Your entire response must be valid JSON. "
        "Start with [ and end with ]. No text before or after the array."
    )

    def _call(extra: str = "") -> list | None:
        user_msg = (
            f"Speaker map: {speaker_map_str}\n\n"
            f"Sentences (each has an absolute index 'i' into the full transcript):\n"
            f"{sentences_payload}\n\n"
            f"{base_instruction}{extra}"
        )
        resp = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            extra_body={"output_config": {"effort": "low"}},
            system=skill_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        tb = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
        if tb is None:
            return None
        return _extract_json_array(strip_fences(tb.text))

    result = _call()
    if result is None:
        print("   ⚠️  Parse failed — retrying with stricter prompt…")
        result = _call(strict_suffix)
        if result is None:
            print("   ⚠️  Retry failed — skipping chunk")
            return []

    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("candidates", [])
    return []


# ── viral judge ──────────────────────────────────────────────────────────────

def _rubric_to_table(rubric: dict) -> str:
    rows = ["## Scoring weights (base = 50)\n"]
    for feat in rubric["features"]:
        rows.append(f"### {feat['name']} — {feat['description']}")
        if feat["type"] == "categorical":
            for val, d in feat["values"].items():
                sign = "+" if d["weight"] >= 0 else ""
                rows.append(f"  - `{val}`: {sign}{d['weight']} pts (lift {d['lift']:.2f}×)")
        else:
            w = feat["weight_true"]
            sign = "+" if w >= 0 else ""
            rows.append(f"  - true: {sign}{w} pts (lift {feat['lift']:.2f}×)")
            rows.append("  - false: 0 pts")
        rows.append("")
    each = rubric.get("anti_pattern_penalty_each", rubric.get("anti_pattern_penalty", 8))
    cap = rubric.get("anti_pattern_penalty_max", 15)
    rows.append(f"**Anti-pattern penalty**: −{each} pts each, total cap −{cap} pts.")
    return "\n".join(rows)


def build_judge_prompt() -> str:
    skill_md = (JUDGE_DIR / "SKILL.md").read_text()
    rubric = json.loads((JUDGE_DIR / "rubric.json").read_text())
    anti = (JUDGE_DIR / "anti-patterns.md").read_text()
    hooks = (JUDGE_DIR / "hooks.md").read_text()
    table = _rubric_to_table(rubric)
    prompt = skill_md.replace("{{RUBRIC_TABLE}}", table)
    return f"{prompt}\n\n---\n\n{anti}\n\n---\n\n{hooks}"


def _find_hook_sentence(
    start_hook: str, search_from: int, end_i: int, sentences: list[dict]
) -> int | None:
    """Return the index of the sentence best matching start_hook, or None."""
    if not start_hook or not start_hook.strip():
        return None
    needle = start_hook.lower().strip()
    needle_prefix = needle[:50]
    # Significant words from first 10 tokens (skip very short words)
    needle_words = [w for w in needle.split()[:10] if len(w) > 2]

    best_i, best_overlap = None, 0
    for i in range(search_from, min(end_i + 1, len(sentences))):
        sent = sentences[i]["text"].lower().strip()
        # Exact prefix match (most reliable)
        if sent.startswith(needle_prefix) or needle_prefix in sent:
            return i
        # Word-overlap fallback: need ≥60 % of significant words
        if needle_words:
            sent_words = set(sent.split())
            overlap = sum(1 for w in needle_words if w in sent_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_i = i

    if needle_words and best_overlap >= max(3, len(needle_words) * 0.6):
        return best_i
    return None


def _apply_trims(
    score_items: list[dict], candidates: list[dict], sentences: list[dict]
) -> None:
    """Validate start_hook suggestions and trim candidates in-place."""
    id_to_cand = {c["id"]: c for c in candidates}

    for item in score_items:
        start_hook = item.get("start_hook")
        if not start_hook:
            continue

        c = id_to_cand.get(item["id"])
        if not c:
            continue

        start_i = c.get("start_i", 0)
        end_i = c.get("end_i", len(sentences) - 1)

        # Search from sentence AFTER current start
        trim_i = _find_hook_sentence(start_hook, start_i + 1, end_i, sentences)

        if trim_i is None:
            # Couldn't locate the sentence Claude referenced
            item["score"] = max(0, item["score"] - 8)
            item["why"] += "; trim not found (−8)"
            item["start_hook"] = None
            continue

        new_start_ms = sentences[trim_i]["start"]
        new_duration_s = (sentences[end_i]["end"] - new_start_ms) / 1000

        if new_duration_s > 90:
            item["score"] = max(0, item["score"] - 8)
            item["why"] += f"; trim too long {new_duration_s:.0f}s (−8)"
            item["start_hook"] = None
            continue

        if new_duration_s < 30:
            # Hook was found but clip would be too short to trim.
            # The good-hook sentence exists → don't penalise, just don't physically trim.
            item["why"] += f"; trim valid but clip too short ({new_duration_s:.0f}s) — kept original"
            item["start_hook"] = None
            continue

        # Valid trim — update candidate fields
        c["trimmed"] = True
        c["pre_trim_start_ms"] = c["start_ms"]
        c["start_i"] = trim_i
        c["start_ms"] = new_start_ms
        c["duration_s"] = new_duration_s
        c["duration_flag"] = "OK"
        c["hook_line"] = sentences[trim_i]["text"]
        c["transcript"] = " ".join(s["text"] for s in sentences[trim_i : end_i + 1])


def judge_candidates(
    candidates: list[dict], sentences: list[dict] | None = None
) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system = build_judge_prompt()

    slim = [
        {
            "id": c["id"],
            "moment_type": c["moment_type"],
            "speaker_name": c["speaker_name"],
            "angle": c.get("angle", ""),
            "transcript": c["transcript"][:700],
            "duration_s": round(c["duration_s"]),
        }
        for c in candidates
    ]

    resp = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "low"}},
        system=system,
        messages=[{
            "role": "user",
            "content": f"Score these {len(slim)} candidates:\n{json.dumps(slim, indent=2)}",
        }],
    )

    # sonnet-5-5 may include ThinkingBlock(s) before the TextBlock
    text_block = next(
        (b for b in resp.content if getattr(b, "type", None) == "text"),
        None,
    )
    if text_block is None:
        raise ValueError(
            f"No text block in response "
            f"(blocks: {[getattr(b, 'type', '?') for b in resp.content]})"
        )
    raw = strip_fences(text_block.text)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            result = json.loads(m.group(0))
        else:
            raise

    if sentences is not None:
        _apply_trims(result.get("scores", []), candidates, sentences)

    return result


# ── judge + content (shared by judge-only and full-pipeline paths) ───────────

def _run_judge_and_content(
    candidates: list[dict],
    sentences: list[dict] | None,
    slug: str,
    old_scores: dict,
    args,
) -> None:
    # ── judge ──
    print(f"⚖️  Judging {len(candidates)} candidates…")
    try:
        judgment = judge_candidates(candidates, sentences)
        score_map = {s["id"]: s for s in judgment.get("scores", [])}
        top8_ids = set(judgment.get("top8", []))
        for c in candidates:
            s = score_map.get(c["id"], {})
            c["score"] = s.get("score", 0)
            c["why"] = s.get("why", "")
            c["top8"] = c["id"] in top8_ids
        n_top8 = sum(1 for c in candidates if c.get("top8"))
        n_trimmed = sum(1 for c in candidates if c.get("trimmed"))
        trim_note = f", {n_trimmed} trimmed" if n_trimmed else ""
        print(f"✅ Judged — {n_top8} selected for top 8{trim_note}")
    except Exception as exc:
        print(f"⚠️  Judge failed: {exc} — continuing without scores")
        for c in candidates:
            c["score"] = 0
            c["why"] = ""
            c["top8"] = False

    # Save candidates with scores
    out_path = DATA_DIR / f"{slug}-candidates.json"
    out_path.write_text(json.dumps(candidates, indent=2))
    print(f"💾 Saved → {out_path}")

    _print_top8(candidates, old_scores)

    # ── content ──
    if not args.skip_content:
        from generate_content import generate_for_candidates, print_content_summary
        content_results = generate_for_candidates(candidates, slug)
        print_content_summary(content_results)


# ── pipeline-level cache helpers ─────────────────────────────────────────────

def _derive_cache_key(input_str: str) -> str:
    """Canonical cache key: URL for YouTube, '{type}:{hash16}' for files."""
    itype = detect_input_type(input_str)
    if itype == "youtube":
        return input_str
    p = Path(input_str)
    if p.exists():
        return f"{itype}:{file_hash(p)}"
    return input_str


def _print_top8(candidates: list[dict], old_scores: dict) -> None:
    top8 = sorted(
        [c for c in candidates if c.get("top8")],
        key=lambda c: -c.get("score", 0),
    )
    if not top8:
        top8 = sorted(candidates, key=lambda c: -c.get("score", 0))[:8]
    print()
    for c in top8:
        old = old_scores.get(c["id"])
        score_str = f"{old}→{c['score']}" if old is not None else str(c["score"])
        trimmed = "yes" if c.get("trimmed") else "no"
        print(
            f"{c['id']}  {score_str}  trimmed={trimmed}  "
            f"{c['moment_type']}  {c['angle']}  //  {c.get('why', '')}"
        )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Webinar → clip candidates pipeline")
    parser.add_argument("input", help="YouTube URL, sentences JSON, or (with --judge-only) candidates JSON")
    parser.add_argument(
        "--speakers",
        default="A=Quintin Smith,B=Ethan Dursht",
        metavar="A=Name,B=Name",
        help="Speaker map (default: A=Quintin Smith, B=Ethan Dursht)",
    )
    parser.add_argument("--slug", help="Output slug override")
    parser.add_argument(
        "--judge-only",
        action="store_true",
        help="Skip transcript/mining — load existing candidates JSON and run only the judge step",
    )
    parser.add_argument(
        "--skip-content",
        action="store_true",
        help="Skip the content-generation step (LinkedIn post, carousel, lesson, caption)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore full-pipeline cache and re-run mining/judging/content (transcript still cached)",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    speaker_map = parse_speakers(args.speakers)

    if args.judge_only:
        # ── judge-only mode ──
        input_path = Path(args.input)
        if not input_path.exists():
            sys.exit(f"❌  File not found: {input_path}")
        candidates = json.loads(input_path.read_text())
        slug = args.slug or input_path.stem.removesuffix("-candidates")
        sentences_path = DATA_DIR / f"{slug}-sentences.json"
        sentences = json.loads(sentences_path.read_text()) if sentences_path.exists() else None
        trim_note = f" + {len(sentences)} sentences (trim enabled)" if sentences else " (no sentences file — trim disabled)"
        print(f"📄 Loaded {len(candidates)} candidates{trim_note}")
        old_scores: dict = {c["id"]: c.get("score") for c in candidates}
        _run_judge_and_content(candidates, sentences, slug, old_scores, args)
        return

    # ── non-judge-only path ──

    # Determine cache key; YouTube needs migration first
    input_type = detect_input_type(args.input)
    if input_type == "youtube":
        migrate_legacy_cache()
    cache_key = _derive_cache_key(args.input)
    cache = load_cache()

    # --fresh: wipe pipeline fields (keeps transcript)
    if args.fresh and cache_key in cache:
        for field in ("pipeline_complete", "candidates_file", "content_file"):
            cache[cache_key].pop(field, None)
        save_cache(cache)
        print("🔄 --fresh: pipeline cache cleared (transcript reused)")

    # Full pipeline cache hit?
    entry = cache.get(cache_key, {})
    cf = entry.get("candidates_file", "")
    if entry.get("pipeline_complete") and cf and Path(cf).exists() and not args.fresh:
        import time as _time
        t0 = _time.monotonic()
        print(f"⚡ Pipeline cache hit — loading results instantly")
        candidates = json.loads(Path(cf).read_text())
        slug = entry.get("slug", args.slug or "output")
        _print_top8(candidates, {})
        if not args.skip_content:
            content_cf = entry.get("content_file", "")
            if content_cf and Path(content_cf).exists():
                from generate_content import print_content_summary
                content_results = json.loads(Path(content_cf).read_text())
                print_content_summary(content_results)
            else:
                from generate_content import generate_for_candidates, print_content_summary
                content_results = generate_for_candidates(candidates, slug)
                print_content_summary(content_results)
                cache = load_cache()
                cache.setdefault(cache_key, {})["content_file"] = \
                    str(DATA_DIR / f"{slug}-content.json")
                save_cache(cache)
        elapsed = _time.monotonic() - t0
        print(f"\n⏱  Finished in {elapsed:.1f}s (cached)")
        return

    # ── 1. sentences ──
    input_path = Path(args.input)
    if input_type == "youtube":
        sentences, slug = get_sentences_for_url(args.input)
    elif input_type == "file":
        sentences, slug = get_sentences_for_file(input_path)
    elif input_type == "txt":
        sentences, slug = get_sentences_for_txt(input_path)
    elif input_type == "json":
        print(f"📄 Loading transcript from {input_path}…")
        sentences = json.loads(input_path.read_text())
        slug = args.slug or input_path.stem.removesuffix("-sentences")
    else:
        sys.exit(f"❌  Cannot determine input type for: {args.input!r}\n"
                 "    Accepted: YouTube URL, .mp4/.mov/.mp3/.m4a/.wav/.webm, "
                 ".txt transcript, or -sentences.json")

    if args.slug:
        slug = args.slug

    n_speakers = len({s["speaker"] for s in sentences})
    duration_min = sentences[-1]["end"] / 60_000
    print(f"📊 {len(sentences)} sentences · {n_speakers} speakers · {duration_min:.1f} min")

    # ── 2. chunk ──
    chunks = chunk_sentences(sentences, CHUNK_MS)
    print(f"📦 Split into {len(chunks)} chunk(s) (~10 min each)")

    # ── 3. mine moments ──
    skill_prompt = build_skill_prompt()
    all_labels: list[dict] = []
    for i, chunk in enumerate(chunks):
        chunk_start = chunk[0][1]["start"] / 60_000
        chunk_end = chunk[-1][1]["end"] / 60_000
        print(f"🔍 Scanning chunk {i + 1}/{len(chunks)} ({chunk_start:.0f}–{chunk_end:.0f} min)…")
        labels = mine_chunk(chunk, speaker_map, skill_prompt)
        all_labels.extend(labels)
        print(f"   → {len(labels)} candidates")

    # ── 4. build & sort ──
    print(f"🏗️  Building candidates from {len(all_labels)} labels…")
    candidates = build_candidates(sentences, all_labels, speaker_map=speaker_map)
    candidates.sort(key=lambda c: c["start_ms"])
    for idx, c in enumerate(candidates, 1):
        c["id"] = f"m{idx:02d}"

    n_ok = sum(1 for c in candidates if c["duration_flag"] == "OK")
    n_flagged = len(candidates) - n_ok
    flag_note = f" ({n_flagged} flagged out-of-range)" if n_flagged else ""
    print(f"✅ Found {len(candidates)} moments{flag_note}")

    # ── 5. preliminary save (before judge) ──
    out_path = DATA_DIR / f"{slug}-candidates.json"
    out_path.write_text(json.dumps(candidates, indent=2))

    old_scores = {c["id"]: c.get("score") for c in candidates}
    _run_judge_and_content(candidates, sentences, slug, old_scores, args)

    # ── update pipeline cache with scored candidates ──
    out_path.write_text(json.dumps(candidates, indent=2))
    cache = load_cache()
    cache.setdefault(cache_key, {}).update({
        "pipeline_complete": True,
        "slug": slug,
        "candidates_file": str(out_path),
    })
    # Carry forward content_file if content just ran
    content_path = DATA_DIR / f"{slug}-content.json"
    if content_path.exists():
        cache[cache_key]["content_file"] = str(content_path)
    save_cache(cache)
    print(f"💾 Pipeline cached → re-runs will be instant")


if __name__ == "__main__":
    main()
