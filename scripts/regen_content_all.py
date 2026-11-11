#!/usr/bin/env python3
"""Regenerate golden content for all 11 webinars using updated editor-gate rules.

Reads each *-candidates.golden.json, regenerates posts, writes *-content.golden.json.
Prints before → after for posts that violated rules in How AEs/SDRs.

Usage:
    cd /path/to/replay-engine
    python3 scripts/regen_content_all.py
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

from generate_content import generate_for_candidates  # noqa: E402

SLUGS = [
    "claude-for-revops-how-to-leverage-ai-to-take-revops-from-tac",
    "cold-email-deliverability-the-hidden-infrastructure-behind-o",
    "custom-ai-signals",
    "how-aes-and-sdrs-are-becoming-their-own-go-to-market-enginee",
    "introducing-the-new-unify-ai-for-outbound-sellers",
    "outbound-email-masterclass-webinar",
    "the-outbound-sweet-spot-orchestrating-automation-and-human-t",
    "the-plg-playbook-how-gumloop-and-navattic-are-turning-produc",
    "vibe-coding-for-marketers-how-to-stop-prompting-and-start-bu",
    "vibe-coding-for-sales-webinar-what-leaders-are-actually-buil",
    "winning-on-linkedin-in-2026-founder-led-content-pipeline-gro",
]

HOW_AES_SLUG = "how-aes-and-sdrs-are-becoming-their-own-go-to-market-enginee"
# Post IDs known to violate the 5 rules in How AEs/SDRs
VIOLATED_IDS = {"m0935", "m1156", "m1064", "m1307", "m2003"}


def _load_posts_by_id(golden_json: Path) -> dict[str, str]:
    if not golden_json.exists():
        return {}
    return {item["id"]: item["linkedin_post"] for item in json.loads(golden_json.read_text())}


def main() -> None:
    # 1. Snapshot "before" state for How AEs/SDRs violated posts
    before = _load_posts_by_id(DATA_DIR / f"{HOW_AES_SLUG}-content.golden.json")
    before = {k: v for k, v in before.items() if k in VIOLATED_IDS}

    # 2. Regenerate all 11 webinars
    for slug in SLUGS:
        golden_cand = DATA_DIR / f"{slug}-candidates.golden.json"
        if not golden_cand.exists():
            print(f"\n❌ {slug}: no candidates.golden.json — skip")
            continue

        candidates = json.loads(golden_cand.read_text())
        print(f"\n{'='*72}")
        print(f"🔄  {slug}")
        print(f"{'='*72}")

        results = generate_for_candidates(candidates, slug, out_dir=DATA_DIR)
        if not results:
            print(f"⚠️  No results generated")
            continue

        # Lock → golden
        src_json = DATA_DIR / f"{slug}-content.json"
        dst_json = DATA_DIR / f"{slug}-content.golden.json"
        if src_json.exists():
            shutil.copy2(src_json, dst_json)
            print(f"🔒  Locked → {dst_json.name}")

    # 3. Print before → after for How AEs/SDRs posts that violated rules
    after = _load_posts_by_id(DATA_DIR / f"{HOW_AES_SLUG}-content.golden.json")
    after = {k: v for k, v in after.items() if k in VIOLATED_IDS}

    print(f"\n\n{'='*72}")
    print("BEFORE → AFTER: How AEs/SDRs posts that violated rules")
    print(f"{'='*72}")

    for mid in sorted(VIOLATED_IDS):
        b = before.get(mid)
        a = after.get(mid)
        if b is None and a is None:
            continue
        print(f"\n{'─'*72}")
        print(f"Post ID: {mid}")
        print(f"{'─'*72}")
        if b:
            print("BEFORE:")
            print(b)
        else:
            print("BEFORE: (no previous golden)")
        print()
        if a:
            print("AFTER:")
            print(a)
        else:
            print("AFTER: (not generated)")

    print(f"\n{'='*72}")
    print("Done.")


if __name__ == "__main__":
    main()
