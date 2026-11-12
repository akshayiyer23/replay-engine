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
_RULES_DIR = ROOT / "skills" / "content-rules"
_CONTEXT_DIR = ROOT / "skills" / "unify-moment-miner"
load_dotenv(ROOT / ".env")

# ── load single-source-of-truth content rules ─────────────────────────────────
_CONTENT_RULES_PATH = _RULES_DIR / "CONTENT_RULES.md"
_CONTENT_RULES: str = _CONTENT_RULES_PATH.read_text() if _CONTENT_RULES_PATH.exists() else ""


def _load_known_people() -> frozenset[str]:
    """Return canonical first+last names and first-names-only from unify-context.md.

    Only reads the Speakers section (## 7) and the Guests paragraph to avoid
    matching webinar titles or company names from other sections.
    """
    ctx = _CONTEXT_DIR / "unify-context.md"
    if not ctx.exists():
        return frozenset()
    text = ctx.read_text()
    names: set[str] = set()

    # Extract just the speakers section (## 7. Speakers through the next ##)
    speakers_m = re.search(r'## 7\. Speakers.*?(?=\n## )', text, re.DOTALL)
    speakers_block = speakers_m.group(0) if speakers_m else ""

    # Table rows in speakers section: | Name | Role | URL |
    # Match "Firstname Lastname" and optionally "Firstname (nickname) Lastname"
    for m in re.finditer(
        r'\|\s*([A-Z][a-z]+(?:\([a-z]+\))?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*\|',
        speakers_block,
    ):
        name = re.sub(r'\(.*?\)', '', m.group(1)).strip()
        # Skip if looks like a column header or non-person word
        if name.lower() not in ('name', 'role', 'linkedin') and ' ' in name:
            names.add(name)

    # Comma-separated names in "Former Unify speakers" and "Guests seen" paragraphs
    for m in re.finditer(r'([A-Z][a-z]+(?:\([a-zA-Z]+\))?\s+[A-Z][a-z]+)\s*[,(]', speakers_block):
        name = re.sub(r'\(.*?\)', '', m.group(1)).strip()
        if ' ' in name:
            names.add(name)

    # First names for fast lookup
    firsts = {n.split()[0] for n in names}
    return frozenset(names | firsts)


_KNOWN_PEOPLE: frozenset[str] = _load_known_people()

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

{content_rules}
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
# Leaked model reasoning: "Wait,", "Corrected version:", "Note:", meta-preambles, etc.
_LEAKED_RE = re.compile(
    r'(?:^|\n)(?:Wait[,\s]|Corrected version:|This violates\b|Note:\s|\[Note\b'
    r'|The issue list says\b|The original post\b|I\'ll keep it\b|I will keep\b'
    r'|The post (?:is already|already|needs to)\b|Since this\b is already\b)',
    re.MULTILINE | re.I,
)
# Wrong Unify spelling: UniFi or Unifi (case-sensitive — "unify" is fine)
_UNIFY_SPELL_RE = re.compile(r'\bUniFi\b|\bUnifi\b')
_SENSITIVE_RE = re.compile(
    r'\b(breaking the law|violat\w+ (?:the )?law|unintentionally breaking|discriminat\w+)\b',
    re.I,
)
_UNIFY_PITCH_RE = re.compile(
    r'\bUnify has\s+\d+|\bUnify has\s+\d+\s*to\s*\d+|\d+\s*(?:to\s*\d+\s*)?\+?\s*data providers?\b',
    re.I,
)
_META_RE = re.compile(
    r'\b(the transcript|based on the transcript|treat it as approximate'
    r'|not entirely clear from the recording|the recording (?:puts|suggests|shows)'
    r'|from the recording|transcript puts)\b',
    re.I,
)
# Subject-less present-participle sentence openers ("Sitting down to plan…")
_SUBJECTLESS_RE = re.compile(
    r'(?:^|\n)(?:Sitting|Walking|Working|Running|Going|Starting|Building|Using|Looking|'
    r'Trying|Taking|Spending|Making|Thinking|Standing|Coming|Joining|Hiring|Scaling|'
    r'Moving|Switching|Turning|Realizing|Learning|Managing|Growing|Watching|Winning|'
    r'Finding|Reaching|Pulling|Pushing|Seeing|Stepping|Sending|Sending|Tracking)\b',
    re.MULTILINE,
)
# Awkward prepositions: "in the phone/call" should be rephrased
_AWKWARD_PREP_RE = re.compile(r'\bin the (?:phone|call|conversation|meeting|room)\b', re.I)
# Hook overclaim: strong quantified scope in first line ("Half our conversations", "Most of our X")
_HOOK_QUANTIFIED_RE = re.compile(r'(?:half|most|all) (?:our|my|of our|of my) \w+', re.I)
# Known transcription errors: ASR mishears that must be corrected if they reach content generation
_TRANSCRIPTION_ERROR_RE = re.compile(r'\bRampit\b')
# Disclaimer / meta sentences (R19)
_DISCLAIMER_RE = re.compile(
    r'(?:^|\n)'
    r'(?:It\'s worth (?:noting|mentioning|emphasizing|pointing out)|'
    r'[A-Za-z]+ was (?:clear|specific|explicit|candid) about|'
    r'To be (?:fair|clear|honest)|'
    r'Worth (?:noting|mentioning|emphasizing)|'
    r'It should be (?:noted|mentioned|said)|'
    r'[Ii]t\'s important to (?:note|mention)|'
    r'(?:As|While) it\'s (?:true|clear|worth))',
    re.MULTILINE | re.I,
)
# Attribution verbs — used to find named persons in the post
_ATTR_VERB_RE = re.compile(
    r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+'
    r'(?:says?|said|shared|explained|mentioned|noted|added|argues?|'
    r'believes?|thinks?|calls?|recommends?|found|learned|discovered|puts? it|puts? it)\b',
)
# Companies / products that may appear as grammatical subjects — not person names
_KNOWN_COMPANIES: frozenset[str] = frozenset({
    'Unify', 'Gumloop', 'Navattic', 'Ramp', 'Salesforce', 'LinkedIn',
    'HubSpot', 'Outreach', 'Salesloft', 'Apollo', 'Clearbit', 'ZoomInfo',
    'ChatGPT', 'Claude', 'OpenAI', 'Anthropic', 'Google', 'Slack', 'Notion',
})
# Count-promise pattern: "3 ways", "5 steps", "2 signals", etc.
_COUNT_PROMISE_RE = re.compile(
    r'\b([2-9])\s+(?:ways?|steps?|signals?|things?|reasons?|buckets?|types?|areas?|'
    r'principles?|rules?|tactics?|tips?|keys?|lessons?|moves?|points?|pillars?|'
    r'components?|signals?|mistakes?|habits?)\b',
    re.I,
)


def _count_delivered_items(post: str) -> int:
    """Count explicitly listed items in a post.

    Recognises three enumeration styles:
      1. Numbered lines: "1. something" or "1) something"
      2. Sentence-start ordinals: "First, ..." / "Second, ..." (at line start)
      3. Labelled sections: "Bucket one / Signal two / Step three / ..."
    Returns 0 when no explicit enumeration is found (skips the promise check).
    """
    # Style 1: numbered lines
    numbered = len(re.findall(r'(?:^|\n)\s*\d+[.)]\s+\S', post))
    if numbered > 0:
        return numbered

    # Style 2: ordinals at the START of a line/sentence only
    # (e.g. "First, do X" but NOT "first-party" or "the third one")
    sentence_ordinals = len(re.findall(
        r'(?:^|\n)\s*(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth)'
        r'(?:[,:\s])',
        post, re.I,
    ))
    if sentence_ordinals > 0:
        return sentence_ordinals

    # Style 3: labelled sections — "<label> <number-word>:"
    # e.g. "Bucket one is …", "Signal two:", "Step three —"
    _NUM_WORDS = r'(?:one|two|three|four|five|six|seven|eight|nine|ten)'
    _LABELS = r'(?:bucket|signal|step|reason|thing|way|rule|tip|key|point|habit|mistake|move|tactic|type|area|pillar)'
    labelled = len(re.findall(
        rf'\b{_LABELS}\s+{_NUM_WORDS}\b',
        post, re.I,
    ))
    return labelled


_OPEN_STOP = frozenset({
    'the', 'a', 'an', 'is', 'it', 'in', 'of', 'to', 'that', 'this', 'and', 'or',
    'at', 'with', 'for', 'by', 'as', 'on', 'was', 'are', 'be', 'were', 'my', 'our',
    'i', 'we', 'they', 'you', 'we', 'had', 'have', 'has', 'not', 'but', 'so', 'then',
    'when', 'just', 'now', 'even', 'one', 'do', 'did',
})


def _has_repetitive_opening(post: str) -> bool:
    """Return True if the first paragraphs restate the same idea (not just share topic words).

    A paragraph is a restatement when: key-word overlap with the hook > 65% AND
    the paragraph does not add significant new vocabulary (new words <= 65% of its total).
    Short hooks (< 3 unique words, e.g. stat-hooks) are always skipped.
    """
    paras = [p.strip() for p in re.split(r'\n\n+', post) if p.strip()][:3]
    if len(paras) < 2:
        return False
    word_sets = []
    for p in paras:
        ws = {w.lower().strip('.,!?;:"\'-') for w in p.split() if len(w) > 3
              and w.lower().strip('.,!?;:"\'-') not in _OPEN_STOP}
        word_sets.append(ws)
    hook_ws = word_sets[0]
    if len(hook_ws) < 3:
        return False
    for ws in word_sets[1:]:
        if not ws:
            continue
        overlap = hook_ws & ws
        if len(overlap) / len(hook_ws) <= 0.65:
            continue
        # High overlap — but is this paragraph mostly new content (explanation)?
        new_vocab_ratio = len(ws - hook_ws) / len(ws)
        if new_vocab_ratio > 0.65:
            continue  # Explanatory paragraph; not a restatement
        return True
    return False

def _clean_post(text: str) -> str:
    """Strip any leaked model reasoning/self-correction from a post.

    Handles: 'Corrected version:' splits, reasoning paragraphs, duplicate paras.
    """
    # If the model included a corrected version, keep only what follows the LAST marker
    for marker in ("Corrected version:", "CORRECTED VERSION:", "corrected version:"):
        if marker in text:
            text = text.split(marker)[-1].strip()

    # Remove any paragraph that starts with a reasoning pattern
    paragraphs = re.split(r'\n\n+', text)
    clean = [p for p in paragraphs if not _LEAKED_RE.search(p)]

    # Deduplicate exact paragraphs (keep first occurrence only)
    seen: set[str] = set()
    deduped: list[str] = []
    for para in clean:
        key = para.strip()
        if not key:
            deduped.append(para)
        elif key not in seen:
            deduped.append(para)
            seen.add(key)
        # else: silently drop duplicate

    return "\n\n".join(deduped).strip()


_REWRITE_USER = """\
This LinkedIn post needs editing. Fix ALL issues listed below:
{issues}

Original post:
{post}

Context (angle and transcript excerpt):
{context}

Point of view rule:
{pov_rule}

Apply ALL content rules (R01–R21 from CONTENT_RULES.md). Key rules:
hook ≤ 8 words; ≤ 1,300 chars; ends with one question ≤ 10 words; no gendered pronouns; \
no sensitive claims; no transcript meta; full name first reference then first name only; \
no leaked reasoning; company is "Unify"; no subject-less participle openers; \
no "in the phone/call"; hook scope matches transcript; count-promises delivered; \
no disclaimer/meta sentences ("It's worth noting", "[Name] was clear about"); \
first 3 paragraphs must not restate the same idea; \
unknown person names → replace with role ("a teammate", "the growth lead").
Return ONLY the rewritten post text — no JSON, no preamble.\
"""


def _editor_check(post: str, post_for: str = "", speaker_name: str = "",
                  transcript: str = "") -> list[str]:
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

    # Transcript meta-commentary
    m = _META_RE.search(post)
    if m:
        issues.append(f"transcript meta-commentary: '{m.group()}' — express uncertainty naturally instead")

    # Leaked model reasoning
    m = _LEAKED_RE.search(post)
    if m:
        issues.append(f"leaked reasoning: '{m.group().strip()[:40]}' — model self-correction in output")

    # Wrong Unify spelling
    m = _UNIFY_SPELL_RE.search(post)
    if m:
        issues.append(f"wrong company name: '{m.group()}' — should be 'Unify'")

    # Duplicated paragraphs
    paras = [p.strip() for p in re.split(r'\n\n+', post) if p.strip()]
    _seen_paras: set[str] = set()
    for para in paras:
        if para in _seen_paras and len(para) > 20:
            issues.append(f"duplicated paragraph: '{para[:50]}…'")
            break
        _seen_paras.add(para)

    # Subject-less present-participle sentence opener
    m = _SUBJECTLESS_RE.search(post)
    if m:
        issues.append(f"subject-less sentence: '{m.group().strip()[:50]}' — start with a subject")

    # Awkward preposition ("in the phone/call")
    m = _AWKWARD_PREP_RE.search(post)
    if m:
        issues.append(f"awkward phrasing: '{m.group()}' — rephrase naturally")

    # Transcription error survived to content generation
    m = _TRANSCRIPTION_ERROR_RE.search(post)
    if m:
        issues.append(f"transcription error: '{m.group()}' — fix company/person name")

    # Disclaimer / meta sentences (R19)
    m = _DISCLAIMER_RE.search(post)
    if m:
        issues.append(f"disclaimer/meta sentence: '{m.group().strip()[:60]}' — cut it entirely")

    # Repetitive opening — first 3 paragraphs restate same idea (R20)
    if _has_repetitive_opening(post):
        first_lines = " / ".join(
            p.split("\n")[0].strip()[:40] for p in re.split(r'\n\n+', post) if p.strip()
        )[:120]
        issues.append(f"repetitive opening: first 3 paragraphs repeat same idea ({first_lines})")

    # Unknown person names (R21): named with attribution verb, not in known list
    for m in _ATTR_VERB_RE.finditer(post):
        name = m.group(1).strip()
        first = name.split()[0]
        if first not in _KNOWN_PEOPLE and name not in _KNOWN_PEOPLE:
            if (len(first) > 2
                    and first not in ("It", "She", "He", "They", "This", "That")
                    and first not in _KNOWN_COMPANIES
                    and name not in _KNOWN_COMPANIES):
                issues.append(
                    f"unknown person name: '{name}' — replace with role "
                    f'("a teammate", "the growth lead", etc.)'
                )
                break  # one flag per post is enough

    # Hook overclaim: quantified scope in first line not supported by transcript
    if transcript:
        first_line = post.split("\n")[0].strip()
        m = _HOOK_QUANTIFIED_RE.match(first_line)
        if m:
            # First 3 words of the hook form the claim phrase (e.g. "half our conversations")
            claim_tokens = first_line.lower().split()[:3]
            claim_phrase = " ".join(claim_tokens)
            if claim_phrase not in transcript.lower():
                issues.append(
                    f"hook may overclaim: '{first_line[:70]}' — verify claim matches transcript"
                )

    # Count-promise vs delivery: "3 ways/signals/steps" must deliver exactly that many
    m = _COUNT_PROMISE_RE.search(post)
    if m:
        promised = int(m.group(1))
        delivered = _count_delivered_items(post)
        if delivered > 0 and delivered != promised:
            issues.append(
                f"count mismatch: promises {promised} {m.group().split()[-1]} "
                f"but delivers {delivered} — rewrite to say '{delivered}' or add the missing item(s)"
            )

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

    return _SYSTEM_BASE.format(
        hooks=hooks_md.strip(),
        pov_block=pov,
        voice_block=voice,
        content_rules=_CONTENT_RULES,
    )


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

    # Use the judged top set (top8=True); fall back to score-based if no top8 flags present.
    # Generate for the full top-8 pool so failing posts can be replaced by the next best.
    top8_set = [c for c in candidates if c.get("top8", False)]
    if top8_set:
        qualifying = sorted(top8_set, key=lambda c: -c.get("score", 0))
    else:
        qualifying = sorted(
            [c for c in candidates if c.get("score", 0) >= SCORE_THRESHOLD],
            key=lambda c: -c["score"],
        )[:MAX_MOMENTS * 2]  # extra bench when no top8 flag

    if not qualifying:
        print("💡 No moments in judged top set — content generation skipped")
        return []

    print(f"💡 Generating content for {len(qualifying)} moments (top-{MAX_MOMENTS} target, bench included)…")

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

        # Strip any leaked reasoning/duplicates before editor check
        content["linkedin_post"] = _clean_post(content["linkedin_post"])

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
        transcript_excerpt = c.get("transcript", "")[:300]
        issues = _editor_check(post, post_for, speaker_name, transcript=transcript_excerpt)
        editor_fixed = False
        if issues:
            print(f"\n      ✍️  editor rewrites ({len(issues)} issue(s)): {issues[0][:60]}…", end=" ", flush=True)
            post = _editor_rewrite(
                post, issues,
                f"Angle: {c.get('angle','')}\nTranscript: {c.get('transcript','')[:300]}",
                post_for, speaker_name,
                client,
            )
            post = _clean_post(post)  # strip any leaked reasoning from the rewrite
            content["linkedin_post"] = post
            remaining = _editor_check(post, post_for, speaker_name, transcript=transcript_excerpt)
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

        # Final quality check: does the post pass all checks?
        final_issues = _editor_check(
            content["linkedin_post"], post_for, speaker_name,
            transcript=c.get("transcript", "")[:300],
        )
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
            "quality_passed": len(final_issues) == 0,
            "_quality_issues": final_issues,
        })

    # ── filter to best MAX_MOMENTS passing posts ──
    passing = [r for r in results if r.get("quality_passed")]
    failing = [r for r in results if not r.get("quality_passed")]
    if failing:
        print(f"\n   ⚠️  {len(failing)} post(s) dropped (still failing after rewrite): "
              f"{[r['id'] for r in failing]}")
    # Take best MAX_MOMENTS from passing; fall back to failing if we're short
    final_results = (passing + failing)[:MAX_MOMENTS]
    for r in final_results:
        r.pop("_quality_issues", None)  # don't expose internal field
    all_quality_passed = all(r.get("quality_passed", False) for r in final_results)
    if all_quality_passed:
        print(f"   ✅  All {len(final_results)} posts passed quality gate")

    # ── save ──
    json_path = out_dir / f"{slug}-content.json"
    json_path.write_text(json.dumps(final_results, indent=2))

    md_lines = [f"# {slug} — Generated Content\n"]
    for r in final_results:
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
    return final_results


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
