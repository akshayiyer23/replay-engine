#!/usr/bin/env python3
"""
scripts/scrape_posts.py — Pull LinkedIn posts for creators in data/creators.txt.

Actor: harvestapi/linkedin-profile-posts  (~$1.50 / 1,000 posts, no cookies)

Usage:
    python3 scripts/scrape_posts.py
    python3 scripts/scrape_posts.py --profiles data/creators.txt --months 12
"""

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apify_client import ApifyClient
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
load_dotenv(ROOT / ".env")

ACTOR_ID = "harvestapi/linkedin-profile-posts"
# Pricing: $0.002 per post = $2.00 / 1,000 posts (pay-per-event model, Nov 2026)


# ── normalisation ─────────────────────────────────────────────────────────────

def infer_post_type(item: dict) -> str:
    """Infer post type from harvestapi/linkedin-profile-posts schema."""
    # Repost: has a reshared post object
    if item.get("resharedPost"):
        return "repost"
    # Video: has a video field or videoUrl
    if item.get("video") or item.get("videoUrl"):
        return "video"
    # Carousel / document
    if item.get("document") or item.get("carousel"):
        return "carousel"
    # Image: postImages array is non-empty
    if item.get("postImages"):
        return "image"
    return "text"


def _parse_date(item: dict) -> datetime | None:
    """Extract post datetime from postedAt field."""
    posted_at = item.get("postedAt")
    if not posted_at:
        return None
    try:
        if isinstance(posted_at, dict):
            # Prefer ISO string; fall back to ms timestamp
            iso = posted_at.get("date")
            ts = posted_at.get("timestamp")
            if iso:
                return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            if ts:
                return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
        if isinstance(posted_at, (int, float)):
            return datetime.fromtimestamp(posted_at / 1000, tz=timezone.utc)
        if isinstance(posted_at, str):
            return datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass
    return None


def normalize(item: dict, author: str, cutoff: datetime) -> dict | None:
    post_dt = _parse_date(item)
    if post_dt is None or post_dt < cutoff:
        return None

    eng = item.get("engagement") or {}
    # reactions = sum of all reaction counts, or fallback to eng.likes
    raw_reactions = eng.get("reactions") or []
    if isinstance(raw_reactions, list):
        total_reactions = sum(r.get("count", 0) for r in raw_reactions) or eng.get("likes", 0)
    else:
        total_reactions = int(raw_reactions or eng.get("likes", 0))

    return {
        "author": author,
        "date": post_dt.strftime("%Y-%m-%d"),
        "text": (item.get("content") or item.get("text") or "").strip(),
        "reactions": int(total_reactions),
        "comments": int(eng.get("comments", 0)),
        "reposts": int(eng.get("shares", 0)),
        "post_type": infer_post_type(item),
        "url": (
            item.get("linkedinUrl")
            or item.get("shareLinkedinUrl")
            or item.get("url")
            or ""
        ),
    }


# ── Apify run ─────────────────────────────────────────────────────────────────

def fetch_profile_posts(client: ApifyClient, profile_url: str, max_posts: int) -> list[dict]:
    if not profile_url.startswith("http"):
        profile_url = "https://www.linkedin.com/in/" + profile_url.split("/in/")[-1].strip("/")

    # Start the run (returns a Run Pydantic model — use attribute access, not dict)
    run = client.actor(ACTOR_ID).start(
        run_input={
            "profileUrls": [profile_url],
            "maxPosts": max_posts,
            "scrapeReactions": False,
            "scrapeComments": False,
            "includeReposts": True,
        },
    )

    run_id = run.id
    terminal = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}

    # Poll until done
    while run.status not in terminal:
        time.sleep(5)
        run = client.run(run_id).get()

    if run.status != "SUCCEEDED":
        raise RuntimeError(f"Actor run {run_id} ended with status: {run.status}")

    return list(client.dataset(run.default_dataset_id).iterate_items())


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape LinkedIn posts via Apify")
    parser.add_argument("--profiles", default=str(DATA_DIR / "creators.txt"))
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument(
        "--max-posts", type=int, default=300,
        help="Max posts to fetch per profile before date filter (default: 300)",
    )
    args = parser.parse_args()

    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        raise SystemExit("❌  APIFY_API_TOKEN not set in .env")

    client = ApifyClient(token)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=args.months * 30)

    profiles = [
        line.strip()
        for line in Path(args.profiles).read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not profiles:
        raise SystemExit("❌  No profiles in creators.txt")

    all_posts: list[dict] = []

    # One raw-cache file per profile to avoid re-billing on reruns
    cache_dir = DATA_DIR / ".post_cache"
    cache_dir.mkdir(exist_ok=True)

    for profile in profiles:
        author = profile.split("/in/")[-1].strip("/")
        cache_file = cache_dir / f"{author}.json"

        if cache_file.exists():
            print(f"📋 Loading cached raw posts for {author}…")
            raw = json.loads(cache_file.read_text())
        else:
            print(f"🔍 Fetching posts for {author}  (actor: {ACTOR_ID})…")
            raw = fetch_profile_posts(client, profile, max_posts=args.max_posts)
            cache_file.write_text(json.dumps(raw, indent=2, default=str))

        print(f"   raw items returned: {len(raw)}")

        kept = [p for item in raw if (p := normalize(item, author, cutoff))]
        kept.sort(key=lambda p: p["date"], reverse=True)
        print(f"   kept after {args.months}-month filter: {len(kept)}")

        if kept:
            types = {}
            for p in kept:
                types[p["post_type"]] = types.get(p["post_type"], 0) + 1
            print(f"   types: {types}")

        all_posts.extend(kept)

    out = DATA_DIR / "posts.json"
    out.write_text(json.dumps(all_posts, indent=2))
    print(f"\n✅  Saved {len(all_posts)} posts → {out}")

    if all_posts:
        sample = all_posts[0]
        print(f"\nSample (most recent):")
        print(f"  date:      {sample['date']}")
        print(f"  type:      {sample['post_type']}")
        print(f"  reactions: {sample['reactions']}")
        print(f"  comments:  {sample['comments']}")
        print(f"  reposts:   {sample['reposts']}")
        print(f"  url:       {sample['url']}")
        print(f"  text:      {sample['text'][:120]}…")


if __name__ == "__main__":
    main()
