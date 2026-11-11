#!/usr/bin/env python3
"""backend/app.py — Replay Engine demo API.

Endpoints:
  GET  /webinars          → list all precomputed webinars
  GET  /results/{slug}    → golden candidates + content (no API calls)
  POST /run               → stream pipeline progress as SSE, then results

Run:
  uvicorn backend.app:app --reload --port 8000
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import AsyncGenerator

import subprocess

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
PRECOMPUTED_DIR = DATA_DIR / "precomputed"

load_dotenv(ROOT / ".env")

DEMO_PASSCODE = os.environ.get("DEMO_PASSCODE", "")
MAX_VIDEO_HOURS = 2.0
MAX_RUNS_PER_IP_PER_DAY = 3

app = FastAPI(title="Replay Engine API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory rate limiter: ip → {date_str → run_count}
_rate_limit: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


# ── helpers ───────────────────────────────────────────────────────────────────

def _check_passcode(x_passcode: str | None) -> None:
    if not DEMO_PASSCODE:
        raise HTTPException(status_code=500, detail="DEMO_PASSCODE not set in .env")
    if x_passcode != DEMO_PASSCODE:
        raise HTTPException(status_code=401, detail="Invalid or missing passcode (X-Passcode header).")


def _check_rate_limit(ip: str) -> None:
    today = date.today().isoformat()
    if _rate_limit[ip][today] >= MAX_RUNS_PER_IP_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit reached: max {MAX_RUNS_PER_IP_PER_DAY} runs per IP per day.",
        )


def _increment_rate_limit(ip: str) -> None:
    today = date.today().isoformat()
    _rate_limit[ip][today] += 1


def _get_video_duration_seconds(url: str) -> int | None:
    """Call yt-dlp to get video duration, return None on failure."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--print", "%(duration)s", "--no-playlist", url],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            raw = result.stdout.strip().splitlines()[-1]
            return int(raw)
    except Exception:
        pass
    return None


# ── GET /webinars ─────────────────────────────────────────────────────────────

@app.get("/webinars")
def list_webinars():
    """Return all precomputed webinars sorted by date descending."""
    webinars = []
    for index_file in sorted(PRECOMPUTED_DIR.glob("*/index.json")):
        try:
            webinars.append(json.loads(index_file.read_text()))
        except Exception:
            pass
    webinars.sort(key=lambda w: w.get("date", ""), reverse=True)
    return webinars


# ── GET /results/{slug} ───────────────────────────────────────────────────────

@app.get("/results/{slug}")
def get_results(slug: str):
    """Return golden candidates + content for a precomputed webinar."""
    index_file = PRECOMPUTED_DIR / slug / "index.json"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail=f"No precomputed results for '{slug}'.")

    index = json.loads(index_file.read_text())

    candidates_file = DATA_DIR / f"{slug}-candidates.golden.json"
    if not candidates_file.exists():
        raise HTTPException(status_code=404, detail="Golden candidates file not found.")
    candidates = json.loads(candidates_file.read_text())

    content_file = DATA_DIR / f"{slug}-content.golden.json"
    content = json.loads(content_file.read_text()) if content_file.exists() else []

    content_ids = {item["id"] for item in content}
    top = [
        {
            "id": c["id"],
            "score": min(c.get("score", 0), 95),
            "_raw_score": c.get("score", 0),
            "speaker_name": c.get("speaker_name", ""),
            "post_for": c.get("post_for", ""),
            "moment_type": c.get("moment_type", ""),
            "angle": c.get("angle", ""),
            "top8": c.get("top8", False),
            "why": c.get("why", ""),
            "trimmed": c.get("trimmed", False),
            "start_ms": c.get("start_ms", 0),
            "end_ms": c.get("end_ms", 0),
            "duration_s": c.get("duration_s", 0),
            "hook_line": c.get("hook_line", ""),
        }
        for c in sorted(candidates, key=lambda c: -c.get("score", 0))
        if c.get("top8") and c["id"] in content_ids
    ]

    return {
        "index": index,
        "top_moments": top,
        "content": content,
    }


# ── GET /events/{slug} ───────────────────────────────────────────────────────

@app.get("/events/{slug}")
def get_events(slug: str):
    """Return precomputed replay events for a webinar."""
    events_file = PRECOMPUTED_DIR / slug / "events.json"
    if not events_file.exists():
        raise HTTPException(status_code=404, detail=f"No replay events for '{slug}'.")
    return json.loads(events_file.read_text())


# ── POST /run (SSE) ───────────────────────────────────────────────────────────

async def _stream_pipeline(
    cmd: list[str],
) -> AsyncGenerator[str, None]:
    """Run pipeline subprocess, yielding SSE-formatted events from stderr."""
    env = os.environ.copy()
    start_time = time.time()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(ROOT),
    )

    slug = None
    saw_error = False
    collected_events: list[dict] = []

    # Read stderr (progress JSON events) line by line
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            evt = json.loads(text)
            if evt.get("step") == "done":
                slug = evt.get("slug")
            if evt.get("step") == "error":
                saw_error = True
            collected_events.append({**evt, "ts": round(time.time() - start_time, 2)})
            yield f"data: {json.dumps(evt)}\n\n"
        except json.JSONDecodeError:
            pass  # Non-JSON stderr lines are human-readable progress; skip

    await proc.wait()

    if proc.returncode != 0:
        if not saw_error:
            # Unexpected crash — surface stdout for diagnosis
            stdout_bytes = await proc.stdout.read()
            err_text = stdout_bytes.decode("utf-8", errors="replace").strip().splitlines()
            err_msg = next(
                (l for l in reversed(err_text) if "❌" in l),
                err_text[-1] if err_text else "Pipeline failed with no output.",
            )
            yield f"data: {json.dumps({'step': 'error', 'message': err_msg, 'percent': 0})}\n\n"
        yield "data: {\"step\": \"stream_done\"}\n\n"
        return

    # Persist replay events if this slug has a precomputed directory
    if slug and not saw_error and collected_events:
        slug_dir = PRECOMPUTED_DIR / slug
        if slug_dir.exists():
            try:
                (slug_dir / "events.json").write_text(json.dumps(collected_events, indent=2))
            except OSError:
                pass

    # Success — read result files and emit as final event
    result: dict = {"slug": slug}
    if slug:
        candidates_file = DATA_DIR / f"{slug}-candidates.json"
        content_file = DATA_DIR / f"{slug}-content.json"
        if candidates_file.exists():
            try:
                candidates = json.loads(candidates_file.read_text())
                result["top_moments"] = [
                    {
                        "id": c["id"],
                        "score": min(c.get("score", 0), 95),
                        "_raw_score": c.get("score", 0),
                        "speaker_name": c.get("speaker_name", ""),
                        "post_for": c.get("post_for", ""),
                        "moment_type": c.get("moment_type", ""),
                        "angle": c.get("angle", ""),
                        "top8": c.get("top8", False),
                        "why": c.get("why", ""),
                        "trimmed": c.get("trimmed", False),
                        "start_ms": c.get("start_ms", 0),
                        "end_ms": c.get("end_ms", 0),
                        "duration_s": c.get("duration_s", 0),
                        "hook_line": c.get("hook_line", ""),
                    }
                    for c in sorted(candidates, key=lambda c: -c.get("score", 0))
                    if c.get("top8")
                ]
            except Exception:
                pass
        if content_file.exists():
            try:
                result["content"] = json.loads(content_file.read_text())
            except Exception:
                pass

    yield f"data: {json.dumps({'step': 'results', 'data': result})}\n\n"
    yield "data: {\"step\": \"stream_done\"}\n\n"


@app.post("/run")
async def run_pipeline(
    request: Request,
    url: str | None = Form(default=None),
    transcript: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    x_passcode: str | None = Header(default=None),
):
    """
    Stream pipeline progress as Server-Sent Events.

    Accepts one of:
      - url: YouTube URL
      - transcript: pasted plain text (no timestamps)
      - file: uploaded audio/video/JSON

    Requires X-Passcode header matching DEMO_PASSCODE.
    Rate limit: 3 runs per IP per day.
    Max video duration: 2 hours.
    """
    _check_passcode(x_passcode)

    client_ip = request.client.host
    _check_rate_limit(client_ip)

    if not url and not transcript and not file:
        raise HTTPException(status_code=422, detail="Provide url, transcript, or file.")

    # Duration guard for YouTube URLs
    if url:
        dur_s = _get_video_duration_seconds(url)
        if dur_s is not None and dur_s > MAX_VIDEO_HOURS * 3600:
            raise HTTPException(
                status_code=422,
                detail=f"Video is {dur_s / 3600:.1f}h — max {MAX_VIDEO_HOURS:.0f}h allowed.",
            )

    _increment_rate_limit(client_ip)

    tmp_path: str | None = None

    if url:
        cmd = [sys.executable, str(ROOT / "scripts" / "pipeline.py"), url, "--fresh"]
    elif transcript:
        fd, tmp_path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(transcript)
        cmd = [sys.executable, str(ROOT / "scripts" / "pipeline.py"), tmp_path, "--fresh"]
    else:
        suffix = Path(file.filename or "upload").suffix or ".mp4"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(await file.read())
        cmd = [sys.executable, str(ROOT / "scripts" / "pipeline.py"), tmp_path, "--fresh"]

    async def generate():
        try:
            async for chunk in _stream_pipeline(cmd):
                yield chunk
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
