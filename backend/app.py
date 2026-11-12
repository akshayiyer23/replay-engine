#!/usr/bin/env python3
"""backend/app.py — Replay Engine demo API.

Endpoints:
  GET  /webinars            → list all precomputed webinars
  GET  /results/{slug}      → golden candidates + content (no API calls)
  POST /run                 → start background job, return {job_id} immediately
  GET  /jobs/{job_id}       → poll progress events + status since a given index
  GET  /events/{slug}       → precomputed replay events for a webinar

Run:
  uvicorn backend.app:app --reload --port 8000
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid as uuid_module
from collections import defaultdict
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
PRECOMPUTED_DIR = DATA_DIR / "precomputed"

load_dotenv(ROOT / ".env")

# Startup key-presence check (values never logged)
def _check_env() -> None:
    keys = {
        "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "ASSEMBLYAI_API_KEY": bool(os.environ.get("ASSEMBLYAI_API_KEY")),
        "SUPADATA_API_KEY": bool(os.environ.get("SUPADATA_API_KEY")),
        "DEMO_PASSCODE": bool(os.environ.get("DEMO_PASSCODE")),
    }
    for name, present in keys.items():
        status = "set" if present else "NOT SET"
        print(f"[config] {name}: {status}", file=sys.stderr)

_check_env()

DEMO_PASSCODE = os.environ.get("DEMO_PASSCODE", "")
MAX_VIDEO_HOURS = 2.0
MAX_RUNS_PER_IP_PER_DAY = 3

_raw_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app = FastAPI(title="Replay Engine API", version="1.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# ── rate limiter ──────────────────────────────────────────────────────────────

_rate_limit: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


# ── job store ─────────────────────────────────────────────────────────────────
# job_id → {events: list[dict], status: "running"|"done"|"error", created_at: float}

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

JOB_TTL_SECONDS = 7200  # expire jobs after 2 hours


def _cleanup_old_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _jobs_lock:
        stale = [jid for jid, j in _jobs.items() if j.get("created_at", 0) < cutoff]
        for jid in stale:
            del _jobs[jid]


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


def _build_result(slug: str | None, cmd: list[str]) -> dict:
    """Build the result dict from pipeline output files (same shape as old SSE results event)."""
    result: dict = {"slug": slug}
    if not slug:
        return result

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

    slug_dir = PRECOMPUTED_DIR / slug
    if (slug_dir / "index.json").exists():
        try:
            result["index"] = json.loads((slug_dir / "index.json").read_text())
        except Exception:
            pass

    if "index" not in result:
        youtube_id = ""
        for arg in cmd:
            m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", arg) or \
                re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", arg)
            if m:
                youtube_id = m.group(1)
                break
        title = ""
        if youtube_id:
            try:
                encoded = urllib.parse.quote(
                    f"https://www.youtube.com/watch?v={youtube_id}", safe=""
                )
                req = urllib.request.Request(
                    f"https://www.youtube.com/oembed?url={encoded}&format=json",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    title = json.loads(resp.read().decode()).get("title", "")
            except Exception:
                pass
        if not title:
            title = slug.replace("-", " ").title() if slug else "YouTube video"
        top_score = (result.get("top_moments") or [{}])[0].get("score", 0)
        result["index"] = {
            "slug": slug,
            "title": title,
            "date": "",
            "youtube_id": youtube_id,
            "duration": "",
            "speakers": [],
            "top_score": top_score,
        }

    content_items = result.get("content") or []
    result["index"]["quality_checked"] = all(
        item.get("quality_passed", True) for item in content_items
    )

    return result


# ── background job runner ─────────────────────────────────────────────────────

def _run_job(job_id: str, cmd: list[str], tmp_path: str | None) -> None:
    """Run pipeline subprocess in a background thread; write events to job store."""
    start_time = time.time()

    def _append(event: dict) -> None:
        with _jobs_lock:
            _jobs[job_id]["events"].append(event)

    def _finish(status: str) -> None:
        with _jobs_lock:
            _jobs[job_id]["status"] = status

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
            env=os.environ.copy(),
        )

        slug: str | None = None
        saw_error = False
        collected_events: list[dict] = []

        for raw_line in proc.stderr:  # type: ignore[union-attr]
            text = raw_line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                evt = json.loads(text)
                event = {
                    **evt,
                    "ts": round(time.time() - start_time, 2),
                    "percent": evt.get("percent", 0),
                    "message": evt.get("message", ""),
                }
                collected_events.append(event)
                _append(event)

                if evt.get("step") == "done":
                    slug = evt.get("slug")
                if evt.get("step") == "error":
                    saw_error = True
                    if evt.get("message") == "credits_exhausted":
                        print("[CREDITS_EXHAUSTED] Anthropic credits exhausted", file=sys.stderr)
            except json.JSONDecodeError:
                pass

        proc.wait()

        if proc.returncode != 0:
            if not saw_error:
                stdout_bytes = proc.stdout.read()  # type: ignore[union-attr]
                err_lines = stdout_bytes.decode("utf-8", errors="replace").strip().splitlines()
                err_msg = next((l for l in reversed(err_lines) if "❌" in l), None)
                if err_msg is None:
                    last = err_lines[-1] if err_lines else "(no output)"
                    print(f"[pipeline-crash] rc={proc.returncode} stdout={last!r}", file=sys.stderr)
                    err_msg = "run_interrupted"
                _append({"step": "error", "message": err_msg, "percent": 0,
                          "ts": round(time.time() - start_time, 2)})
            _finish("error")
            return

        # Persist replay events if the slug has a precomputed directory
        if slug and not saw_error and collected_events:
            slug_dir = PRECOMPUTED_DIR / slug
            if slug_dir.exists():
                try:
                    (slug_dir / "events.json").write_text(json.dumps(collected_events, indent=2))
                except OSError:
                    pass

        # Build results and emit as a "results" event (same shape as old SSE)
        result = _build_result(slug, cmd)
        _append({
            "step": "results",
            "data": result,
            "ts": round(time.time() - start_time, 2),
            "percent": 100,
            "message": "",
        })
        _finish("done")

    except Exception as exc:
        print(f"[job-crash] {job_id}: {exc}", file=sys.stderr)
        _append({
            "step": "error",
            "message": "run_interrupted",
            "percent": 0,
            "ts": round(time.time() - start_time, 2),
        })
        _finish("error")

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ── GET /webinars ─────────────────────────────────────────────────────────────

@app.get("/webinars")
def list_webinars():
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

    quality_checked = all(item.get("quality_passed", True) for item in content)
    index_with_qc = {**index, "quality_checked": quality_checked}

    return {
        "index": index_with_qc,
        "top_moments": top,
        "content": content,
    }


# ── GET /events/{slug} ────────────────────────────────────────────────────────

@app.get("/events/{slug}")
def get_events(slug: str):
    events_file = PRECOMPUTED_DIR / slug / "events.json"
    if not events_file.exists():
        raise HTTPException(status_code=404, detail=f"No replay events for '{slug}'.")
    return json.loads(events_file.read_text())


# ── POST /run → returns {job_id} immediately ──────────────────────────────────

@app.post("/run")
async def run_pipeline(
    request: Request,
    url: str | None = Form(default=None),
    transcript: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    x_passcode: str | None = Header(default=None),
):
    """
    Start a background pipeline job and return {job_id} immediately.
    Poll GET /jobs/{job_id} for progress and results.

    Accepts one of: url, transcript, file.
    Requires X-Passcode header. Rate limit: 3 runs per IP per day.
    """
    _check_passcode(x_passcode)

    client_ip = request.client.host
    _check_rate_limit(client_ip)

    if not url and not transcript and not file:
        raise HTTPException(status_code=422, detail="Provide url, transcript, or file.")

    # Duration guard — run yt-dlp in a thread to avoid blocking the event loop
    if url:
        dur_s = await asyncio.to_thread(_get_video_duration_seconds, url)
        if dur_s is not None and dur_s > MAX_VIDEO_HOURS * 3600:
            raise HTTPException(
                status_code=422,
                detail=f"Video is {dur_s / 3600:.1f}h — max {MAX_VIDEO_HOURS:.0f}h allowed.",
            )

    _increment_rate_limit(client_ip)
    _cleanup_old_jobs()

    tmp_path: str | None = None

    if url:
        cmd = [sys.executable, str(ROOT / "scripts" / "pipeline.py"), url, "--fresh"]
    elif transcript:
        fd, tmp_path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(transcript)
        cmd = [sys.executable, str(ROOT / "scripts" / "pipeline.py"), tmp_path, "--fresh"]
    else:
        suffix = Path(file.filename or "upload").suffix or ".mp4"  # type: ignore[union-attr]
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(await file.read())  # type: ignore[union-attr]
        cmd = [sys.executable, str(ROOT / "scripts" / "pipeline.py"), tmp_path, "--fresh"]

    job_id = str(uuid_module.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"events": [], "status": "running", "created_at": time.time()}

    t = threading.Thread(target=_run_job, args=(job_id, cmd, tmp_path), daemon=True)
    t.start()

    return {"job_id": job_id}


# ── GET /jobs/{job_id} ────────────────────────────────────────────────────────

@app.get("/jobs/{job_id}")
def get_job(job_id: str, since: int = Query(default=0, ge=0)):
    """
    Return events since `since` index, current status, and total event count.
    Poll this every 2 s until status is 'done' or 'error'.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found or expired.")
        events_slice = list(job["events"][since:])
        total_events = len(job["events"])
        status = job["status"]

    return {
        "status": status,          # "running" | "done" | "error"
        "events": events_slice,    # AgentEvent objects since `since`
        "total_events": total_events,
    }
