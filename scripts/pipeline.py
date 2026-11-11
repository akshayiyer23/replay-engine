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
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


# ── pipeline errors ───────────────────────────────────────────────────────────

class PipelineError(Exception):
    """Known, user-friendly pipeline error — printed without a stack trace."""


# ── progress events ───────────────────────────────────────────────────────────

_T0: float = 0.0   # set in main() before first emit
_ETA: int = 0      # estimated total seconds, set in main()


def _emit(step: str, message: str, percent: int, **extra) -> None:
    """Emit a structured progress event as a JSON line to stderr."""
    elapsed = time.monotonic() - _T0 if _T0 else 0
    remaining = max(0, int(_ETA - elapsed))
    evt = {"step": step, "message": message, "percent": percent, "eta_seconds": remaining, **extra}
    print(json.dumps(evt), file=sys.stderr, flush=True)


def _fmt_duration(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _estimate_eta(duration_min: float, has_transcript: bool = False) -> int:
    if has_transcript:
        mine_s = max(30, int((duration_min / 10) * 20))
        return mine_s + 20 + 70   # mine + judge + content
    download_s = 30
    transcribe_s = max(60, int(duration_min * 6))
    mine_s = max(30, int((duration_min / 10) * 20))
    return download_s + transcribe_s + mine_s + 20 + 70


def _count_gtm_posts() -> int:
    """Return number of GTM posts used in judge training, or 0 if unavailable."""
    for name in ("posts.json", ".raw_posts_cache.json"):
        p = DATA_DIR / name
        if p.exists():
            try:
                return len(json.loads(p.read_text()))
            except Exception:
                pass
    return 0


# ── helpers ──────────────────────────────────────────────────────────────────

def _normalize_name(detected: str, known_names: list[str]) -> str:
    """Return canonical spelling from known_names if detected name is very close."""
    if not detected:
        return detected
    for n in known_names:
        if n.lower() == detected.lower():
            return n
    matches = difflib.get_close_matches(detected, known_names, n=1, cutoff=0.85)
    return matches[0] if matches else detected


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


def get_video_info(url: str) -> tuple[str, int, str]:
    """Return (slug, duration_seconds, original_title) via a fast yt-dlp metadata call.
    duration_seconds is 0 if unavailable. original_title is "" on failure.
    Raises PipelineError for private/unavailable/blocked URLs.
    """
    try:
        result = subprocess.run(
            ["yt-dlp", "--print", "%(title)s", "--print", "%(duration)s",
             "--no-playlist", url],
            capture_output=True, text=True, timeout=20,
        )
    except FileNotFoundError:
        raise PipelineError("yt-dlp not found — install it with: pip install yt-dlp")
    except subprocess.TimeoutExpired:
        return get_video_id(url), 0, ""

    if result.returncode != 0:
        err = (result.stderr or "").lower()
        if "private" in err or "not available" in err or "unavailable" in err:
            raise PipelineError("Video is private or unavailable — check the URL and permissions.")
        if "sign in" in err or "403" in err:
            raise PipelineError("YouTube requires sign-in for this video — upload the file directly.")
        # Non-fatal: fall back to ID-based slug
        return get_video_id(url), 0, ""

    lines = result.stdout.strip().split("\n")
    original_title = lines[0].strip() if lines else ""
    slug = slugify(original_title) if original_title else get_video_id(url)
    try:
        dur_s = int(lines[1].strip()) if len(lines) > 1 else 0
    except (ValueError, IndexError):
        dur_s = 0
    return slug, dur_s, original_title


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


def _lock_golden(slug: str, cache_key: str) -> None:
    """Copy the current run's output files to golden (stable demo) versions."""
    cache = load_cache()
    for suffix in ("candidates", "content"):
        src = DATA_DIR / f"{slug}-{suffix}.json"
        dst = DATA_DIR / f"{slug}-{suffix}.golden.json"
        if src.exists():
            shutil.copy2(src, dst)
            cache.setdefault(cache_key, {})[f"golden_{suffix}_file"] = str(dst)
            print(f"🔒 Locked → {dst.name}")
    save_cache(cache)


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

_SUPADATA_CACHE_FILE = DATA_DIR / ".supadata_cache.json"


def _supadata_cache_load() -> dict:
    try:
        if _SUPADATA_CACHE_FILE.exists():
            return json.loads(_SUPADATA_CACHE_FILE.read_text())
    except Exception:
        pass
    return {}


def _supadata_cache_save(video_id: str, sentences: list[dict]) -> None:
    try:
        cache = _supadata_cache_load()
        cache[video_id] = sentences
        _SUPADATA_CACHE_FILE.write_text(json.dumps(cache))
    except Exception as exc:
        print(f"[supadata] cache write failed (non-fatal): {exc}")


def _merge_caption_segments(
    raw: list[dict],
    target_words: int = 20,
    max_gap_ms: int = 1500,
) -> list[dict]:
    """Merge short YouTube caption snippets into sentence-length chunks.

    YouTube auto-captions arrive as 2-5 word fragments. Merging within a gap
    threshold and stopping at ~20 words produces sentences the mining model
    can reason about, dramatically increasing moment discovery.
    """
    if not raw:
        return []
    merged: list[dict] = []
    current = dict(raw[0])
    for seg in raw[1:]:
        gap = seg["start"] - current["end"]
        words_so_far = len(current["text"].split())
        if gap <= max_gap_ms and words_so_far < target_words:
            current["text"] = current["text"].rstrip() + " " + seg["text"].lstrip()
            current["end"] = seg["end"]
        else:
            if current["text"].strip():
                merged.append(current)
            current = dict(seg)
    if current["text"].strip():
        merged.append(current)
    return merged


def _fetch_supadata_captions(video_url: str) -> list[dict]:
    """Fetch captions via Supadata API.

    - Checks a disk cache keyed by video ID first (survives --fresh and
      pipeline re-runs; costs zero quota calls).
    - Retries once after 3 s on timeout, 429, or 5xx.
    - Logs the real HTTP status/body so Railway logs show the root cause.
    """
    import urllib.request as _ureq
    import urllib.parse as _uparse
    import urllib.error as _uerr

    video_id = get_video_id(video_url)

    # ── disk cache hit ────────────────────────────────────────────────────────
    cached = _supadata_cache_load()
    if video_id in cached and cached[video_id]:
        raw_cached = cached[video_id]
        print(f"[supadata] cache hit for {video_id} ({len(raw_cached)} segments)")
        # Always re-merge: old cache entries may predate _merge_caption_segments;
        # the function is idempotent so already-merged segments pass through fine.
        return _merge_caption_segments(raw_cached)

    # ── key check ─────────────────────────────────────────────────────────────
    api_key = os.environ.get("SUPADATA_API_KEY", "")
    if not api_key:
        raise PipelineError("SUPADATA_API_KEY not set — cannot fetch captions.")

    # ── API call with one retry on transient failures ─────────────────────────
    encoded = _uparse.urlencode({"url": video_url})
    endpoint = f"https://api.supadata.ai/v1/youtube/transcript?{encoded}"

    data: dict = {}
    for attempt in range(2):
        req = _ureq.Request(endpoint, headers={"x-api-key": api_key})
        try:
            with _ureq.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            break  # success
        except _uerr.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            print(f"[supadata] attempt {attempt + 1} HTTP {exc.code}: {body[:200]}")
            if attempt == 0 and exc.code in (429, 500, 502, 503, 504):
                import time as _time
                print("[supadata] retrying in 3 s…")
                _time.sleep(3)
                continue
            raise PipelineError(f"Supadata API error {exc.code}: {body[:200]}")
        except Exception as exc:
            err_str = str(exc)
            print(f"[supadata] attempt {attempt + 1} exception: {err_str}")
            if attempt == 0 and ("timeout" in err_str.lower() or "timed out" in err_str.lower()):
                import time as _time
                print("[supadata] retrying in 3 s…")
                _time.sleep(3)
                continue
            raise PipelineError(f"Supadata request failed: {exc}")

    # ── parse segments ────────────────────────────────────────────────────────
    segments = data.get("content", [])
    if not segments:
        raise PipelineError("Supadata returned no captions for this video.")

    raw = []
    for seg in segments:
        text = seg.get("text", "").replace("\n", " ").strip()
        if not text or re.match(r"^\[.+\]$", text):
            continue
        offset_ms = int(seg.get("offset", 0))
        duration_ms = int(seg.get("duration", 2000))
        raw.append({
            "speaker": "A",
            "start": offset_ms,
            "end": offset_ms + duration_ms,
            "text": text,
        })

    if not raw:
        raise PipelineError("No usable captions found (Supadata returned empty segments).")

    # Merge short caption snippets into sentence-length chunks so the mining
    # model has enough context. YouTube auto-captions are typically 2-5 words
    # each; merging within a 1.5 s gap and targeting ~20 words per sentence
    # reduces 1500+ fragments to ~200-300 proper sentences.
    sentences = _merge_caption_segments(raw)

    # ── save to disk cache (survives --fresh) ─────────────────────────────────
    _supadata_cache_save(video_id, sentences)
    print(f"[supadata] fetched {len(sentences)} segments, cached as {video_id}")

    return sentences


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
            err = (result.stderr or "").lower()
            if "private" in err or "not available" in err:
                raise PipelineError("Video is private or unavailable — check the URL.")
            raise PipelineError("YouTube blocked the download — upload the .webm or .mp4 directly instead.")

    matches = list(Path(out_dir).glob("audio.*"))
    if not matches:
        raise FileNotFoundError("yt-dlp produced no audio file")
    return str(matches[0])


def transcribe_file(audio_path: str) -> tuple[list[dict], int, str]:
    size_mb = Path(audio_path).stat().st_size / (1024 * 1024)
    if size_mb > 2000:
        raise PipelineError(f"File is {size_mb:.0f} MB — max 2 GB. Compress or trim before uploading.")
    aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]
    config = aai.TranscriptionConfig(speaker_labels=True, language_detection=True)
    transcript = aai.Transcriber().transcribe(audio_path, config=config)
    if transcript.status == aai.TranscriptStatus.error:
        err_msg = (transcript.error or "").lower()
        if "no speech" in err_msg or "no audio" in err_msg:
            raise PipelineError("No speech detected — confirm the file contains a voice track.")
        raise RuntimeError(f"Transcription failed: {transcript.error}")
    sentences = [
        {"speaker": s.speaker, "start": s.start, "end": s.end, "text": s.text}
        for s in transcript.get_sentences()
    ]
    if not sentences:
        raise PipelineError("No speech detected — confirm the file contains a voice track.")
    lang = getattr(transcript, "language_code", None) or ""
    if lang and not lang.startswith("en"):
        raise PipelineError(f"Audio appears to be {lang} — this pipeline is built for English content.")
    n_speakers = len({s["speaker"] for s in sentences})
    return sentences, n_speakers, transcript.id


def get_sentences_for_url(
    url: str, slug: str | None = None
) -> tuple[list[dict], str]:
    migrate_legacy_cache()
    cache = load_cache()

    if url in cache:
        entry = cache[url]
        sentences_path = Path(entry.get("sentences_file", ""))
        if sentences_path.name and sentences_path.exists():
            _emit("transcript_cache", "📋 Reusing cached transcript…", 10)
            print(f"📋 Reusing cached transcript {entry['transcript_id'][:8]}…")
            return json.loads(sentences_path.read_text()), entry["slug"]

    slug = slug or get_video_info(url)[0]  # only the slug is needed here
    _emit("download", "🎧 Downloading audio…", 5)
    print("🎧 Downloading audio…")

    try:
        if os.environ.get("REPLAY_FORCE_CAPTION_FALLBACK") == "1":
            raise PipelineError("forced for testing — skipping yt-dlp")
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = download_audio(url, tmp)
            _emit("transcribe", "✍️  Transcribing…", 15)
            print("✍️  Transcribing…")
            sentences, _, tid = transcribe_file(audio_path)
    except PipelineError as _exc:
        # ANY yt-dlp/transcription failure → try Supadata captions before giving up
        _orig_err = str(_exc)
        print(f"[yt-dlp] download/transcribe failed: {_orig_err}")
        _emit("download", "⚠️  YouTube blocked audio download — using captions instead.", 8)
        print("[supadata] trying caption fallback…")
        try:
            sentences = _fetch_supadata_captions(url)
        except PipelineError as _sup_err:
            print(f"[supadata] failed: {_sup_err}")
            raise PipelineError(
                "YouTube blocked this video on our server. "
                "Upload the file or paste the transcript — takes 10 seconds."
            )
        tid = f"captions:{get_video_id(url)}"
        _emit("transcribe", f"📋 Captions loaded — {len(sentences)} segments", 15)
        print(f"[supadata] loaded {len(sentences)} segments")

    sentences_path = DATA_DIR / f"{slug}-sentences.json"
    sentences_path.write_text(json.dumps(sentences, indent=2))

    cache_entry: dict = {
        "transcript_id": tid,
        "slug": slug,
        "sentences_file": str(sentences_path),
    }
    if tid.startswith("captions:"):
        cache_entry["source_type"] = "captions"
    cache[url] = cache_entry
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


# ── speaker auto-detection ───────────────────────────────────────────────────

def detect_speakers(sentences: list[dict], title: str, is_captions: bool = False) -> list[dict]:
    """Identify speaker labels via Haiku. Returns one detection dict per label."""
    unify_context = (SKILLS_DIR / "unify-context.md").read_text()
    cutoff_ms = 5 * 60 * 1000
    first5 = [s for s in sentences if s.get("start", 0) < cutoff_ms] or sentences[:60]
    labels = sorted({s["speaker"] for s in sentences})
    snippet = "\n".join(f"[{s['speaker']}] {s['text']}" for s in first5[:120])

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system = (
        "You are identifying speakers in a webinar transcript.\n"
        "Use the Unify company context below to match speaker labels to known people.\n\n"
        + unify_context
    )
    user = (
        f"Video title: {title}\n"
        f"Speaker labels present: {', '.join(labels)}\n\n"
        f"First 5 minutes of transcript:\n{snippet}\n\n"
        "For EACH speaker label, return a JSON array with one object per label:\n"
        '{\n'
        '  "label": "A",\n'
        '  "name": "Full Name (or null if unknown)",\n'
        '  "role": "their role/title",\n'
        '  "company": "their company",\n'
        '  "is_current_unify_team": true or false,\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "proof_sentence": "verbatim sentence from the transcript that proves identity"\n'
        '}\n\n'
        "Confidence guide: 0.9+ = name explicitly stated or introduced; "
        "0.7–0.89 = strong context match; below 0.7 = uncertain.\n"
        "IMPORTANT: Use the EXACT name spelling from the company context above, "
        "not the phonetic transcription. Example: use 'Quintin Smith' (from context), "
        "not 'Quinton Smith' (from audio).\n"
        "Return only the JSON array."
    )

    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "low"}},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    tb = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
    if tb is None:
        raise ValueError("No text block from speaker detection")
    result = _extract_json_array(strip_fences(tb.text))
    if result is None:
        raise ValueError("Could not parse speaker detection response")

    # Normalize names to canonical spelling from unify_context
    known_names = re.findall(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', unify_context)
    for d in result:
        if d.get("name"):
            d["name"] = _normalize_name(d["name"], known_names)

    # Apply routing rules
    for d in result:
        name = (d.get("name") or "").strip()
        conf = float(d.get("confidence", 0.0))
        is_current = bool(d.get("is_current_unify_team", False))
        if conf < 0.7 or not name:
            d["display_name"] = "Guest" if is_captions else f"Speaker {d['label']}"
            d["post_for"] = "Unify company page"
        elif is_current:
            d["display_name"] = name
            d["post_for"] = name
        else:
            d["display_name"] = name
            d["post_for"] = "Unify company page"

    return result


def _print_speaker_map(detections: list[dict]) -> None:
    for d in detections:
        conf_pct = int(float(d.get("confidence", 0)) * 100)
        name = d.get("display_name", f"Speaker {d['label']}")
        post = d.get("post_for", "Unify company page")
        proof = (d.get("proof_sentence") or "").strip()
        print(f"   {d['label']} → {name}  (conf={conf_pct}%,  post_for={post!r})")
        if proof:
            print(f"      proof: \"{proof[:110]}\"")


def _build_speaker_maps(detections: list[dict]) -> tuple[dict, dict]:
    """Return (simple_map, enriched_map).
    simple_map  : label → display_name   (for build_candidates)
    enriched_map: label → 'name (routing note)'  (for mine_chunk prompt)
    """
    simple, enriched = {}, {}
    for d in detections:
        label = d["label"]
        name = d["display_name"]
        post = d["post_for"]
        simple[label] = name
        if post == name:
            note = "current Unify team, post for their account"
        elif d.get("confidence", 0) >= 0.7 and d.get("name"):
            note = f"not current Unify team — post for Unify company page, credit {name} by name"
        else:
            note = "unknown — post for Unify company page"
        enriched[label] = f"{name} ({note})"
    return simple, enriched


# ── caption-mode: title-based speaker extraction + per-moment attribution ────

def _speakers_from_title(title: str, unify_context: str, client) -> list[dict]:
    """Extract named participants from video title using Haiku.
    Returns [{name, role: 'guest'|'host', is_current_unify_team}].
    """
    system = (
        "You extract speaker names from podcast/webinar titles.\n"
        "Use the company context below to identify current Unify team members.\n\n"
        + unify_context
    )
    user = (
        f"Video title: {title}\n\n"
        "Extract all named people mentioned. For each return a JSON array:\n"
        '{"name": "Full Name", "role": "guest" or "host", "is_current_unify_team": true/false}\n\n'
        "Rules:\n"
        "- 'guest' = person featured/interviewed (after 'w/', 'with', 'ft.', 'featuring', or after '|')\n"
        "- 'host' = show host (before 'w/' or the show brand's representative)\n"
        "- is_current_unify_team = true only if they appear as current Unify team in the context above\n"
        "- If you cannot find any names, return []\n"
        "Return only the JSON array."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-5-5",
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        tb = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
        if tb is None:
            return []
        result = _extract_json_array(strip_fences(tb.text))
        if not result:
            return []
    except Exception as exc:
        print(f"⚠️  Title speaker extraction failed: {exc}")
        return []

    known_names = re.findall(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', unify_context)
    for d in result:
        if d.get("name"):
            d["name"] = _normalize_name(d["name"], known_names)
    return result


def _attribute_top_moments(
    candidates: list[dict],
    title_speakers: list[dict],
    sentences: list[dict],
) -> None:
    """Binary host/guest classification per top-8 moment.

    Only runs when title has exactly one identified guest.
    guest + conf >= 0.75 + current Unify team → first-person post for that person.
    host, low-confidence, or anything else → Unify company page (safe default).
    Never sets speaker_name to 'Guest' or 'Host' — always uses the actual name.
    """
    if not title_speakers:
        return
    top8 = [c for c in candidates if c.get("top8")]
    if not top8:
        return

    guests = [p for p in title_speakers if p["role"] == "guest"]
    if len(guests) != 1:
        # Ambiguous (0 or 2+ guests) — safe default for all
        for c in top8:
            c["post_for"] = "Unify company page"
        return

    guest = guests[0]
    guest_name = guest["name"]
    is_unify_team = guest.get("is_current_unify_team", False)

    unify_context = (SKILLS_DIR / "unify-context.md").read_text()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    moment_entries = [
        {"id": c["id"], "transcript": c.get("transcript", "")[:500]}
        for c in top8
    ]

    system = (
        f"You classify each podcast moment as spoken by the guest ({guest_name}) or the host.\n\n"
        "Guest patterns: shares personal stories and first-hand experiences, "
        "says 'when I was at [company]', states their own opinions/lessons, "
        "cites their own company's numbers or results, uses 'I', 'we', 'our' naturally.\n"
        "Host patterns: asks questions ('So what do you think...', 'Can you walk us through...'), "
        "sets up topics, re-phrases the guest's point, says 'that's interesting', "
        "transitions between segments.\n\n"
        "Company context (to identify Unify team members):\n" + unify_context
    )
    user = (
        f"Guest: {guest_name}\n\n"
        "For EACH moment, classify as 'guest' or 'host'.\n"
        "Return a JSON array:\n"
        '{"id": "mXXXX", "role": "guest" or "host", '
        '"confidence": 0.0-1.0, "reason": "one sentence"}\n\n'
        f"Moments:\n{json.dumps(moment_entries, indent=2)}\n\n"
        "Return only the JSON array."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-5-5",
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        tb = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
        if tb is None:
            for c in top8:
                c["speaker_name"] = guest_name
                c["post_for"] = "Unify company page"
            return
        attributions = _extract_json_array(strip_fences(tb.text))
        if not attributions:
            for c in top8:
                c["speaker_name"] = guest_name
                c["post_for"] = "Unify company page"
            return
    except Exception as exc:
        print(f"⚠️  Per-moment attribution failed: {exc} — defaulting all to Unify company page")
        for c in top8:
            c["speaker_name"] = guest_name
            c["post_for"] = "Unify company page"
        return

    attr_map = {a["id"]: a for a in attributions}
    for c in top8:
        attr = attr_map.get(c["id"])
        if not attr:
            # No result → safe default, use guest name for crediting
            c["speaker_name"] = guest_name
            c["post_for"] = "Unify company page"
            continue
        role = (attr.get("role") or "").strip().lower()
        conf = float(attr.get("confidence", 0.0))
        reason = attr.get("reason", "")

        if role == "guest" and conf >= 0.75:
            c["speaker_name"] = guest_name
            c["post_for"] = guest_name if is_unify_team else "Unify company page"
            print(f"   {c['id']} → {guest_name} (guest {int(conf * 100)}%): {reason}")
        else:
            # Host or below threshold — credit guest by name, Unify company page
            c["speaker_name"] = guest_name
            c["post_for"] = "Unify company page"
            label = "host" if role == "host" else f"guest {int(conf * 100)}% < 75%"
            print(f"   {c['id']} → {label} → Unify company page: {reason}")


# ── housekeeping detection ───────────────────────────────────────────────────

def mark_housekeeping(sentences: list[dict]) -> set[int]:
    """Call Haiku to identify sentences to skip during mining (intros, polls, logistics)."""
    items = [{"i": i, "text": s["text"][:120]} for i, s in enumerate(sentences)]
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user = (
        "Identify sentences that are pure LOGISTICS — to be excluded from content mining.\n\n"
        "SKIP only these exact categories:\n"
        "  • Opening welcomes and 'thank you for joining' sentences\n"
        "  • Agenda announcements ('today we'll cover...')\n"
        "  • 'Can you hear me', 'is the audio working', tech-check sentences\n"
        "  • Poll questions and their answer options\n"
        "  • Instructions for how to submit Q&A questions ('type in the chat box...')\n"
        "  • Explicit 'book a demo' / 'sign up at...' / 'check out our website' pitches\n"
        "  • Closing sign-offs and goodbyes\n\n"
        "NEVER SKIP sentences that contain:\n"
        "  • Any specific number, stat, or metric (e.g. '50,000 accounts', '4x', '15%')\n"
        "  • A claim, insight, opinion, or lesson learned\n"
        "  • A workflow description or how-something-works explanation\n"
        "  → This includes sentences inside live demos or product walkthroughs.\n"
        "    A sentence in a demo that states a number or makes a claim is CONTENT, not logistics.\n\n"
        "Return a JSON array of integer ranges to EXCLUDE:\n"
        '[{"start_i": 0, "end_i": 5, "reason": "opening welcome"}, ...]\n'
        "Return [] if there is nothing to exclude. Return only the JSON array.\n\n"
        f"Transcript ({len(items)} sentences):\n"
        + json.dumps(items, separators=(",", ":"))
    )
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=4096,
        temperature=1,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "low"}},
        messages=[{"role": "user", "content": user}],
    )
    tb = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
    if tb is None:
        return set()
    result = _extract_json_array(strip_fences(tb.text)) or []
    excluded: set[int] = set()
    for r in result:
        for i in range(int(r["start_i"]), int(r["end_i"]) + 1):
            if 0 <= i < len(sentences):
                excluded.add(i)
    return excluded


# ── label deduplication ───────────────────────────────────────────────────────

def _dedup_labels(labels: list[dict]) -> tuple[list[dict], int]:
    """Remove duplicate labels (overlapping ranges or near-identical angles)."""
    if not labels:
        return labels, 0
    kept: list[dict] = []
    for lab in sorted(labels, key=lambda l: l["start_i"]):
        s, e = lab["start_i"], lab["end_i"]
        angle = (lab.get("angle") or "").lower()[:100]
        is_dup = False
        for kept_lab in kept:
            ks, ke = kept_lab["start_i"], kept_lab["end_i"]
            overlap = max(0, min(e, ke) - max(s, ks) + 1)
            len_a, len_b = e - s + 1, ke - ks + 1
            if overlap / max(1, min(len_a, len_b)) >= 0.5:
                is_dup = True
                if len_a > len_b:
                    kept.remove(kept_lab)
                    kept.append(lab)
                break
            kept_angle = (kept_lab.get("angle") or "").lower()[:100]
            if angle and kept_angle:
                ratio = difflib.SequenceMatcher(None, angle, kept_angle).ratio()
                if ratio >= 0.75:
                    is_dup = True
                    if len_a > len_b:
                        kept.remove(kept_lab)
                        kept.append(lab)
                    break
        if not is_dup:
            kept.append(lab)
    return kept, len(labels) - len(kept)


# ── stat rescue ───────────────────────────────────────────────────────────────

def _rescue_stat_moments(
    sentences: list[dict],
    existing_labels: list[dict],
    hk_indices: set[int],
    min_number: int = 10_000,
) -> list[dict]:
    """Add stat candidates for any uncovered sentence with a scale number ≥ min_number."""
    covered: set[int] = set()
    for lab in existing_labels:
        for i in range(lab["start_i"], lab["end_i"] + 1):
            covered.add(i)

    rescued: list[dict] = []
    for anchor_i, s in enumerate(sentences):
        if anchor_i in hk_indices or anchor_i in covered:
            continue
        nums = _all_nums(s["text"])
        if not any(n >= min_number for n in nums):
            continue

        # Build window: up to 30s before anchor, extend forward to 30-75s
        anchor_start_ms = s["start"]
        start_i = anchor_i
        for j in range(anchor_i - 1, max(0, anchor_i - 15) - 1, -1):
            if j in hk_indices:
                break
            if anchor_start_ms - sentences[j]["start"] > 30_000:
                break
            start_i = j

        end_i = anchor_i
        for j in range(anchor_i + 1, min(len(sentences), anchor_i + 30)):
            if j in hk_indices:
                break
            if sentences[j]["end"] - sentences[start_i]["start"] > 75_000:
                break
            end_i = j

        # Ensure at least 30s
        if sentences[end_i]["end"] - sentences[start_i]["start"] < 30_000:
            for j in range(end_i + 1, min(len(sentences), end_i + 20)):
                if j in hk_indices:
                    break
                end_i = j
                if sentences[end_i]["end"] - sentences[start_i]["start"] >= 30_000:
                    break

        # Build angle from the stat sentence, stripping filler prefix
        text = re.sub(
            r'^(so\s+|and\s+|yeah\s+|well\s+|um\s+|uh\s+|right\s+|like\s+|'
            r'i mean\s+|again\s+|it\'s like\s+|that\s+|)+',
            '', s["text"], flags=re.I,
        ).strip()
        text = text[:1].upper() + text[1:] if text else s["text"]

        for j in range(start_i, end_i + 1):
            covered.add(j)

        rescued.append({
            "id": f"m{sentences[start_i]['start'] // 1000:04d}",
            "start_i": start_i,
            "end_i": end_i,
            "moment_type": "stat",
            "pillar": "results",
            "connects_to": "proof",
            "persona": "AE/SDR",
            "post_for": "",
            "angle": text,
            "standalone_check": "auto-rescued: large-number scale claim",
            "risks": "",
            "rescued": True,
        })

    return rescued


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

    # Build a 0-based relative payload so the model always sees indices 0…N
    # regardless of which chunk of the transcript this is.  After mining we
    # remap start_i / end_i back to absolute sentence indices.
    abs_indices = [abs_i for abs_i, _ in chunk]
    sentences_payload = json.dumps(
        [{"i": rel_i, **s} for rel_i, (_, s) in enumerate(chunk)],
        indent=2,
    )

    base_instruction = (
        "Find 5–8 strong clip candidates from these sentences only.\n\n"
        "Return a JSON array where each element has EXACTLY these fields — "
        "no transcript, no hook_line, no timestamps:\n"
        "  id, start_i, end_i, moment_type, pillar, connects_to, "
        "persona, post_for, angle, standalone_check, risks\n\n"
        "start_i and end_i must be the 'i' values from the sentences above "
        f"(0 to {len(chunk) - 1}).\n"
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
        for _attempt in range(3):
            try:
                resp = client.messages.create(
                    model=MODEL,
                    max_tokens=16000,
                    thinking={"type": "adaptive"},
                    extra_body={"output_config": {"effort": "low"}},
                    extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
                    system=[{"type": "text", "text": skill_prompt,
                             "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user_msg}],
                )
                break
            except anthropic.RateLimitError:
                if _attempt < 2:
                    wait = 15 * (_attempt + 1)
                    print(f"   ⏳ Rate limited — retrying in {wait}s…")
                    time.sleep(wait)
                    continue
                raise
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

    if isinstance(result, dict):
        result = result.get("candidates", [])
    if not isinstance(result, list):
        return []

    # Remap relative indices → absolute indices
    n = len(abs_indices)
    remapped = []
    for lab in result:
        try:
            si = int(lab["start_i"])
            ei = int(lab["end_i"])
        except (KeyError, TypeError, ValueError):
            continue
        # Clamp to valid relative range then convert
        si = max(0, min(si, n - 1))
        ei = max(si, min(ei, n - 1))
        lab["start_i"] = abs_indices[si]
        lab["end_i"] = abs_indices[ei]
        remapped.append(lab)
    return remapped


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

    for _attempt in range(3):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                extra_body={"output_config": {"effort": "low"}},
                extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{
                    "role": "user",
                    "content": f"Score these {len(slim)} candidates:\n{json.dumps(slim, indent=2)}",
                }],
            )
            break
        except anthropic.RateLimitError:
            if _attempt < 2:
                wait = 20 * (_attempt + 1)
                print(f"   ⏳ Judge rate limited — retrying in {wait}s…")
                time.sleep(wait)
                continue
            raise

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
        try:
            # handles "Extra data" — trailing text after valid JSON
            result, _ = json.decoder.JSONDecoder().raw_decode(raw.lstrip())
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
    qa_entries: list | None = None,
    post_judge_hook=None,
) -> None:
    if qa_entries is None:
        qa_entries = []

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
        import traceback
        exc_type = type(exc).__name__
        exc_short = str(exc)[:200]
        print(f"⚠️  Judge failed: {exc} — continuing without scores")
        print(f"[judge-error] {exc_type}: {exc_short}", file=sys.stderr)
        print(f"[judge-traceback] {traceback.format_exc()[:500]}", file=sys.stderr)
        _emit("judge_error", f"Judge error: {exc_type}: {exc_short[:120]}", 66)
        for c in candidates:
            c["score"] = 0
            c["why"] = ""
            c["top8"] = False

    # ── post-judge QA gates ──
    _gate_diversity(candidates, qa_entries)
    _fallback_min3(candidates, qa_entries)

    # ── per-moment speaker attribution (captions path) ──
    if post_judge_hook:
        post_judge_hook(candidates)

    # ── de-duplicate moments that cover the same stat or story ──
    _gate_story_dedup(candidates, qa_entries)

    # Save candidates with scores
    out_path = DATA_DIR / f"{slug}-candidates.json"
    out_path.write_text(json.dumps(candidates, indent=2))
    print(f"💾 Saved → {out_path}")

    _print_top8(candidates, old_scores)

    # Check for the "50,000 accounts" moment across all candidates
    fifty_k = next(
        (c for c in sorted(candidates, key=lambda x: -x.get("score", 0))
         if "50,000" in c.get("transcript", "") or "50000" in c.get("transcript", "")
         or "50,000" in c.get("angle", "")),
        None,
    )
    if fifty_k:
        in_top = "✅ in top set" if fifty_k.get("top8") else "not in top set"
        print(f"\n  50k-accounts moment: {fifty_k['id']}  score={fifty_k.get('score',0)}  {in_top}")
    else:
        print("\n  → '50,000 accounts in under an hour' not found in any candidate")

    # ── content ──
    if not args.skip_content:
        from generate_content import (
            generate_for_candidates, print_content_summary, get_and_clear_qa as _gcq,
        )
        n_top_content = sum(1 for c in candidates if c.get("top8"))
        _emit("content", f"✍️  Writing posts for the top {min(n_top_content, 6)}…", 80)
        content_results = generate_for_candidates(candidates, slug)
        qa_entries.extend(_gcq())
        print_content_summary(content_results)

    # ── save QA log ──
    qa_path = DATA_DIR / f"{slug}-qa.json"
    qa_path.write_text(json.dumps(qa_entries, indent=2))
    _print_qa_summary(qa_entries)
    print(f"💾 QA log → {qa_path}")


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


# ── QA logging ───────────────────────────────────────────────────────────────

def _qa_record(entries: list, gate: str, item_id: str, passed: bool,
               details: str = "", fixed: bool = False) -> None:
    entries.append({
        "gate": gate, "item_id": str(item_id),
        "passed": passed, "details": details, "fixed": fixed,
    })


def _print_qa_summary(qa_entries: list) -> None:
    from collections import defaultdict
    counts: dict = defaultdict(lambda: {"passed": 0, "failed": 0, "fixed": 0})
    for e in qa_entries:
        g = e["gate"]
        if e.get("fixed"):
            counts[g]["fixed"] += 1
        elif e["passed"]:
            counts[g]["passed"] += 1
        else:
            counts[g]["failed"] += 1
    print("\n📋 QA gate summary:")
    for gate, c in sorted(counts.items()):
        p, f, x = c["passed"], c["failed"], c["fixed"]
        icon = "✅" if f == 0 and x == 0 else ("🔧" if x > 0 else "❌")
        parts = [f"pass={p}"]
        if f:
            parts.append(f"fail={f}")
        if x:
            parts.append(f"fixed={x}")
        print(f"   {icon} {gate}: {', '.join(parts)}")


# ── QA gate functions ─────────────────────────────────────────────────────────

REQUIRED_LABEL_FIELDS = {"start_i", "end_i", "moment_type", "post_for", "angle"}


def _gate_label_fields(labels: list[dict], qa: list) -> list[dict]:
    """Drop labels missing required fields; log each."""
    valid = []
    for lab in labels:
        missing = REQUIRED_LABEL_FIELDS - set(lab.keys())
        lid = lab.get("id", f"s{lab.get('start_i', '?')}")
        if missing:
            _qa_record(qa, "label_fields", lid, False, f"missing: {sorted(missing)}")
        else:
            _qa_record(qa, "label_fields", lid, True)
            valid.append(lab)
    return valid


# Number word ↔ digit equivalence (five = 5, thirty = 30, fifty thousand = 50,000)
_WORD_NUM: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000, "million": 1_000_000,
}
_MULT_WORDS = {"hundred", "thousand", "million"}


def _all_nums(text: str) -> set[int]:
    """Return all numbers (digit or word form) as ints ≥ 2. 'percent' treated as '%'."""
    text = re.sub(r'\bpercent\b', '%', text, flags=re.I)
    nums: set[int] = set()
    # Digit forms: 5, 30, 50,000
    for m in re.finditer(r'\b\d[\d,]*\b', text):
        try:
            nums.add(int(m.group().replace(",", "")))
        except ValueError:
            pass
    # Word forms — including two-word compounds like "fifty thousand"
    words = re.findall(r'\b[a-z]+\b', text.lower())
    i = 0
    while i < len(words):
        v = _WORD_NUM.get(words[i])
        if v is not None:
            if i + 1 < len(words) and words[i + 1] in _MULT_WORDS:
                nums.add(v * _WORD_NUM[words[i + 1]])
                i += 2
            else:
                nums.add(v)
                i += 1
        else:
            i += 1
    return {n for n in nums if n >= 2}  # skip 0/1 (often used as concepts, not stats)


def _gate_numbers_in_transcript(candidates: list[dict], qa: list) -> None:
    """Warn if numbers in angle don't appear in clip's transcript (digit = word form)."""
    for c in candidates:
        angle_nums = _all_nums(c.get("angle", ""))
        transcript_nums = _all_nums(c.get("transcript", ""))
        bad = sorted(angle_nums - transcript_nums)
        if bad:
            c.setdefault("qa_warnings", []).append(f"numbers not in transcript: {bad}")
            _qa_record(qa, "numbers_in_transcript", c["id"], False, f"not found: {bad}")
        else:
            _qa_record(qa, "numbers_in_transcript", c["id"], True)


def _gate_duration(candidates: list[dict], qa: list) -> None:
    """Log duration-range check for every candidate."""
    for c in candidates:
        passed = c.get("duration_flag") == "OK"
        _qa_record(qa, "duration", c["id"], passed,
                   f"{c.get('duration_s', 0):.0f}s [{c.get('duration_flag', '?')}]")


def _gate_story_dedup(candidates: list[dict], qa: list) -> None:
    """Remove top-8 moments that cover the same stat or story as a higher-scoring one.
    Uses significant numbers (>= 100, non-year) in hook_line + why + angle as the
    duplicate signal. Keeps the higher-scoring moment; promotes the next best instead.
    Applies to every video (not just captions).
    """
    top8 = sorted([c for c in candidates if c.get("top8")], key=lambda c: -c.get("score", 0))
    if len(top8) <= 1:
        return

    def _sig_numbers(c: dict) -> set[str]:
        text = " ".join([c.get("hook_line") or "", c.get("why") or "", c.get("angle") or ""])
        return {n for n in re.findall(r'\b(\d+)\b', text)
                if int(n) >= 100 and not 1900 <= int(n) <= 2099}

    demoted: set[str] = set()
    for i, ci in enumerate(top8):
        if ci["id"] in demoted:
            continue
        ni = _sig_numbers(ci)
        if not ni:
            continue
        for cj in top8[i + 1:]:
            if cj["id"] in demoted:
                continue
            shared = ni & _sig_numbers(cj)
            if shared:
                demoted.add(cj["id"])
                qa.append({
                    "gate": "story_dedup", "action": "demote",
                    "kept": ci["id"], "demoted": cj["id"], "shared_stat": sorted(shared),
                    "reason": f"same stat {sorted(shared)}: '{ci['hook_line'][:60]}' vs '{cj['hook_line'][:60]}'",
                })
                print(f"   story_dedup: {cj['id']} (score={cj.get('score', 0)}) duplicates "
                      f"{ci['id']} on stat {sorted(shared)} — demoted")

    if not demoted:
        return

    for c in candidates:
        if c["id"] in demoted:
            c["top8"] = False

    n_to_fill = len(demoted)
    filled = 0
    for c in sorted(candidates, key=lambda x: -x.get("score", 0)):
        if filled >= n_to_fill:
            break
        if c.get("top8") or c["id"] in demoted:
            continue
        c["top8"] = True
        filled += 1
        qa.append({"gate": "story_dedup", "action": "promote", "id": c["id"],
                   "score": c.get("score", 0)})
        print(f"   story_dedup: promoted {c['id']} (score={c.get('score', 0)}) to fill gap")


def _gate_diversity(candidates: list[dict], qa: list) -> None:
    """Ensure top set has ≥3 moment types and ≤2 of any one type. Fix by swapping."""
    top = sorted([c for c in candidates if c.get("top8")],
                 key=lambda c: -c.get("score", 0))
    if not top:
        return

    type_counts: dict[str, int] = {}
    for c in top:
        type_counts[c["moment_type"]] = type_counts.get(c["moment_type"], 0) + 1

    n_types = len(type_counts)
    max_cnt = max(type_counts.values())
    details = f"types={n_types}, max_per_type={max_cnt}"

    if n_types >= 3 and max_cnt <= 2:
        _qa_record(qa, "diversity", "top_set", True, details)
        return

    # Fix: demote excess, promote replacements
    bench = sorted([c for c in candidates if not c.get("top8")],
                   key=lambda c: -c.get("score", 0))
    removed_ids, added_ids = [], []

    for mtype, cnt in list(type_counts.items()):
        if cnt > 2:
            excess = sorted(
                [c for c in top if c["moment_type"] == mtype],
                key=lambda c: c.get("score", 0),
            )[:cnt - 2]
            for c in excess:
                c["top8"] = False
                removed_ids.append(c["id"])

    current_types = {c["moment_type"] for c in candidates if c.get("top8")}
    for c in bench:
        if not removed_ids:
            break
        if len(current_types) < 3 or c["moment_type"] not in current_types:
            c["top8"] = True
            current_types.add(c["moment_type"])
            added_ids.append(c["id"])
            removed_ids.pop(0)

    new_top = [c for c in candidates if c.get("top8")]
    nc: dict[str, int] = {}
    for c in new_top:
        nc[c["moment_type"]] = nc.get(c["moment_type"], 0) + 1
    fixed = len(nc) >= 3 and max(nc.values(), default=0) <= 2
    fix_note = f"removed {removed_ids}, added {added_ids}"
    _qa_record(qa, "diversity", "top_set", fixed,
               f"{details} → {fix_note}", fixed=fixed)


def _fallback_min3(candidates: list[dict], qa: list) -> None:
    """Ensure at least 3 candidates make it to content generation."""
    top = [c for c in candidates if c.get("top8")]
    if len(top) >= 3:
        _qa_record(qa, "min3", "top_set", True, f"{len(top)} top8 candidates")
        return
    best3 = sorted(candidates, key=lambda c: -c.get("score", 0))[:3]
    promoted = []
    for c in best3:
        if not c.get("top8"):
            c["top8"] = True
            promoted.append(c["id"])
        if c.get("score", 0) < 45:
            c["lower_confidence"] = True
            c["lower_confidence_reason"] = (
                f"best available (score={c['score']}, threshold=45)"
            )
    _qa_record(qa, "min3", "top_set", False,
               f"only {len(top)} top8 — promoted {promoted}", fixed=True)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    global _T0, _ETA

    parser = argparse.ArgumentParser(description="Webinar → clip candidates pipeline")
    parser.add_argument("input", help="YouTube URL, sentences JSON, or (with --judge-only) candidates JSON")
    parser.add_argument(
        "--speakers",
        default="",
        metavar="A=Name,B=Name",
        help="Override speaker map (auto-detected by default)",
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
    parser.add_argument(
        "--lock",
        action="store_true",
        help="Save the current results as the golden demo version for this video. "
             "The landing page always loads the golden version. "
             "Only --lock overwrites it; --fresh alone never does.",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _T0 = time.monotonic()

    try:
        _run_main(args)
    except PipelineError as exc:
        _emit("error", f"❌  {exc}", 0)
        sys.exit(f"❌  {exc}")
    except anthropic.AuthenticationError:
        _emit("error", "❌  Anthropic API key is invalid — check ANTHROPIC_API_KEY in .env.", 0)
        sys.exit("❌  Anthropic API key is invalid — check ANTHROPIC_API_KEY in .env.")
    except anthropic.BadRequestError as exc:
        msg = str(exc).lower()
        if "credit" in msg or "billing" in msg or "balance" in msg or "quota" in msg:
            print(f"[CREDITS_EXHAUSTED] {exc}", file=sys.stderr)
            _emit("error", "credits_exhausted", 0)
            sys.exit("❌  Anthropic API credits exhausted.")
        _emit("error", f"❌  Anthropic API bad request: {exc}", 0)
        sys.exit(f"❌  Anthropic API bad request: {exc}")
    except anthropic.PermissionDeniedError as exc:
        msg = str(exc).lower()
        if "credit" in msg or "billing" in msg or "quota" in msg:
            print(f"[CREDITS_EXHAUSTED] {exc}", file=sys.stderr)
            _emit("error", "credits_exhausted", 0)
            sys.exit("❌  Anthropic API credits exhausted.")
        _emit("error", f"❌  Anthropic API permission denied: {exc}", 0)
        sys.exit(f"❌  Anthropic API permission denied: {exc}")
    except anthropic.RateLimitError:
        _emit("error", "❌  Anthropic rate limit hit — wait a moment and retry.", 0)
        sys.exit("❌  Anthropic rate limit hit — wait a moment and retry.")


def _run_main(args) -> None:
    global _T0, _ETA

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

    input_type = detect_input_type(args.input)
    if input_type == "youtube":
        migrate_legacy_cache()
    cache_key = _derive_cache_key(args.input)
    cache = load_cache()

    # For YouTube: get title + duration quickly for ETA, before any download
    video_slug_hint: str | None = None
    _video_title: str = ""
    if input_type == "youtube":
        try:
            video_slug_hint, dur_s, _video_title = get_video_info(args.input)
        except PipelineError as _vinfo_err:
            # yt-dlp metadata blocked on this server (sign-in, bot check, 403, etc.)
            # Fall back gracefully — get_sentences_for_url will try Supadata captions
            print(f"[yt-dlp] metadata failed: {_vinfo_err} — will attempt Supadata fallback")
            video_slug_hint = get_video_id(args.input)
            dur_s = 0
            _video_title = ""
        dur_min = dur_s / 60
        # Check if transcript is already cached (no download needed)
        transcript_cached = (
            cache_key in cache and
            Path(cache.get(cache_key, {}).get("sentences_file", "")).exists()
        )
        _ETA = _estimate_eta(dur_min, has_transcript=transcript_cached)
        dur_str = _fmt_duration(dur_s) if dur_s else "unknown length"
        dur_min_note = f" (~{int(dur_min)} min)" if dur_min >= 5 else ""
        if transcript_cached:
            _emit("start", f"📋 Transcript cached · re-mining ({dur_str}{dur_min_note})… ~{_ETA//60} min", 2)
        else:
            _emit("start", f"🎧 Downloading audio ({dur_str}{dur_min_note})… ~{_ETA//60} min", 2)
        print(f"\n⏱  ETA: ~{_ETA//60} min  ({dur_str})")
        if dur_s and dur_s < 300:
            raise PipelineError(f"Video is {dur_min:.1f} min — too short to find multiple clip moments (min 5 min).")

    # --fresh: wipe pipeline fields but never the golden version.
    # For caption-based transcripts, also drop the sentences_file so the
    # merge parameters are always re-applied (Supadata disk cache keeps this
    # free — no API call is made).
    if args.fresh and cache_key in cache:
        entry_now = cache[cache_key]
        for field in ("pipeline_complete", "candidates_file", "content_file"):
            entry_now.pop(field, None)
        if entry_now.get("source_type") == "captions" or \
                entry_now.get("transcript_id", "").startswith("captions:"):
            sf = entry_now.pop("sentences_file", None)
            if sf:
                try:
                    Path(sf).unlink(missing_ok=True)
                except OSError:
                    pass
            print("🔄 --fresh: caption transcript cleared (will re-merge from cache)")
        save_cache(cache)
        print("🔄 --fresh: pipeline cache cleared (transcript reused, golden preserved)")

    # Full pipeline cache hit? Prefer golden version for the landing page.
    entry = cache.get(cache_key, {})
    golden_cf = entry.get("golden_candidates_file", "")
    regular_cf = entry.get("candidates_file", "")
    cf = golden_cf if (golden_cf and Path(golden_cf).exists()) else regular_cf

    if entry.get("pipeline_complete") and cf and Path(cf).exists() and not args.fresh:
        t0 = time.monotonic()
        is_golden = bool(golden_cf and Path(golden_cf).exists())
        label = "🔒 golden" if is_golden else "⚡ cached"
        _emit("cache", f"{label} — loading results instantly", 95)
        print(f"{label} — loading results instantly")
        candidates = json.loads(Path(cf).read_text())
        slug = entry.get("slug", args.slug or "output")
        _print_top8(candidates, {})
        if not args.skip_content:
            golden_content = entry.get("golden_content_file", "")
            content_cf = golden_content if (golden_content and Path(golden_content).exists()) \
                else entry.get("content_file", "")
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
        elapsed = time.monotonic() - t0
        _emit("done", f"✅ Loaded {sum(1 for c in candidates if c.get('top8')) } viral-ready moments", 100, slug=slug)
        print(f"\n⏱  Finished in {elapsed:.1f}s (cached)")
        if args.lock:
            _lock_golden(slug, cache_key)
        return

    # ── 1. sentences ──
    input_path = Path(args.input)
    if input_type == "youtube":
        sentences, slug = get_sentences_for_url(args.input, slug=video_slug_hint)
    elif input_type == "file":
        sentences, slug = get_sentences_for_file(input_path)
    elif input_type == "txt":
        sentences, slug = get_sentences_for_txt(input_path)
    elif input_type == "json":
        print(f"📄 Loading transcript from {input_path}…")
        sentences = json.loads(input_path.read_text())
        slug = args.slug or input_path.stem.removesuffix("-sentences")
    else:
        raise PipelineError(
            f"Cannot determine input type for: {args.input!r}\n"
            "    Accepted: YouTube URL, .mp4/.mov/.mp3/.m4a/.wav/.webm, "
            ".txt transcript, or -sentences.json"
        )

    if args.slug:
        slug = args.slug

    if len(sentences) == 0:
        raise PipelineError("No speech detected — confirm the file contains a voice track.")

    n_speakers = len({s["speaker"] for s in sentences})
    duration_min = sentences[-1]["end"] / 60_000

    if duration_min < 5 and input_type != "json":
        raise PipelineError(f"Video is {duration_min:.1f} min — too short to find multiple clip moments (min 5 min).")

    # Recalibrate ETA now that we have the real duration
    if not _ETA:
        _ETA = _estimate_eta(duration_min)

    print(f"📊 {len(sentences)} sentences · {n_speakers} speakers · {duration_min:.1f} min")

    # ── 1b. speaker detection ──
    cache = load_cache()
    entry = cache.get(cache_key, {})
    is_captions = (
        entry.get("source_type") == "captions"
        or entry.get("transcript_id", "").startswith("captions:")
    )
    if args.speakers:
        raw_map = parse_speakers(args.speakers)
        speaker_detections = [
            {
                "label": k, "name": v, "display_name": v,
                "post_for": "Unify company page",
                "confidence": 1.0, "is_current_unify_team": False,
                "role": "", "company": "", "proof_sentence": "(manual override)",
            }
            for k, v in raw_map.items()
        ]
        print("🎙️  Speaker map (manual override):")
        _print_speaker_map(speaker_detections)
    elif "speaker_detections" in entry:
        speaker_detections = entry["speaker_detections"]
        print("🎙️  Speaker map (cached):")
        _print_speaker_map(speaker_detections)
    else:
        print("🎙️  Detecting speakers…")
        title = _video_title if _video_title else slug.replace("-", " ")
        speaker_detections = detect_speakers(sentences, title, is_captions=is_captions)
        cache.setdefault(cache_key, {})["speaker_detections"] = speaker_detections
        save_cache(cache)
        _print_speaker_map(speaker_detections)

    # Emit speaker event with real names
    speaker_names = ", ".join(
        d["display_name"] for d in speaker_detections
        if d.get("confidence", 0) >= 0.7
    ) or f"{n_speakers} speakers"
    _emit("speakers", f"✍️  Transcribing… found {n_speakers} speakers: {speaker_names}", 30)

    speaker_map_simple, speaker_map_enriched = _build_speaker_maps(speaker_detections)
    post_for_map = {d["label"]: d["post_for"] for d in speaker_detections}

    # For captions: safe default — per-moment attribution will upgrade to first-person where
    # confident; if attribution fails or skips, every moment stays on the Unify company page
    if is_captions:
        post_for_map = {k: "Unify company page" for k in post_for_map}

    # For captions: extract named speakers from video title for per-moment attribution
    _title_speakers: list[dict] = []
    if is_captions and _video_title:
        _unify_ctx = (SKILLS_DIR / "unify-context.md").read_text()
        _haiku = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        _title_speakers = _speakers_from_title(_video_title, _unify_ctx, _haiku)
        if _title_speakers:
            print(f"   Names from title: "
                  f"{', '.join(p['name'] + ' (' + p['role'] + ')' for p in _title_speakers)}")

    qa_entries: list[dict] = []
    t_pipeline_start = time.monotonic()

    # ── 2. housekeeping detection ──
    print("🧹 Detecting housekeeping sentences to skip…")
    try:
        hk_indices = mark_housekeeping(sentences)
    except Exception as _hk_err:
        print(f"⚠️  housekeeping detection failed ({_hk_err}) — skipping, continuing with all sentences")
        hk_indices = set()
    n_hk = len(hk_indices)
    print(f"   → {n_hk} sentence(s) marked as housekeeping and excluded from mining")
    _emit("housekeeping", f"🧹 Skipped {n_hk} housekeeping sentences", 37)

    # ── 3. chunk ──
    chunks = chunk_sentences(sentences, CHUNK_MS)
    n_chunks = len(chunks)
    workers = 1
    print(f"📦 {n_chunks} chunk(s), mining sequentially (workers={workers})…")
    _emit("mining", f"🔍 Scanning {len(sentences)} sentences across {n_chunks} chunks…", 40)

    # ── 4. mine moments (parallel) ──
    skill_prompt = build_skill_prompt()
    all_labels_by_chunk: dict[int, list[dict]] = {}

    def _mine(i: int) -> tuple[int, list[dict]]:
        chunk = chunks[i]
        filtered = [(idx, s) for idx, s in chunk if idx not in hk_indices]
        labels = mine_chunk(filtered, speaker_map_enriched, skill_prompt) if filtered else []
        return i, labels

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_mine, i): i for i in range(n_chunks)}
        for future in as_completed(futures):
            try:
                i, labels = future.result()
            except Exception as exc:
                i = futures[future]
                exc_type = type(exc).__name__
                exc_short = str(exc)[:200]
                print(f"⚠️  Chunk {i + 1} failed: {exc} — skipping")
                print(f"[mining-error] chunk={i + 1} error={exc!r}", file=sys.stderr)
                _emit("mining_error", f"Chunk {i + 1} error: {exc_type}: {exc_short[:120]}", 42)
                labels = []
            all_labels_by_chunk[i] = labels
            chunk = chunks[i]
            cs = chunk[0][1]["start"] / 60_000
            ce = chunk[-1][1]["end"] / 60_000
            n_mined = sum(1 for idx, _ in chunk if idx not in hk_indices)
            print(f"🔍 Chunk {i + 1}/{n_chunks} ({cs:.0f}–{ce:.0f} min, {n_mined} sentences) → {len(labels)} candidates")

    all_labels_raw: list[dict] = []
    for i in range(n_chunks):
        all_labels_raw.extend(all_labels_by_chunk.get(i, []))

    # ── 4b. stat rescue: catch any large-number scale claims the miner missed ──
    rescued = _rescue_stat_moments(sentences, all_labels_raw, hk_indices)
    if rescued:
        print(f"🔢 Rescued {len(rescued)} stat moment(s) with large-number scale claims")
        all_labels_raw.extend(rescued)

    # ── 5. dedup + label field gate ──
    all_labels, n_dupes = _dedup_labels(all_labels_raw)
    if n_dupes:
        print(f"🔁 Removed {n_dupes} duplicate label(s) ({len(all_labels_raw)} → {len(all_labels)})")
    all_labels = _gate_label_fields(all_labels, qa_entries)
    n_dropped = len(all_labels_raw) - n_dupes - len(all_labels)
    if n_dropped:
        print(f"⚠️  Dropped {n_dropped} label(s) with missing required fields")

    # ── 6. build & sort ──
    # Ensure every label has an id (defensive — miner should always set one)
    for lab in all_labels:
        if not lab.get("id"):
            start_ms = sentences[lab["start_i"]]["start"] if lab.get("start_i", 0) < len(sentences) else 0
            lab["id"] = f"m{start_ms // 1000:04d}"
    print(f"🏗️  Building candidates from {len(all_labels)} labels…")
    candidates = build_candidates(sentences, all_labels, speaker_map=speaker_map_simple)
    candidates.sort(key=lambda c: c["start_ms"])
    for c in candidates:
        c["id"] = f"m{c['start_ms'] // 1000:04d}"

    for c in candidates:
        c["post_for"] = post_for_map.get(c["speaker"], "Unify company page")

    n_ok = sum(1 for c in candidates if c["duration_flag"] == "OK")
    n_flagged = len(candidates) - n_ok
    flag_note = f" ({n_flagged} flagged out-of-range)" if n_flagged else ""
    print(f"✅ Found {len(candidates)} moments{flag_note}")

    # Emit a sample of hook lines so the UI can show a live "reading" feed
    sample_hooks = [c["hook_line"][:120] for c in candidates[:6] if c.get("hook_line")]
    for i, hook in enumerate(sample_hooks):
        pct = 42 + i * 3
        _emit("mining_hook", hook, min(pct, 60))

    # ── 7. pre-judge QA gates ──
    _gate_numbers_in_transcript(candidates, qa_entries)
    _gate_duration(candidates, qa_entries)

    # ── 8. preliminary save (before judge) ──
    out_path = DATA_DIR / f"{slug}-candidates.json"
    out_path.write_text(json.dumps(candidates, indent=2))

    # Judge emit with real post count
    n_posts = _count_gtm_posts()
    post_ref = f"{n_posts} real GTM posts" if n_posts else "GTM post benchmarks"
    print(f"[mining-summary] {len(candidates)} candidates before judge", file=sys.stderr)
    _emit("judge", f"⚖️  Scoring {len(candidates)} moments against {post_ref}…", 65)

    old_scores = {c["id"]: c.get("score") for c in candidates}

    def _captions_attribution_hook(cands: list[dict]) -> None:
        if _title_speakers:
            print(f"🎤 Attributing top moments by content…")
            _attribute_top_moments(cands, _title_speakers, sentences)

    _run_judge_and_content(
        candidates, sentences, slug, old_scores, args, qa_entries,
        post_judge_hook=_captions_attribution_hook if is_captions else None,
    )

    # ── update pipeline cache with scored candidates ──
    out_path.write_text(json.dumps(candidates, indent=2))
    cache = load_cache()
    cache.setdefault(cache_key, {}).update({
        "pipeline_complete": True,
        "slug": slug,
        "candidates_file": str(out_path),
    })
    content_path = DATA_DIR / f"{slug}-content.json"
    if content_path.exists():
        cache[cache_key]["content_file"] = str(content_path)
    save_cache(cache)

    t_elapsed = time.monotonic() - t_pipeline_start
    n_final = sum(1 for c in candidates if c.get("top8"))
    _emit("done", f"✅ Found {n_final} viral-ready moments", 100, slug=slug)
    print(f"💾 Pipeline cached → re-runs will be instant")
    print(f"\n⏱  Pipeline finished in {t_elapsed:.1f}s  "
          f"(hk_skipped={n_hk}, dupes_removed={n_dupes}, final_moments={len(candidates)})")

    if args.lock:
        _lock_golden(slug, cache_key)


if __name__ == "__main__":
    main()
