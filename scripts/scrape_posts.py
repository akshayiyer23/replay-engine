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


# ── normalisation ─────────────────────────────────────────────────────────────

def infer_post_type(item: dict) -> str:
    t = (item.get("type") or item.get("postType") or "").lower()
    if "video" in t:
        return "video"
    if "article" in t:
        return "article"
    if "document" in t or "carousel" in t:
        return "carousel"
    if "image" in t or item.get("images"):
        return "image"
    if "share" in t or item.get("resharedPost") or item.get("isRepost"):
        return "repost"
    return "text"


def _int(val) -> int:
    if isinstance(val, dict):
        return int(val.get("count", 0) or sum(v for v in val.values() if isinstance(v, int)))
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def normalize(item: dict, author: str, cutoff: datetime) -> dict | None:
    raw_date = (
        item.get("publishedAt")
        or item.get("date")
        or item.get("postedAt")
        or item.get("timestamp")
    )
    if not raw_date:
        return None

    try:
        if isinstance(raw_date, (int, float)):
            post_dt = datetime.fromtimestamp(raw_date / 1000, tz=timezone.utc)
        else:
            post_dt = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

    if post_dt < cutoff:
        return None

    return {
        "author": author,
        "date": post_dt.strftime("%Y-%m-%d"),
        "text": (item.get("text") or item.get("content") or "").strip(),
        "reactions": _int(item.get("reactionsCount") or item.get("reactions")),
        "comments": _int(item.get("commentsCount") or item.get("comments")),
        "reposts": _int(
            item.get("repostsCount")
            or item.get("reposts")
            or item.get("sharesCount")
        ),
        "post_type": infer_post_type(item),
        "url": (
            item.get("url")
            or item.get("postUrl")
            or item.get("link")
            or ""
        ),
    }


# ── Apify run ─────────────────────────────────────────────────────────────────

def fetch_profile_posts(client: ApifyClient, profile_url: str, max_posts: int) -> list[dict]:
    if not profile_url.startswith("http"):
        profile_url = "https://www.linkedin.com/in/" + profile_url.split("/in/")[-1].strip("/")

    run = client.actor(ACTOR_ID).call(
        run_input={
            "profileUrls": [profile_url],
            "maxPosts": max_posts,
            "scrapeReactions": False,
            "scrapeComments": False,
            "includeReposts": True,
        },
        timeout_secs=300,
    )

    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Actor run ended with status: {run['status']}")

    return list(client.dataset(run["defaultDatasetId"]).iterate_items())


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

    for profile in profiles:
        author = profile.split("/in/")[-1].strip("/")
        print(f"🔍 Fetching posts for {author}  (actor: {ACTOR_ID})…")

        raw = fetch_profile_posts(client, profile, max_posts=args.max_posts)
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
