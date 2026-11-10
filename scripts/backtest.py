#!/usr/bin/env python3
"""
scripts/backtest.py — Validate the judge rubric against real post performance.

For each qualifying author (20+ originals): takes the 30 most recent original
posts that were NOT used in hooks.md, tags them with the same Haiku tagger as
analyze_posts.py, scores with the full rubric, then compares judge rank to
real-reaction rank.

Prints per-author Spearman ρ, top-10 overlap vs random baseline (3.3/10),
and the 3 biggest misses across all authors.

Usage:
    python3 scripts/backtest.py
"""

import json
import math
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from scipy.stats import spearmanr

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
JUDGE_DIR = ROOT / "skills" / "gtm-viral-judge"
load_dotenv(ROOT / ".env")

MIN_POSTS = 20
WINNER_THRESHOLD = 3.0
POSTS_PER_AUTHOR = 30
RANDOM_BASELINE = POSTS_PER_AUTHOR / POSTS_PER_AUTHOR * 10 / POSTS_PER_AUTHOR * POSTS_PER_AUTHOR
# = 10 * (10/30) = 3.33 — expected top-10 overlap if scores are random
RANDOM_BASELINE = round(10 * 10 / POSTS_PER_AUTHOR, 1)  # 3.3

# ── import shared tagger from analyze_posts ───────────────────────────────────
import sys
sys.path.insert(0, str(ROOT / "scripts"))
from analyze_posts import SYSTEM_PROMPT, MODEL, tag_batch, extract_features, wc_bucket


# ── rubric scoring ────────────────────────────────────────────────────────────

def rubric_score(post: dict, rubric: dict) -> int:
    """Score a post against all rubric features using extract_features() output."""
    feats = extract_features(post)
    score = rubric["base_score"]

    for feat_def in rubric["features"]:
        name = feat_def["name"]
        ftype = feat_def["type"]

        if ftype == "categorical":
            for f in feats:
                matched_val = None
                if name == "hook_type" and f.startswith("hook_type:"):
                    matched_val = f[len("hook_type:"):]
                elif name == "hook_length" and f.startswith("hook_") and ":" not in f:
                    matched_val = f[len("hook_"):]
                elif name == "topic" and f.startswith("topic:"):
                    matched_val = f[len("topic:"):]
                elif name == "word_count" and f.startswith("wc_bucket:"):
                    matched_val = f[len("wc_bucket:"):]
                elif name == "post_type" and f.startswith("post_type:"):
                    matched_val = f[len("post_type:"):]
                if matched_val is not None:
                    score += feat_def["values"].get(matched_val, {}).get("weight", 0)
                    break
        elif ftype == "boolean":
            if name in feats:
                score += feat_def.get("weight_true", 0)

    return max(0, min(95, score))


# ── helpers ───────────────────────────────────────────────────────────────────

def load_hooks_first_lines(hooks_path: Path) -> set:
    text = hooks_path.read_text()
    lines = set()
    for line in text.splitlines():
        m = re.match(r"^\s*\d+\.\s+\[[\d.]+×\]\s+(.+)$", line)
        if m:
            lines.add(m.group(1).strip().lower()[:80])
    return lines


def rank_desc(vals: list) -> list:
    """Return 1-based ranks (1 = highest)."""
    order = sorted(range(len(vals)), key=lambda i: -vals[i])
    ranks = [0] * len(vals)
    for rank, idx in enumerate(order, 1):
        ranks[idx] = rank
    return ranks


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    posts = json.loads((DATA_DIR / "posts.json").read_text())
    rubric = json.loads((JUDGE_DIR / "rubric.json").read_text())
    hooks_first_lines = load_hooks_first_lines(JUDGE_DIR / "hooks.md")

    originals = [p for p in posts if not p["is_repost"]]
    by_author: dict = defaultdict(list)
    for p in originals:
        by_author[p["author"]].append(p)
    qualifying = {a: ps for a, ps in by_author.items() if len(ps) >= MIN_POSTS}

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    all_scored: list[dict] = []
    author_results: list[dict] = []

    for author, author_posts in sorted(qualifying.items()):
        sorted_posts = sorted(author_posts, key=lambda p: p.get("date", ""), reverse=True)

        # 30 most recent not used in hooks.md
        sample = []
        for p in sorted_posts:
            fl = p["text"].split("\n")[0].strip().lower()[:80]
            if fl not in hooks_first_lines:
                sample.append(p)
            if len(sample) == POSTS_PER_AUTHOR:
                break

        if len(sample) < 5:
            print(f"  ⚠️  {author}: only {len(sample)} posts after filtering — skipping")
            continue

        print(f"\n{author} ({len(sample)} posts)…")

        # Tag all posts in batches
        n_batches = math.ceil(len(sample) / 20)
        for i in range(0, len(sample), 20):
            batch = sample[i : i + 20]
            batch_num = i // 20 + 1
            print(f"   batch {batch_num}/{n_batches}…", end=" ", flush=True)
            try:
                tags_list = tag_batch(client, batch)
                for post, tags in zip(batch, tags_list):
                    post["tags"] = {k: v for k, v in tags.items() if k != "i"}
                print("✓")
            except Exception as exc:
                print(f"⚠️  {exc}")
                for post in batch:
                    post["tags"] = {}

        # Score each post
        for p in sample:
            score = rubric_score(p, rubric)
            feats = extract_features(p)
            hook_type = (p.get("tags") or {}).get("hook_type", "other")
            p["judge_score"] = score
            p["features_applied"] = sorted(feats)

        reactions = [p["reactions"] for p in sample]
        scores = [p["judge_score"] for p in sample]

        rho, pval = spearmanr(reactions, scores)

        real_ranks = rank_desc(reactions)
        judge_ranks = rank_desc(scores)
        real_top10 = set(i for i, r in enumerate(real_ranks) if r <= 10)
        judge_top10 = set(i for i, r in enumerate(judge_ranks) if r <= 10)
        top10_overlap = len(real_top10 & judge_top10)

        for i, p in enumerate(sample):
            p["real_rank"] = real_ranks[i]
            p["judge_rank"] = judge_ranks[i]
            p["rank_delta"] = abs(real_ranks[i] - judge_ranks[i])
            p["author"] = author
            all_scored.append(p)

        author_results.append({
            "author": author,
            "n": len(sample),
            "spearman_rho": round(rho, 4),
            "p_value": round(pval, 4),
            "top10_overlap": top10_overlap,
        })

        beat = "✓ beats" if top10_overlap > RANDOM_BASELINE else "✗ below"
        print(f"   ρ={rho:.3f} | top-10 overlap {top10_overlap}/10 ({beat} baseline {RANDOM_BASELINE})")

    # Aggregate across all authors
    all_reactions = [p["reactions"] for p in all_scored]
    all_scores = [p["judge_score"] for p in all_scored]
    agg_rho, agg_pval = spearmanr(all_reactions, all_scores)

    biggest_misses = sorted(all_scored, key=lambda p: -p["rank_delta"])[:3]

    # ── print final summary ──
    print(f"\n{'═'*60}")
    print("Per-author results:")
    for r in author_results:
        beat = "✓" if r["top10_overlap"] > RANDOM_BASELINE else "✗"
        print(
            f"  {r['author']:<30}  ρ={r['spearman_rho']:+.3f}  "
            f"top-10 {r['top10_overlap']}/10 {beat}  (n={r['n']})"
        )
    print(f"\nAggregate (n={len(all_scored)} posts): Spearman ρ = {agg_rho:.3f}  (p={agg_pval:.3f})")
    print(f"Random baseline: {RANDOM_BASELINE}/10")

    print("\n3 biggest misses (real rank vs judge rank):")
    for miss in biggest_misses:
        fl = miss["text"].split("\n")[0].strip()
        print(
            f"  [{miss['author'][:20]:<20}]  "
            f"real #{miss['real_rank']:2d} → judge #{miss['judge_rank']:2d}  "
            f"(Δ{miss['rank_delta']})  react={miss['reactions']}  score={miss['judge_score']}"
        )
        print(f"    \"{fl[:70]}\"")

    # ── save ──
    out = {
        "aggregate_spearman_rho": round(agg_rho, 4),
        "aggregate_p_value": round(agg_pval, 4),
        "random_baseline_top10": RANDOM_BASELINE,
        "n_total": len(all_scored),
        "author_results": author_results,
        "biggest_misses": [
            {
                "author": m["author"],
                "first_line": m["text"].split("\n")[0].strip(),
                "reactions": m["reactions"],
                "real_rank": m["real_rank"],
                "judge_rank": m["judge_rank"],
                "rank_delta": m["rank_delta"],
                "judge_score": m["judge_score"],
                "hook_type": (m.get("tags") or {}).get("hook_type", "?"),
            }
            for m in biggest_misses
        ],
        "posts": [
            {
                "author": p["author"],
                "date": p["date"],
                "first_line": p["text"].split("\n")[0].strip(),
                "reactions": p["reactions"],
                "judge_score": p["judge_score"],
                "real_rank": p["real_rank"],
                "judge_rank": p["judge_rank"],
                "rank_delta": p["rank_delta"],
                "hook_type": (p.get("tags") or {}).get("hook_type", "?"),
                "features": p.get("features_applied", []),
            }
            for p in all_scored
        ],
    }
    out_path = DATA_DIR / "backtest.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n💾 Saved → {out_path}")


if __name__ == "__main__":
    main()
