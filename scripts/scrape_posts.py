#!/usr/bin/env python3
"""
scripts/scrape_posts.py — Pull LinkedIn posts for creators and company pages.

Actors used:
  profiles  : harvestapi/linkedin-profile-posts   (~$2.00 / 1,000 posts)
  companies : harvestapi/linkedin-company-posts   (~$2.00 / 1,000 posts)

Usage:
    python3 scripts/scrape_posts.py
    python3 scripts/scrape_posts.py --profiles data/creators.txt \\
                                    --companies data/companies.txt \\
                                    --months 12 --max-posts 300
"""

import argparse
import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os

from apify_client import ApifyClient
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / ".post_cache"
load_dotenv(ROOT / ".env")

PROFILE_ACTOR  = "harvestapi/linkedin-profile-posts"
COMPANY_ACTOR  = "harvestapi/linkedin-company-posts"


# ── type inference ────────────────────────────────────────────────────────────

def infer_post_type(item: dict) -> str:
    """Infer post type from harvestapi schema (audited on Austin Hughes, 300 posts).

    Field presence: repostId(1), postVideo(42), article(1), postImages(210), text(46).
    postVideo and postImages never co-occur.
    """
    if item.get("repostId") or item.get("resharedPost"):
        return "repost"
    if item.get("postVideo"):
        return "video"
    if item.get("article"):
        return "article"
    if item.get("postImages"):
        return "image"
    return "text"


# ── date + normalisation ──────────────────────────────────────────────────────

def _parse_date(item: dict) -> datetime | None:
    posted_at = item.get("postedAt")
    if not posted_at:
        return None
    try:
        if isinstance(posted_at, dict):
            iso = posted_at.get("date")
            ts  = posted_at.get("timestamp")
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


def _int(val) -> int:
    if isinstance(val, dict):
        return int(val.get("count", 0) or sum(v for v in val.values() if isinstance(v, int)))
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def normalize(item: dict, author: str, cutoff: datetime) -> dict | None:
    post_dt = _parse_date(item)
    if post_dt is None or post_dt < cutoff:
        return None

    eng = item.get("engagement") or {}
    raw_reactions = eng.get("reactions") or []
    if isinstance(raw_reactions, list):
        total_reactions = sum(r.get("count", 0) for r in raw_reactions) or eng.get("likes", 0)
    else:
        total_reactions = int(raw_reactions or eng.get("likes", 0))

    post_type = infer_post_type(item)
    is_repost  = post_type == "repost"

    return {
        "author":    author,
        "date":      post_dt.strftime("%Y-%m-%d"),
        "text":      (item.get("content") or item.get("text") or "").strip(),
        "reactions": int(total_reactions),
        "comments":  int(eng.get("comments", 0)),
        "reposts":   int(eng.get("shares", 0)),
        "post_type": post_type,
        "is_repost": is_repost,
        "url": (
            item.get("linkedinUrl")
            or item.get("shareLinkedinUrl")
            or item.get("url")
            or ""
        ),
    }


# ── Apify helpers ─────────────────────────────────────────────────────────────

def _poll_run(client: ApifyClient, run) -> object:
    terminal = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
    while run.status not in terminal:
        time.sleep(5)
        run = client.run(run.id).get()
    return run


def fetch_profile_posts(client: ApifyClient, profile_url: str, max_posts: int) -> list[dict]:
    if not profile_url.startswith("http"):
        profile_url = "https://www.linkedin.com/in/" + profile_url.split("/in/")[-1].strip("/")
    run = client.actor(PROFILE_ACTOR).start(
        run_input={
            "profileUrls":    [profile_url],
            "maxPosts":       max_posts,
            "scrapeReactions": False,
            "scrapeComments":  False,
            "includeReposts":  True,
        },
    )
    run = _poll_run(client, run)
    if run.status != "SUCCEEDED":
        raise RuntimeError(f"Profile actor run {run.id} ended: {run.status}")
    return list(client.dataset(run.default_dataset_id).iterate_items())


def fetch_company_posts(client: ApifyClient, company_url: str, max_posts: int) -> list[dict]:
    if not company_url.startswith("http"):
        company_url = "https://www.linkedin.com/company/" + company_url.split("/company/")[-1].strip("/")
    run = client.actor(COMPANY_ACTOR).start(
        run_input={
            "companyUrls": [company_url],
            "maxPosts":    max_posts,
        },
    )
    run = _poll_run(client, run)
    if run.status != "SUCCEEDED":
        raise RuntimeError(f"Company actor run {run.id} ended: {run.status}")
    return list(client.dataset(run.default_dataset_id).iterate_items())


# ── results table ─────────────────────────────────────────────────────────────

def print_table(all_posts: list[dict]) -> None:
    from collections import defaultdict
    by_author: dict[str, list[dict]] = defaultdict(list)
    for p in all_posts:
        by_author[p["author"]].append(p)

    col = "{:<22} {:>5}  {:>6}  {:>7}  {:>14}"
    print()
    print(col.format("author", "posts", "videos", "reposts", "median react."))
    print("─" * 62)
    for author, posts in sorted(by_author.items(), key=lambda x: -len(x[1])):
        originals = [p for p in posts if not p["is_repost"]]
        videos    = sum(1 for p in originals if p["post_type"] == "video")
        reposts   = sum(1 for p in posts if p["is_repost"])
        med       = statistics.median(p["reactions"] for p in originals) if originals else 0
        print(col.format(author[:22], len(posts), videos, reposts, f"{med:.0f}"))


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles",  default=str(DATA_DIR / "creators.txt"))
    parser.add_argument("--companies", default=str(DATA_DIR / "companies.txt"))
    parser.add_argument("--months",    type=int, default=12)
    parser.add_argument("--max-posts", type=int, default=300)
    args = parser.parse_args()

    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        raise SystemExit("❌  APIFY_API_TOKEN not set in .env")

    client  = ApifyClient(token)
    cutoff  = datetime.now(tz=timezone.utc) - timedelta(days=args.months * 30)
    CACHE_DIR.mkdir(exist_ok=True)

    all_posts: list[dict] = []

    # ── profiles ──
    profiles_path = Path(args.profiles)
    if profiles_path.exists():
        profiles = [
            l.strip() for l in profiles_path.read_text().splitlines()
            if l.strip() and not l.startswith("#")
        ]
        for profile in profiles:
            slug   = profile.split("/in/")[-1].strip("/")
            author = slug
            cache  = CACHE_DIR / f"{slug}.json"

            if cache.exists():
                print(f"📋 {author} — cached")
                raw = json.loads(cache.read_text())
            else:
                print(f"🔍 {author} — fetching…")
                raw = fetch_profile_posts(client, profile, args.max_posts)
                cache.write_text(json.dumps(raw, indent=2, default=str))
                print(f"   {len(raw)} raw posts saved to cache")

            kept = [p for item in raw if (p := normalize(item, author, cutoff))]
            kept.sort(key=lambda p: p["date"], reverse=True)
            all_posts.extend(kept)
            print(f"   kept {len(kept)} posts (last {args.months} mo)")

    # ── company pages ──
    companies_path = Path(args.companies)
    if companies_path.exists():
        for line in companies_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            url_part, _, display_name = line.partition("=")
            url_part     = url_part.strip()
            display_name = display_name.strip() or url_part.split("/")[-1]
            slug         = url_part.split("/company/")[-1].strip("/")
            cache        = CACHE_DIR / f"company_{slug}.json"

            if cache.exists():
                print(f"📋 {display_name} (company) — cached")
                raw = json.loads(cache.read_text())
            else:
                print(f"🔍 {display_name} (company) — fetching…")
                raw = fetch_company_posts(client, url_part, args.max_posts)
                cache.write_text(json.dumps(raw, indent=2, default=str))
                print(f"   {len(raw)} raw posts saved to cache")

            kept = [p for item in raw if (p := normalize(item, display_name, cutoff))]
            kept.sort(key=lambda p: p["date"], reverse=True)
            all_posts.extend(kept)
            print(f"   kept {len(kept)} posts (last {args.months} mo)")

    # ── save ──
    out = DATA_DIR / "posts.json"
    out.write_text(json.dumps(all_posts, indent=2))
    total_orig = sum(1 for p in all_posts if not p["is_repost"])
    total_repost = sum(1 for p in all_posts if p["is_repost"])
    print(f"\n✅  {len(all_posts)} total posts saved → {out}")
    print(f"    {total_orig} original  |  {total_repost} reposts (is_repost=true)")

    print_table(all_posts)


if __name__ == "__main__":
    main()
