#!/usr/bin/env python3
"""
scripts/build_rubric.py — Generate skills/gtm-viral-judge/rubric.json and hooks.md
from data/lift.json and data/posts.json.

Re-run whenever lift.json is updated with fresh post data.
"""

import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
SKILL_DIR = ROOT / "skills" / "gtm-viral-judge"

SCALE = 25.0
WEIGHT_CAP = 15
MIN_POSTS = 20
WINNER_THRESHOLD = 3.0
TOP_HOOKS = 20

CLIP_FEATURES = {
    "hook_type",
    "hook_length",
    "topic",
    "has_specific_number",
    "names_a_tool_or_company",
    "wc_bucket",
    "post_type",
    "announcement_milestone",
    "salary_money_topic",
    "personal_story",
}


def lift_to_weight(lift: float) -> int:
    return max(-WEIGHT_CAP, min(WEIGHT_CAP, round((lift - 1.0) * SCALE)))


def build_rubric(lift_data: dict) -> dict:
    features_raw = lift_data["features"]

    # Group into categories
    hook_type_vals: dict = {}
    hook_length_vals: dict = {}
    topic_vals: dict = {}
    post_type_vals: dict = {}
    booleans: dict = {}
    wc_vals: dict = {}

    for feat, stats in features_raw.items():
        if stats["low_confidence"]:
            continue
        lift = stats["lift"]
        raw_lift = stats["raw_lift"]
        weight = lift_to_weight(lift)
        entry = {"weight": weight, "lift": raw_lift,
                 "pct_winners": stats["pct_winners"],
                 "pct_normals": stats["pct_normals"]}

        if feat.startswith("hook_type:"):
            hook_type_vals[feat.split(":", 1)[1]] = entry
        elif feat.startswith("hook_"):  # hook_1-5w, hook_6-10w, hook_11+w
            hook_length_vals[feat.replace("hook_", "")] = entry
        elif feat.startswith("topic:"):
            topic_vals[feat.split(":", 1)[1]] = entry
        elif feat.startswith("wc_bucket:"):
            wc_vals[feat.split(":", 1)[1]] = entry
        elif feat.startswith("post_type:"):
            post_type_vals[feat.split(":", 1)[1]] = entry
        elif ":" not in feat:
            # Boolean features: has_specific_number, names_a_tool_or_company,
            # announcement_milestone, salary_money_topic, personal_story, etc.
            booleans[feat] = {"weight_true": weight, "weight_false": 0,
                              "lift": raw_lift,
                              "pct_winners": stats["pct_winners"],
                              "pct_normals": stats["pct_normals"]}

    categorical_features = [
        {
            "name": "hook_type",
            "description": "Style of the opening sentence/hook",
            "type": "categorical",
            "values": dict(sorted(hook_type_vals.items(), key=lambda x: -x[1]["weight"])),
        },
        {
            "name": "hook_length",
            "description": "Word count of the very first sentence",
            "type": "categorical",
            "values": dict(sorted(hook_length_vals.items(), key=lambda x: -x[1]["weight"])),
        },
        {
            "name": "topic",
            "description": "Primary subject of the clip",
            "type": "categorical",
            "values": dict(sorted(topic_vals.items(), key=lambda x: -x[1]["weight"])),
        },
        {
            "name": "word_count",
            "description": "Post/transcript word count bucket (short <100, medium 100-300, long 300+)",
            "type": "categorical",
            "values": dict(sorted(wc_vals.items(), key=lambda x: -x[1]["weight"])),
        },
    ]
    if post_type_vals:
        categorical_features.append({
            "name": "post_type",
            "description": "LinkedIn post format (video/image/text)",
            "type": "categorical",
            "values": dict(sorted(post_type_vals.items(), key=lambda x: -x[1]["weight"])),
        })

    boolean_features = [
        {
            "name": feat,
            "description": feat.replace("_", " ").capitalize(),
            "type": "boolean",
            **vals,
        }
        for feat, vals in sorted(booleans.items(), key=lambda x: -x[1]["weight_true"])
    ]

    rubric = {
        "version": "1.1",
        "generated_from": "data/lift.json",
        "note": "score = 50 + sum(weights for features present), clamped 0-100.",
        "base_score": 50,
        "anti_pattern_penalty_each": 8,
        "anti_pattern_penalty_max": 15,
        "features": categorical_features + boolean_features,
    }
    return rubric


def build_hooks_md(posts_path: Path) -> str:
    posts = json.loads(posts_path.read_text())
    originals = [p for p in posts if not p["is_repost"]]

    by_author: dict[str, list] = defaultdict(list)
    for p in originals:
        by_author[p["author"]].append(p)
    qualifying = {a for a, ps in by_author.items() if len(ps) >= MIN_POSTS}

    winners = []
    for p in originals:
        if p["author"] not in qualifying:
            continue
        med = statistics.median(pp["reactions"] for pp in by_author[p["author"]])
        if med == 0:
            continue
        score = p["reactions"] / med
        if score >= WINNER_THRESHOLD:
            winners.append({**p, "outlier_score": score})

    winners.sort(key=lambda x: -x["outlier_score"])

    # Deduplicate by first line, keep best score
    seen: set[str] = set()
    deduped = []
    for w in winners:
        fl = w["text"].split("\n")[0].strip()
        if fl not in seen:
            seen.add(fl)
            deduped.append(w)
        if len(deduped) >= TOP_HOOKS:
            break

    lines = [
        "# hooks.md — 20 Best Winner First Lines",
        "",
        "These are the 20 highest-performing opening lines from GTM LinkedIn posts "
        "(≥3× above author median). Use them as style examples when judging candidates.",
        "",
    ]
    for i, w in enumerate(deduped, 1):
        fl = w["text"].split("\n")[0].strip()
        lines.append(f"{i:>2}. [{w['outlier_score']:.1f}×] {fl}")

    return "\n".join(lines) + "\n"


def rubric_to_table(rubric: dict) -> str:
    """Format rubric as a scoring table for embedding in SKILL.md / prompts."""
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
            rows.append(f"  - false: 0 pts")
        rows.append("")
    rows.append(
        f"**Anti-pattern penalty**: −{rubric['anti_pattern_penalty_each']} pts each, "
        f"−{rubric['anti_pattern_penalty_max']} pts max "
        "(see anti-patterns.md for the list)."
    )
    return "\n".join(rows)


def main() -> None:
    SKILL_DIR.mkdir(parents=True, exist_ok=True)

    lift_data = json.loads((DATA_DIR / "lift.json").read_text())
    rubric = build_rubric(lift_data)

    rubric_path = SKILL_DIR / "rubric.json"
    rubric_path.write_text(json.dumps(rubric, indent=2))
    print(f"✅  rubric.json → {rubric_path}")

    hooks_md = build_hooks_md(DATA_DIR / "posts.json")
    hooks_path = SKILL_DIR / "hooks.md"
    hooks_path.write_text(hooks_md)
    print(f"✅  hooks.md ({TOP_HOOKS} examples) → {hooks_path}")

    table = rubric_to_table(rubric)
    print("\nScoring table preview:\n")
    print(table)


if __name__ == "__main__":
    main()
