#!/usr/bin/env python3
"""
scripts/pipeline.py — End-to-end webinar → clip candidates pipeline.

Usage:
    python scripts/pipeline.py <youtube-url>
    python scripts/pipeline.py <sentences.json>
    python scripts/pipeline.py <url> --speakers "A=Alice,B=Bob" --slug my-webinar
"""

import argparse
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
CACHE_FILE = DATA_DIR / ".transcript_cache.json"

MODEL = "claude-sonnet-5-5"
CHUNK_MS = 600_000  # 10 minutes

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
    subprocess.run(
        ["yt-dlp", "-f", "bestaudio", "--no-post-overwrites", "-o", out_template, url],
        check=True,
    )
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
    sentences_payload = json.dumps(
        [{"i": i, **s} for i, s in chunk], indent=2
    )

    user_msg = (
        f"Speaker map: {speaker_map_str}\n\n"
        f"Sentences (each has an absolute index 'i' into the full transcript):\n"
        f"{sentences_payload}\n\n"
        "Find 5–8 strong clip candidates from these sentences only.\n\n"
        "Return a JSON array where each element has EXACTLY these fields — "
        "no transcript, no hook_line, no timestamps:\n"
        "  id, start_i, end_i, moment_type, pillar, connects_to, "
        "persona, post_for, angle, standalone_check, risks\n\n"
        "start_i and end_i must be the 'i' values from the sentences above.\n"
        "Output only the JSON array — no prose, no markdown fences."
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=skill_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = strip_fences(response.content[0].text)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\[.*?\]", raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                print("   ⚠️  Could not parse Claude response — skipping chunk")
                return []
        else:
            print("   ⚠️  No JSON array found in Claude response — skipping chunk")
            return []

    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return parsed.get("candidates", [])
    return []


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Webinar → clip candidates pipeline")
    parser.add_argument("input", help="YouTube URL or path to sentences JSON file")
    parser.add_argument(
        "--speakers",
        default="A=Quintin Smith,B=Ethan Dursht",
        metavar="A=Name,B=Name",
        help="Speaker map (default: A=Quintin Smith, B=Ethan Dursht)",
    )
    parser.add_argument("--slug", help="Output slug override")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    speaker_map = parse_speakers(args.speakers)

    # ── 1. sentences ──
    input_path = Path(args.input)
    if input_path.exists():
        print(f"📄 Loading transcript from {input_path}…")
        sentences = json.loads(input_path.read_text())
        slug = args.slug or input_path.stem.removesuffix("-sentences")
    else:
        sentences, slug = get_sentences_for_url(args.input)
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
        print(f"🔍 Scanning chunk {i + 1}/{len(chunks)} "
              f"({chunk_start:.0f}–{chunk_end:.0f} min)…")
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

    # ── 5. save ──
    out_path = DATA_DIR / f"{slug}-candidates.json"
    out_path.write_text(json.dumps(candidates, indent=2))
    print(f"💾 Saved → {out_path}")

    # ── results table ──
    print()
    header = f"{'id':<5}  {'dur':>5}  {'speaker':<16}  {'type':<12}  angle"
    print(header)
    print("─" * 90)
    for c in candidates:
        flag = " ⚠" if c["duration_flag"] != "OK" else ""
        print(
            f"{c['id']:<5}  {c['duration_s']:>4.0f}s  "
            f"{c['speaker_name']:<16}  {c['moment_type']:<12}  "
            f"{c['angle'][:54]}{flag}"
        )


if __name__ == "__main__":
    main()
