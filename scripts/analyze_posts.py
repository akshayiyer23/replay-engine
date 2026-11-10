#!/usr/bin/env python3
"""
scripts/analyze_posts.py — Viral pattern analysis on LinkedIn posts.

Steps:
  1. Load data/posts.json; exclude reposts; keep authors with 20+ originals.
  2. outlier_score = reactions / author_median.
     winner ≥3×  |  normal 0.5–1.5×  |  rest skipped.
  3. Print cost estimate, then tag winners + normals with Claude (batch 20).
  4. Lift = pct_winners_with_feature / pct_normals_with_feature per feature.
  5. Save data/lift.json; print summary.
"""

import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
load_dotenv(ROOT / ".env")

MODEL = "claude-haiku-5-5"
BATCH_SIZE = 20
MIN_POSTS = 20
WINNER_THRESHOLD = 3.0
NORMAL_LOW = 0.5
NORMAL_HIGH = 1.5

# Haiku 4.5 pricing
INPUT_PRICE_PER_M = 0.80
OUTPUT_PRICE_PER_M = 4.00


# ── helpers ───────────────────────────────────────────────────────────────────

def wc_bucket(text: str) -> str:
    n = len(text.split())
    if n < 100:
        return "short"
    if n <= 300:
        return "medium"
    return "long"


def first_line_bucket(n: int) -> str:
    if n <= 5:
        return "hook_1-5w"
    if n <= 10:
        return "hook_6-10w"
    return "hook_11+w"


# ── classification ────────────────────────────────────────────────────────────

def classify(originals: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (winners, normals) for qualifying authors only."""
    by_author: dict[str, list[dict]] = defaultdict(list)
    for p in originals:
        by_author[p["author"]].append(p)

    qualifying = {a for a, ps in by_author.items() if len(ps) >= MIN_POSTS}

    winners, normals = [], []
    for p in originals:
        if p["author"] not in qualifying:
            continue
        med = statistics.median(pp["reactions"] for pp in by_author[p["author"]])
        if med == 0:
            continue
        score = p["reactions"] / med
        enriched = {**p, "outlier_score": round(score, 3), "author_median": round(med, 1)}
        if score >= WINNER_THRESHOLD:
            winners.append({**enriched, "label": "winner"})
        elif NORMAL_LOW <= score <= NORMAL_HIGH:
            normals.append({**enriched, "label": "normal"})

    return winners, normals


# ── Claude tagging ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a content analyst tagging LinkedIn posts for virality research.
Given a JSON array of posts, return a JSON array of the same length (same order). Each element:
{
  "hook_type": "contrarian"|"stat"|"how_to"|"story"|"mistake"|"prediction"|"other",
  "first_line_words": <integer — word count of the very first line>,
  "has_specific_number": true|false,
  "names_a_tool_or_company": true|false,
  "topic": "signals"|"cold_email"|"LinkedIn"|"ai_agents"|"hiring_sdrs"|"career"|"other",
  "announcement_milestone": true|false,
  "salary_money_topic": true|false,
  "personal_story": true|false
}

Definitions:
- announcement_milestone: post is about a job change, funding round, product launch, promotion, company news, or personal achievement
- salary_money_topic: post explicitly mentions a specific salary figure, compensation range, revenue number, or money amount in the context of career/business advice
- personal_story: post is primarily a first-person narrative about a personal experience, journey, or specific event the author lived through

Output only the JSON array. No prose, no markdown fences."""


def tag_batch(client: anthropic.Anthropic, batch: list[dict]) -> list[dict]:
    items = [{"i": idx, "text": p["text"][:800]} for idx, p in enumerate(batch)]
    resp = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "low"}},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Tag these {len(items)} posts:\n{json.dumps(items, indent=2)}"}],
    )
    text_block = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
    if text_block is None:
        raise ValueError(f"No text block in response (blocks: {[getattr(b,'type','?') for b in resp.content]})")
    raw = text_block.text.strip()
    raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
    raw = re.sub(r"\n?```\s*$", "", raw.strip())
    return json.loads(raw)


# ── lift ──────────────────────────────────────────────────────────────────────

def extract_features(p: dict) -> set[str]:
    tags = p.get("tags") or {}
    feats: set[str] = set()

    # Categorical → binary per value
    for field in ("hook_type", "topic"):
        v = tags.get(field)
        if v:
            feats.add(f"{field}:{v}")

    # Boolean
    for bool_feat in (
        "has_specific_number",
        "names_a_tool_or_company",
        "announcement_milestone",
        "salary_money_topic",
        "personal_story",
    ):
        if tags.get(bool_feat):
            feats.add(bool_feat)

    # first_line_words → bucket
    flw = tags.get("first_line_words")
    if isinstance(flw, (int, float)):
        feats.add(first_line_bucket(int(flw)))

    # Derived from existing data
    feats.add(f"post_type:{p['post_type']}")
    feats.add(f"wc_bucket:{wc_bucket(p['text'])}")

    return feats


def compute_lift(winners: list[dict], normals: list[dict]) -> dict:
    nw, nn = len(winners), len(normals)
    all_feats: set[str] = set()
    for p in winners + normals:
        all_feats |= extract_features(p)

    result = {}
    for feat in sorted(all_feats):
        cnt_w = sum(1 for p in winners if feat in extract_features(p))
        cnt_n = sum(1 for p in normals if feat in extract_features(p))
        pct_w = cnt_w / nw
        pct_n = cnt_n / nn
        raw_lift = (pct_w / pct_n) if pct_n > 0 else (99.0 if pct_w > 0 else 1.0)
        low_confidence = cnt_w < 5
        result[feat] = {
            "lift": 1.0 if low_confidence else round(raw_lift, 3),
            "raw_lift": round(raw_lift, 3),
            "low_confidence": low_confidence,
            "pct_winners": round(pct_w * 100, 1),
            "pct_normals": round(pct_n * 100, 1),
            "winner_count": cnt_w,
            "normal_count": cnt_n,
        }
    return result


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    posts_path = DATA_DIR / "posts.json"
    if not posts_path.exists():
        sys.exit("❌  data/posts.json not found — run scrape_posts.py first")

    all_posts = json.loads(posts_path.read_text())
    originals = [p for p in all_posts if not p["is_repost"]]

    winners, normals = classify(originals)
    to_tag = winners + normals

    n_batches = math.ceil(len(to_tag) / BATCH_SIZE)
    # Cost estimate
    input_tok = n_batches * (BATCH_SIZE * 300 + 200)
    output_tok = n_batches * BATCH_SIZE * 50
    cost = input_tok / 1_000_000 * INPUT_PRICE_PER_M + output_tok / 1_000_000 * OUTPUT_PRICE_PER_M
    print(f"📊 {len(originals)} originals | {len(winners)} winners | {len(normals)} normals")
    print(f"💰 Estimated cost: {n_batches} batches → ~{input_tok:,} input + {output_tok:,} output tokens ≈ ${cost:.3f}")
    print()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    print(f"🤖 Tagging {len(to_tag)} posts ({MODEL})…")
    failed = 0
    for i in range(0, len(to_tag), BATCH_SIZE):
        batch = to_tag[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"   batch {batch_num}/{n_batches}…", end=" ", flush=True)
        try:
            tags_list = tag_batch(client, batch)
            if abs(len(tags_list) - len(batch)) > 3:
                raise ValueError(f"expected ~{len(batch)} tags, got {len(tags_list)}")
            for post, tags in zip(batch, tags_list):  # zip stops at shorter
                post["tags"] = {k: v for k, v in tags.items() if k != "i"}
            print("✓")
        except Exception as exc:
            print(f"⚠️  {exc}")
            failed += 1
            for post in batch:
                post["tags"] = {}

    tagged_winners = [p for p in to_tag if p["label"] == "winner"]
    tagged_normals = [p for p in to_tag if p["label"] == "normal"]

    print(f"\n📈 Computing lift…")
    lift = compute_lift(tagged_winners, tagged_normals)
    sorted_lift = sorted(lift.items(), key=lambda x: -x[1]["lift"])

    output = {
        "winners": len(tagged_winners),
        "normals": len(tagged_normals),
        "model": MODEL,
        "features": {k: v for k, v in sorted_lift},
    }
    out_path = DATA_DIR / "lift.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"💾 Saved → {out_path}")

    # ── print summary ──
    print(f"\n{'─'*60}")
    print(f"Winners: {len(tagged_winners)}   Normals: {len(tagged_normals)}")
    if failed:
        print(f"⚠️  {failed} batch(es) failed — partial results")
    print(f"{'─'*60}")

    print("\nTop 10 features by lift:")
    for feat, data in sorted_lift[:10]:
        lift_val = data["lift"]
        print(f"  {feat:<38}  {lift_val:>6.2f}×   W:{data['pct_winners']:>4.0f}% vs N:{data['pct_normals']:>4.0f}%")

    print("\nBottom 5 features by lift:")
    for feat, data in sorted_lift[-5:]:
        print(f"  {feat:<38}  {data['lift']:>6.2f}×   W:{data['pct_winners']:>4.0f}% vs N:{data['pct_normals']:>4.0f}%")

    print("\n5 example winner first lines:")
    for p in tagged_winners[:5]:
        first_line = p["text"].split("\n")[0][:120]
        print(f"  [{p['author'][:18]:<18}]  {first_line}")


if __name__ == "__main__":
    main()
