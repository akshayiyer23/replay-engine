#!/usr/bin/env python3
"""
scripts/generate_content.py — Generate LinkedIn content for top scored moments.

For each moment with score ≥ 45 (max 6):
  - LinkedIn post  (first line ≤ 8 words, hook patterns from hooks.md)
  - Carousel outline (6–8 slides, one line each)
  - Unify University lesson (title, 3 takeaways, 1 exercise)
  - Caption text (first 3 seconds on-screen)

Re-scores each post's first line against the rubric and prints before → after.

Usage:
    python3 scripts/generate_content.py data/<slug>-candidates.json
"""

import json
import os
import re
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
JUDGE_DIR = ROOT / "skills" / "gtm-viral-judge"
load_dotenv(ROOT / ".env")

MODEL = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"
SCORE_THRESHOLD = 45
MAX_MOMENTS = 6

# ── prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_BASE = """\
You are a GTM content writer turning webinar clips into LinkedIn content.
Write like a sharp practitioner sharing real insight — no corporate tone, no marketing speak.

## Winning hook patterns (high-performing GTM post openers)
{hooks}

{pov_block}

## Pronouns — HARD RULE
Never use gendered pronouns. Never write he, she, his, her, him.
Use the person's name on first reference, then rewrite around it ("the approach", "that answer").

## Sensitive claims — HARD RULE
Never name a real company, school, or person in a negative, legal, or compliance context.
If the transcript says a named org "was breaking the law" or "violated" something,
anonymize: write "a large school district" / "one company" instead, or drop the detail entirely.
If the moment can't stand without the sensitive claim, skip it and return a minimal placeholder.

## Grounding — HARD RULE
Every factual claim (not just numbers) must be directly in the transcript — no inference,
no extrapolation. If a claim isn't supported by the transcript, remove it from the post.
Numbers must preserve qualifiers exactly: "close to 30", "around 40%" — not "30" or "40%".

## No feature pitches — HARD RULE
Never describe Unify's product capabilities in a sales context.
Wrong: "Unify has 40 to 50 data providers." "Unify connects every data point and signals."
Right: Mention Unify by name as context ("a Unify workflow", "inside Unify") — never sell it.

## Formatting rules
- LinkedIn post first line: ≤ 8 words. Never start with "I". Direct. No question marks.
- STAT openers preferred: if the transcript has a specific number (accounts, meetings, days,
  dollars, percent), LEAD WITH IT as a stat hook. Fall back to contrarian/story only if no
  strong number exists.
- Line 2 (second paragraph, 1 sentence): open loop — tease the insight without delivering it.
  No "Here's what I mean." Create curiosity. No payoff yet.
- Short paragraphs (1–3 sentences). Blank line between paragraphs. Plain text only.
- Zero explicit pitches: never "sign up", "try X", "book a demo", "check out".
- End every post with one short question (≤ 10 words) that invites a real comment.
- Written prose, not spoken transcript: cut all filler ("and so", "you know", "right?",
  "I mean", "sort of", "pretty much", "essentially", "obviously").
- Carousel: each slide = one punchy line (8–12 words). No bullets inside a slide.
- Caption: 3–6 words max for the on-screen text that appears first 3 seconds.
{voice_block}\
"""

_POV_THIRD_BLOCK = """\
## Point of view: Unify company page post — HARD RULE
Write in THIRD PERSON crediting the speaker by full name on first reference.
Speaker: {speaker_name}
Example opener: "{speaker_name} spent 10 hours a month just to prioritize accounts."
NEVER use I, me, my, myself. Using "we" or "our" for Unify the company is fine.
Never use the speaker's name in a first-person sentence structure.
"""

_POV_FIRST_BLOCK = """\
## Point of view: {post_for}'s personal LinkedIn post — HARD RULE
Write in FIRST PERSON — this is {post_for} speaking directly to their audience.
NEVER refer to {post_for} in third person ("{last_name}'s answer", "{last_name} says",
"their approach"). Use "I", "we", "my", "our" throughout. Write as if {post_for} is talking.
"""

_VOICE_BLOCK = """\

## Write in this voice (Ethan Dursht's post style)
{examples}
"""

_GEN_USER = """\
Generate content for this GTM clip:

{candidate_json}

Return ONE JSON object with exactly these keys — no prose outside the JSON:
{{
  "linkedin_post": "<full post, use \\n for line breaks>",
  "carousel": ["Slide 1: ...", "Slide 2: ...", "...6-8 slides total"],
  "lesson": {{
    "title": "<lesson title>",
    "takeaways": ["<takeaway 1>", "<takeaway 2>", "<takeaway 3>"],
    "exercise": "<one actionable exercise for the reader>"
  }},
  "caption": "<3-6 words for on-screen text>"
}}\
"""

_TAG_USER = """\
Tag this LinkedIn opening line. Return only valid JSON, no prose.
Line: "{line}"
Schema: {{"hook_type": "contrarian"|"stat"|"story"|"how_to"|"prediction"|"other", "first_line_words": <integer>}}\
"""


# ── QA accumulator ───────────────────────────────────────────────────────────

_QA_ENTRIES: list[dict] = []


def get_and_clear_qa() -> list[dict]:
    global _QA_ENTRIES
    entries = _QA_ENTRIES[:]
    _QA_ENTRIES.clear()
    return entries


# ── editor pass ───────────────────────────────────────────────────────────────

_PITCH_RE = re.compile(
    r'\b(sign\s+up|book\s+a\s+demo|try\s+unify|check\s+out|get\s+started|click\s+here)\b',
    re.I,
)
_FILLER_RE = re.compile(
    r'\b(and so,?|you know,?|right\?|i mean,?|sort of,?|pretty much|essentially,?'
    r'|obviously,?|basically,?)\b',
    re.I,
)
_POV_FIRST_RE = re.compile(r'\bI\b|\bme\b|\bmy\b|\bmyself\b', re.I)
_SPEAKER_LABEL_RE = re.compile(r'\b(Guest|Host)\b')  # case-sensitive: flags capitalized label usage
_GENDER_RE = re.compile(r'\b(he|she|his|her|him)\b', re.I)
_SENSITIVE_RE = re.compile(
    r'\b(breaking the law|violat\w+ (?:the )?law|unintentionally breaking|discriminat\w+)\b',
    re.I,
)
_UNIFY_PITCH_RE = re.compile(
    r'\bUnify has\b|\d+\s*(?:to\s*\d+\s*)?\+?\s*data providers?\b',
    re.I,
)

_REWRITE_USER = """\
This LinkedIn post needs editing. Fix ALL issues listed below:
{issues}

Original post:
{post}

Context (angle and transcript excerpt):
{context}

Point of view rule:
{pov_rule}

Rules: hook ≤ 8 words; no product pitch; no filler phrases; ≤ 1,300 chars total; \
end with exactly one question (≤ 10 words); no gendered pronouns (he/she/his/her/him); \
no sensitive claims about real named entities in legal/compliance/negative context.
Return ONLY the rewritten post text — no JSON, no preamble.\
"""


def _editor_check(post: str, post_for: str = "", speaker_name: str = "") -> list[str]:
    """Return list of issue strings. Empty list = passes all checks."""
    issues = []
    lines = [ln for ln in post.split("\n") if ln.strip()]
    if lines:
        n_w = len(lines[0].split())
        if n_w > 8:
            issues.append(f"hook too long: {n_w} words (max 8)")
    m = _PITCH_RE.search(post)
    if m:
        issues.append(f"product pitch: '{m.group()}'")
    m = _FILLER_RE.search(post)
    if m:
        issues.append(f"filler phrase: '{m.group()}'")
    if len(post) > 1300:
        issues.append(f"too long: {len(post)} chars (max 1,300)")
    last = next((ln for ln in reversed(post.split("\n")) if ln.strip()), "")
    if not last.strip().endswith("?"):
        issues.append(f"doesn't end with a question (last: '…{last.strip()[-30:]}')")

    # POV: Unify company page → must be third person (no I/me/my)
    if "unify company page" in post_for.lower():
        m = _POV_FIRST_RE.search(post)
        if m:
            issues.append(f"POV: first-person '{m.group()}' on Unify company page (must be third person)")

    # POV: speaker's own page → must NOT use speaker name in third-person
    elif speaker_name and post_for and post_for.lower() not in ("", "unify company page"):
        sp_last = speaker_name.split()[-1]
        third_person_re = re.compile(
            rf"\b{re.escape(sp_last)}'s (?:answer|approach|take|recommendation|strategy|method)\b"
            rf"|\b{re.escape(sp_last)}\s+(?:says|explains|notes|learned|shared|recommends|believes|thinks)\b",
            re.I,
        )
        m = third_person_re.search(post)
        if m:
            issues.append(f"POV: '{m.group()}' — speaker referred to in third person on own account")

    # Gendered pronouns
    m = _GENDER_RE.search(post)
    if m:
        issues.append(f"gendered pronoun: '{m.group()}' — use name instead")

    # Sensitive claims
    m = _SENSITIVE_RE.search(post)
    if m:
        issues.append(f"sensitive claim: '{m.group()}' — anonymize or drop")

    # Unify feature pitch
    m = _UNIFY_PITCH_RE.search(post)
    if m:
        issues.append(f"Unify feature pitch: '{m.group()[:60]}' — remove or reframe")

    # Never use "Guest" or "Host" as speaker attribution labels
    m = _SPEAKER_LABEL_RE.search(post)
    if m:
        issues.append(f"speaker labeled as '{m.group()}' — use their actual name or rewrite")

    return issues


def _editor_rewrite(post: str, issues: list[str], context: str,
                    post_for: str, speaker_name: str,
                    client: anthropic.Anthropic) -> str:
    if "unify company page" in post_for.lower():
        pov_rule = f"Write in THIRD PERSON. Speaker is {speaker_name}. Never use 'I', 'me', 'my'. Use speaker's full name or 'Unify'."
    else:
        last_name = speaker_name.split()[-1] if speaker_name else post_for
        pov_rule = f"Write in FIRST PERSON as {post_for}. Never refer to them as '{last_name}' in third person."

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "low"}},
        messages=[{"role": "user", "content": _REWRITE_USER.format(
            issues="\n".join(f"- {i}" for i in issues),
            post=post,
            context=context[:500],
            pov_rule=pov_rule,
        )}],
    )
    tb = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
    return tb.text.strip() if tb else post


# ── helpers ───────────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text.strip())
    return text.strip()


def _ethan_examples(posts_path: Path, n: int = 4) -> str:
    posts = json.loads(posts_path.read_text())
    ethan = sorted(
        [p for p in posts if p["author"] == "ethandursht" and not p["is_repost"]],
        key=lambda p: -p["reactions"],
    )
    return "\n\n".join(f"---\n{p['text'][:400].strip()}\n---" for p in ethan[:n])


def _build_system(hooks_md: str, post_for: str, speaker_name: str = "") -> str:
    # Build POV block based on who the post is for
    if "unify company page" in (post_for or "").lower():
        pov = _POV_THIRD_BLOCK.format(speaker_name=speaker_name or "the speaker")
    else:
        last_name = (post_for or "").split()[-1] if post_for else "the speaker"
        pov = _POV_FIRST_BLOCK.format(post_for=post_for or "the speaker", last_name=last_name)

    voice = ""
    if "ethan" in (post_for or "").lower():
        posts_path = DATA_DIR / "posts.json"
        if posts_path.exists():
            voice = _VOICE_BLOCK.format(examples=_ethan_examples(posts_path))

    return _SYSTEM_BASE.format(hooks=hooks_md.strip(), pov_block=pov, voice_block=voice)


def _call_sonnet(system: str, user: str, client: anthropic.Anthropic) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "low"}},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    block = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
    if block is None:
        raise ValueError(f"No text block (blocks: {[getattr(b,'type','?') for b in resp.content]})")
    return block.text


def _tag_line(line: str, client: anthropic.Anthropic) -> dict:
    resp = client.messages.create(
        model=HAIKU,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        extra_body={"output_config": {"effort": "low"}},
        messages=[{"role": "user", "content": _TAG_USER.format(line=line[:200])}],
    )
    text_block = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
    if text_block is None:
        raise ValueError(f"No text block from haiku (blocks: {[getattr(b,'type','?') for b in resp.content]})")
    raw = _strip_fences(text_block.text)
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


# ── main logic ────────────────────────────────────────────────────────────────

def generate_for_candidates(
    candidates: list[dict],
    slug: str,
    out_dir: Path | None = None,
) -> list[dict]:
    if out_dir is None:
        out_dir = DATA_DIR

    # Use the judged top set (top8=True); fall back to score-based if no top8 flags present
    top8_set = [c for c in candidates if c.get("top8", False)]
    if top8_set:
        qualifying = sorted(top8_set, key=lambda c: -c.get("score", 0))[:MAX_MOMENTS]
    else:
        qualifying = sorted(
            [c for c in candidates if c.get("score", 0) >= SCORE_THRESHOLD],
            key=lambda c: -c["score"],
        )[:MAX_MOMENTS]

    if not qualifying:
        print("💡 No moments in judged top set — content generation skipped")
        return []

    print(f"💡 Generating content for {len(qualifying)} moments (judged top set)…")

    hooks_md = (JUDGE_DIR / "hooks.md").read_text()
    rubric = json.loads((JUDGE_DIR / "rubric.json").read_text())
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    results = []
    for c in qualifying:
        cid = c["id"]
        post_for = c.get("post_for", "")
        print(f"   {cid} (score={c['score']}, {c['moment_type']}, for={post_for})…", end=" ", flush=True)

        # ── 1. tag original angle (before) ──
        angle_line = c.get("angle", "").split("\n")[0].strip()
        try:
            angle_tag = _tag_line(angle_line, client)
        except Exception:
            angle_tag = {
                "hook_type": "other",
                "first_line_words": len(angle_line.split()),
            }

        # ── 2. generate content ──
        speaker_name = c.get("speaker_name", "")
        system = _build_system(hooks_md, post_for, speaker_name)
        slim = {
            "id": cid,
            "angle": c.get("angle", ""),
            "moment_type": c["moment_type"],
            "speaker_name": c["speaker_name"],
            "post_for": post_for,
            "transcript": c["transcript"][:900],
            "judge_score": c.get("score", 0),
            "trimmed": c.get("trimmed", False),
        }
        try:
            raw = _call_sonnet(system, _GEN_USER.format(candidate_json=json.dumps(slim, indent=2)), client)
            content = json.loads(_strip_fences(raw))
        except Exception as exc:
            print(f"⚠️  {exc}")
            continue

        # ── 3. tag generated post first line (after) ──
        post_first_line = content["linkedin_post"].split("\n")[0].strip()
        try:
            post_tag = _tag_line(post_first_line, client)
        except Exception:
            post_tag = {
                "hook_type": "other",
                "first_line_words": len(post_first_line.split()),
            }

        # ── 4. editor pass (check + one rewrite if needed) ──
        post = content["linkedin_post"]
        issues = _editor_check(post, post_for, speaker_name)
        editor_fixed = False
        if issues:
            print(f"\n      ✍️  editor rewrites ({len(issues)} issue(s)): {issues[0][:60]}…", end=" ", flush=True)
            post = _editor_rewrite(
                post, issues,
                f"Angle: {c.get('angle','')}\nTranscript: {c.get('transcript','')[:300]}",
                post_for, speaker_name,
                client,
            )
            content["linkedin_post"] = post
            remaining = _editor_check(post, post_for, speaker_name)
            editor_fixed = len(remaining) == 0
            _QA_ENTRIES.append({
                "gate": "editor", "item_id": cid,
                "passed": editor_fixed,
                "details": f"issues={issues}; remaining={remaining}",
                "fixed": editor_fixed,
            })
            # Re-tag first line after rewrite
            post_first_line = post.split("\n")[0].strip()
            try:
                post_tag = _tag_line(post_first_line, client)
            except Exception:
                pass
            print("✓" if editor_fixed else f"⚠️ still: {remaining}")
        else:
            _QA_ENTRIES.append({
                "gate": "editor", "item_id": cid,
                "passed": True, "details": "all checks passed", "fixed": False,
            })

        # ── 5. before → after hook score (displayed max 95) ──
        post_first_line = content["linkedin_post"].split("\n")[0].strip()
        try:
            post_tag = _tag_line(post_first_line, client)
        except Exception:
            pass  # keep existing post_tag
        before_hook_w = _hook_weight(angle_tag, rubric)
        after_hook_w = _hook_weight(post_tag, rubric)
        score_before = min(95, c.get("score", 0))
        score_after = max(0, min(95, score_before - before_hook_w + after_hook_w))

        if not issues:
            print(f"hook {score_before}→{score_after} [{post_tag.get('hook_type','?')}] ✓")

        results.append({
            "id": cid,
            "moment_type": c["moment_type"],
            "speaker_name": c["speaker_name"],
            "post_for": post_for,
            "angle": c.get("angle", ""),
            "judge_score": score_before,
            "angle_tag": angle_tag,
            "post_first_line": post_first_line,
            "post_tag": post_tag,
            "hook_score_before": score_before,
            "hook_score_after": score_after,
            "linkedin_post": content["linkedin_post"],
            "carousel": content["carousel"],
            "lesson": content["lesson"],
            "caption": content["caption"],
        })

    # ── save ──
    json_path = out_dir / f"{slug}-content.json"
    json_path.write_text(json.dumps(results, indent=2))

    md_lines = [f"# {slug} — Generated Content\n"]
    for r in results:
        md_lines.append(
            f"## {r['id']} · score {r['judge_score']} · {r['moment_type']}"
        )
        md_lines.append(
            f"**Post for:** {r['post_for']}  "
            f"**Hook score:** {r['hook_score_before']} → {r['hook_score_after']} "
            f"[{r['post_tag'].get('hook_type','')}]\n"
        )
        md_lines.append(f"### LinkedIn Post\n```\n{r['linkedin_post']}\n```\n")
        md_lines.append(
            "### Carousel\n"
            + "\n".join(f"- {s}" for s in r["carousel"])
            + "\n"
        )
        lesson = r["lesson"]
        md_lines.append(f"### Unify University — {lesson['title']}")
        for t in lesson["takeaways"]:
            md_lines.append(f"- {t}")
        md_lines.append(f"\n**Exercise:** {lesson['exercise']}\n")
        md_lines.append(f"### Caption\n`{r['caption']}`\n")
        md_lines.append("---\n")

    md_path = out_dir / f"{slug}-content.md"
    md_path.write_text("\n".join(md_lines))

    print(f"💾 Saved → {json_path}")
    print(f"💾 Saved → {md_path}")
    return results


# ── pipeline-facing wrapper ───────────────────────────────────────────────────

def print_content_summary(results: list[dict]) -> None:
    if not results:
        return

    # ── top 6 plain list ──
    print(f"\n{'─'*62}")
    print(f"{'ID':<8} {'Score':>5}  {'Speaker':<22} {'Type':<12}  Post first line")
    print(f"{'─'*62}")
    for r in results[:6]:
        speaker = (r.get("speaker_name") or "?")[:22]
        mtype = (r.get("moment_type") or "?")[:12]
        score = r.get("judge_score", 0)
        first = r.get("post_first_line", "")[:45]
        lc = " [lower-conf]" if r.get("lower_confidence") else ""
        print(f"{r['id']:<8} {score:>5}  {speaker:<22} {mtype:<12}  \"{first}\"{lc}")
    print(f"{'─'*62}")

    # ── full post for the highest-scoring moment ──
    top = results[0] if results else None
    if top:
        print(f"\n{top['id']} — full LinkedIn post (for {top['post_for']}):\n")
        print(top["linkedin_post"])


# ── standalone entry point ────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python3 scripts/generate_content.py <candidates.json>")

    cand_path = Path(sys.argv[1])
    if not cand_path.exists():
        sys.exit(f"❌  File not found: {cand_path}")

    candidates = json.loads(cand_path.read_text())
    slug = cand_path.stem.removesuffix("-candidates")

    results = generate_for_candidates(candidates, slug)
    print_content_summary(results)


if __name__ == "__main__":
    main()
