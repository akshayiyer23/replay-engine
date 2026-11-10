#!/usr/bin/env python3
"""
scripts/backtest.py — Validate the judge rubric against real post performance.

Takes the 30 most recent original posts by austin-t-hughes from data/posts.json
that were NOT used in hooks.md. Scores each first line with the judge rubric,
then compares to real reactions using Spearman rank correlation.

Saves to data/backtest.json and prints:
  - Spearman correlation
  - Top-10 overlap (how many of real top 10 appear in judge's top 10)
  - 3 biggest misses (largest rank discrepancy)

Usage:
    python3 scripts/backtest.py
"""

import json
import os
import re
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from scipy.stats import spearmanr

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
JUDGE_DIR = ROOT / "skills" / "gtm-viral-judge"
load_dotenv(ROOT / ".env")

HAIKU = "claude-haiku-5-5"

_TAG_USER = """\
Tag this LinkedIn opening line. Return only valid JSON, no prose.
Line: "{line}"
Schema: {{"hook_type": "contrarian"|"stat"|"story"|"how_to"|"prediction"|"other", "first_line_words": <integer>}}\
"""


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text.strip())
    return text.strip()


def _tag_line(line: str, client: anthropic.Anthropic) -> dict:
    resp = client.messages.create(
        model=HAIKU,
        max_tokens=80,
        messages=[{"role": "user", "content": _TAG_USER.format(line=line[:200])}],
    )
    raw = _strip_fences(resp.content[0].text)
    return json.loads(raw)


def _hook_weight(tag: dict, rubric: dict) -> int:
    weight = 0
    for feat in rubric["features"]:
        if feat["name"] == "hook_type":
            weight += feat["values"].get(tag.get("hook_type", "other"), {}).get("weight", 0)
        elif feat["name"] == "hook_length":
            n = int(tag.get("first_line_words", 0))
            bucket = "1-5w" if n <= 5 else ("6-10w" if n <= 10 else "11+w")
            weight += feat["values"].get(bucket, {}).get("weight", 0)
    return weight


def _load_hooks_first_lines(hooks_path: Path) -> set[str]:
    """Extract the raw first-line text from hooks.md entries."""
    text = hooks_path.read_text()
    lines = set()
    for line in text.splitlines():
        m = re.match(r"^\s*\d+\.\s+\[\d+.*?\]\s+(.+)$", line)
        if m:
            lines.add(m.group(1).strip().lower()[:80])
    return lines


def main() -> None:
    posts = json.loads((DATA_DIR / "posts.json").read_text())
    rubric = json.loads((JUDGE_DIR / "rubric.json").read_text())
    hooks_first_lines = _load_hooks_first_lines(JUDGE_DIR / "hooks.md")

    # 30 most recent original austin posts not used in hooks.md
    austin = [p for p in posts if p["author"] == "austin-t-hughes" and not p["is_repost"]]
    austin_sorted = sorted(austin, key=lambda p: p.get("date", ""), reverse=True)

    sample = []
    for p in austin_sorted:
        first_line = p["text"].split("\n")[0].strip()
        if first_line.lower()[:80] not in hooks_first_lines:
            sample.append(p)
        if len(sample) == 30:
            break

    if len(sample) < 30:
        print(f"⚠️  Only {len(sample)} posts available after filtering hooks.md")

    print(f"📊 Scoring {len(sample)} austin-t-hughes posts…")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Base score same as judge: 50 + hook weights (no topic/number features since
    # we only have the first line, not the full post)
    BASE = 50
    scored = []
    for i, p in enumerate(sample):
        first_line = p["text"].split("\n")[0].strip()
        try:
            tag = _tag_line(first_line, client)
        except Exception:
            tag = {"hook_type": "other", "first_line_words": len(first_line.split())}
        w = _hook_weight(tag, rubric)
        score = max(0, min(95, BASE + w))
        scored.append({
            "date": p["date"],
            "first_line": first_line,
            "reactions": p["reactions"],
            "hook_type": tag.get("hook_type", "other"),
            "first_line_words": tag.get("first_line_words", 0),
            "judge_score": score,
        })
        print(f"  {i+1:2d}. score={score:3d} [{tag.get('hook_type','?'):12s}] react={p['reactions']:4d}  {first_line[:55]}")

    # Rank: 1 = best
    reactions_list = [s["reactions"] for s in scored]
    judge_scores = [s["judge_score"] for s in scored]

    def rank_desc(vals: list[int]) -> list[int]:
        """Rank 1 = highest value."""
        order = sorted(range(len(vals)), key=lambda i: -vals[i])
        ranks = [0] * len(vals)
        for rank, idx in enumerate(order, 1):
            ranks[idx] = rank
        return ranks

    real_ranks = rank_desc(reactions_list)
    judge_ranks = rank_desc(judge_scores)

    for i, s in enumerate(scored):
        s["real_rank"] = real_ranks[i]
        s["judge_rank"] = judge_ranks[i]
        s["rank_delta"] = abs(real_ranks[i] - judge_ranks[i])

    rho, pval = spearmanr(reactions_list, judge_scores)

    real_top10 = set(i for i, r in enumerate(real_ranks) if r <= 10)
    judge_top10 = set(i for i, r in enumerate(judge_ranks) if r <= 10)
    top10_overlap = len(real_top10 & judge_top10)

    biggest_misses = sorted(scored, key=lambda s: -s["rank_delta"])[:3]

    print()
    print(f"{'─'*60}")
    print(f"Spearman ρ = {rho:.3f}  (p={pval:.3f})")
    print(f"Top-10 overlap: {top10_overlap}/10")
    print()
    print("3 biggest misses (real rank vs judge rank):")
    for miss in biggest_misses:
        print(
            f"  real #{miss['real_rank']:2d} → judge #{miss['judge_rank']:2d}  "
            f"(Δ{miss['rank_delta']})  react={miss['reactions']}  "
            f"[{miss['hook_type']}]  {miss['first_line'][:50]}"
        )

    out = {
        "spearman_rho": round(rho, 4),
        "p_value": round(pval, 4),
        "top10_overlap": top10_overlap,
        "n": len(scored),
        "biggest_misses": [
            {
                "first_line": m["first_line"],
                "reactions": m["reactions"],
                "real_rank": m["real_rank"],
                "judge_rank": m["judge_rank"],
                "rank_delta": m["rank_delta"],
                "hook_type": m["hook_type"],
            }
            for m in biggest_misses
        ],
        "posts": scored,
    }
    out_path = DATA_DIR / "backtest.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n💾 Saved → {out_path}")


if __name__ == "__main__":
    main()
