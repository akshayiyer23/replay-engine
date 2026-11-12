#!/usr/bin/env python3
"""
tests/run_regression.py — Regression suite for the replay engine pipeline.

Runs the full pipeline (--fresh) on 5 test cases and validates output quality.
Saves a markdown report to data/regression_report.md.

Usage:
    cd /path/to/replay-engine
    python3 tests/run_regression.py

Takes ~10–20 min total (API calls).  Requires .env with all keys.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

# ── test-case definitions ─────────────────────────────────────────────────────

CASES = [
    {
        "id": "gtmnow-austin",
        "label": "(a) GTMnow – Austin Hughes (Unify people present)",
        "input": "https://www.youtube.com/watch?v=4Grsbi3hFL4",
        "expect_success": True,
        "expect_routing": "speaker",   # posts should be for named speakers
        "min_posts": 4,
    },
    {
        "id": "gtm-no-unify",
        "label": "(b) GTM podcast – no Unify people",
        "input": "https://www.youtube.com/watch?v=oDzzjMGGjb8",
        "expect_success": True,
        "expect_routing": "company",   # posts should fall back to Unify company page
        "min_posts": 2,
    },
    {
        "id": "non-english",
        "label": "(c) Non-English / music video – should fail gracefully",
        # Despacito (Spanish music video) — no GTM content
        "input": "https://www.youtube.com/watch?v=ktvTqknDobU",
        "expect_success": False,       # pipeline should error or produce 0 posts
        "expect_routing": None,
        "min_posts": 0,
    },
    {
        "id": "txt-transcript",
        "label": "(d) Pasted transcript (plain text, no timestamps)",
        "input": str(ROOT / "tests" / "fixtures" / "sample_transcript.txt"),
        "expect_success": True,
        "expect_routing": None,
        "min_posts": 1,
        "setup": "create_sample_transcript",
    },
    {
        "id": "long-video",
        "label": "(e) Very long video (>2 h) – duration guard should reject",
        # MasterClass-style long-form video; use a known long one
        # Swap for any verified YouTube video longer than 2 hours
        "input": "https://www.youtube.com/watch?v=09R8_2nJtjg",
        "expect_success": False,       # should be rejected by duration guard in app.py
        "expect_routing": None,
        "min_posts": 0,
        "note": "Duration guard fires at >2 h in backend/app.py; pipeline itself will process it.",
    },
]

# ── helpers ───────────────────────────────────────────────────────────────────

def _create_sample_transcript() -> None:
    """Create a minimal plain-text transcript fixture for test (d)."""
    fixture_dir = ROOT / "tests" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    path = fixture_dir / "sample_transcript.txt"
    if not path.exists():
        path.write_text(
            "We spent six months building the wrong thing. "
            "Everyone on the team knew it, but nobody said it out loud. "
            "That's the real lesson about product-market fit: silence isn't agreement. "
            "We had seven enterprise pilots, three of which were paying, "
            "and we still decided to pivot because the retention numbers told the truth. "
            "The best thing we ever did was cancel the roadmap and go talk to customers. "
            "Twenty interviews in two weeks. That's it. "
            "If you're waiting for a signal, you already have it. "
            "What's the last time you talked to a customer who churned?"
        )
        print(f"Created fixture: {path}")


def _run_pipeline(input_str: str, slug_hint: str) -> tuple[bool, float, dict | None, str]:
    """Run pipeline and return (success, elapsed_s, results_dict_or_None, stderr_tail)."""
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "pipeline.py"),
        input_str,
        "--fresh",
        "--slug", slug_hint,
    ]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(ROOT),
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        return False, time.time() - t0, None, "TIMEOUT after 600s"

    elapsed = time.time() - t0
    success = proc.returncode == 0

    # Try to load content
    content_path = DATA_DIR / f"{slug_hint}-content.json"
    results = None
    if content_path.exists():
        try:
            results = json.loads(content_path.read_text())
        except Exception:
            pass

    stderr_tail = "\n".join(proc.stderr.strip().splitlines()[-20:])
    return success, elapsed, results, stderr_tail


def _audit_results(results: list[dict]) -> list[str]:
    """Run audit checks on a set of content results, return list of issue strings."""
    if not results:
        return []
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from audit_content import audit_post
        issues = []
        for item in results:
            item_issues = audit_post(item)
            if item_issues:
                issues.append(f"{item['id']}: {'; '.join(item_issues)}")
        return issues
    except Exception as exc:
        return [f"audit import error: {exc}"]


def _routing_summary(results: list[dict]) -> str:
    if not results:
        return "none"
    post_fors = [r.get("post_for", "?") for r in results]
    return ", ".join(sorted(set(post_fors)))


def _top3_first_lines(results: list[dict]) -> list[str]:
    return [
        r.get("post_first_line", r.get("linkedin_post", "")[:60].split("\n")[0])
        for r in results[:3]
    ]


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    report_lines = ["# Regression Report\n"]
    report_lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    overall_pass = 0
    overall_fail = 0

    for case in CASES:
        print(f"\n{'='*70}")
        print(f"Running: {case['label']}")
        print(f"Input  : {case['input']}")
        print(f"{'='*70}")

        # Setup if needed
        if case.get("setup") == "create_sample_transcript":
            _create_sample_transcript()

        # Skip if input file doesn't exist (fixture not created)
        if not case["input"].startswith("http") and not Path(case["input"]).exists():
            print(f"  ⚠️  Input file not found — skipping: {case['input']}")
            report_lines.append(f"\n## {case['label']}\n⚠️ SKIPPED — input file missing\n")
            continue

        success, elapsed, results, stderr_tail = _run_pipeline(case["input"], case["id"])

        n_posts = len(results) if results else 0
        audit_issues = _audit_results(results or [])
        routing = _routing_summary(results or [])
        top3 = _top3_first_lines(results or [])

        expected_success = case["expect_success"]
        min_posts = case["min_posts"]

        # Evaluate pass/fail
        pipeline_ok = (success == expected_success)
        posts_ok = (n_posts >= min_posts)
        audit_ok = len(audit_issues) == 0
        passed = pipeline_ok and posts_ok and audit_ok

        status = "✅ PASS" if passed else "❌ FAIL"
        if passed:
            overall_pass += 1
        else:
            overall_fail += 1

        print(f"  {status}  |  {elapsed:.0f}s  |  {n_posts} posts  |  {len(audit_issues)} audit issues")
        if top3:
            for line in top3:
                print(f"    → {line}")

        # Build report section
        report_lines.append(f"\n## {case['label']}")
        report_lines.append(f"**Status:** {status}  |  **Time:** {elapsed:.0f}s  |  **Posts:** {n_posts}")
        report_lines.append(f"**Routing:** {routing}")
        if top3:
            report_lines.append("**Top 3 first lines:**")
            for line in top3:
                report_lines.append(f"- {line}")
        if audit_issues:
            report_lines.append(f"**Audit issues ({len(audit_issues)}):**")
            for iss in audit_issues[:5]:
                report_lines.append(f"- {iss}")
        if case.get("note"):
            report_lines.append(f"**Note:** {case['note']}")
        failures = []
        if not pipeline_ok:
            failures.append(f"pipeline returned rc={'0' if success else 'non-0'}, expected {'0' if expected_success else 'non-0'}")
        if not posts_ok:
            failures.append(f"only {n_posts} posts, expected ≥{min_posts}")
        if failures:
            report_lines.append(f"**Failures:** {'; '.join(failures)}")
        report_lines.append("")

    # Summary
    report_lines.append(f"\n---\n## Summary\n{overall_pass} passed · {overall_fail} failed")
    total = overall_pass + overall_fail
    if total and overall_fail == 0:
        report_lines.append("\n✅ All tests passed.")
    elif total:
        report_lines.append(f"\n⚠️  {overall_fail}/{total} test(s) failed.")

    report_path = DATA_DIR / "regression_report.md"
    report_path.write_text("\n".join(report_lines))
    print(f"\n{'='*70}")
    print(f"Report saved → {report_path}")
    print(f"{overall_pass} passed · {overall_fail} failed")
    if overall_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
