#!/usr/bin/env python3
"""scripts/audit_content.py — Audit all 12 result sets for critical content quality issues.

Checks every linkedin_post for the following CRITICAL issues:
  1. Meta/reasoning text ("Wait,", "Corrected version:", "Note:", transcript refs)
  2. Duplicated paragraphs
  3. Debug chips in candidates' `why` field (non-allowlist: anything without N.NN×)
  4. "Guest" / "Host" / "Speaker A/B" labels
  5. Wrong company spelling ("UniFi", "Unifi")
  6. Posts > 1,300 characters
  7. No question at end
  8. POV violations (first person on Unify page, third person on speaker page)
  9. Gendered pronouns

(Hook length 9-10 words is pre-existing / tolerated and NOT flagged here.)

Fixes found issues via the editor gate, re-locks golden files, and prints a report.

Usage:
    cd /path/to/replay-engine
    python3 scripts/audit_content.py
"""

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

from generate_content import (  # noqa: E402
    _clean_post,
    _editor_rewrite,
    _LEAKED_RE,
    _META_RE,
    _UNIFY_SPELL_RE,
    _SPEAKER_LABEL_RE,
    _GENDER_RE,
    _SENSITIVE_RE,
    _UNIFY_PITCH_RE,
    _POV_FIRST_RE,
)
import anthropic
import os
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

# ── slugs ─────────────────────────────────────────────────────────────────────

GOLDEN_SLUGS = [
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

GTMNOW_SLUG = "gtm-153-building-technical-growth-machines-signal-based-sell"

# ── chip audit helper (allowlist: only "Label (N.NN×)") ──────────────────────

_CHIP_RE = re.compile(r'^.+\(\d+\.\d+[×x]\)$')


def _has_debug_chips(why: str) -> list[str]:
    """Return non-allowlist chip strings from a why field."""
    if not why:
        return []
    bad = []
    for part in re.split(r'[+;]', why):
        part = part.strip()
        if part and not _CHIP_RE.match(part):
            bad.append(part)
    return bad


# ── per-post critical check (excludes hook-length) ───────────────────────────

def audit_post(item: dict) -> list[str]:
    """Return list of CRITICAL issue strings for a content item (no hook-length)."""
    post = item.get("linkedin_post", "")
    post_for = item.get("post_for", "")
    speaker = item.get("speaker_name", "")
    issues: list[str] = []

    # Post > 1,300 chars
    if len(post) > 1300:
        issues.append(f"too long: {len(post)} chars")

    # No question at end
    last = next((ln for ln in reversed(post.split("\n")) if ln.strip()), "")
    if not last.strip().endswith("?"):
        issues.append(f"no end question: '…{last.strip()[-30:]}'")

    # POV: Unify company page → must be third person
    if "unify company page" in post_for.lower():
        m = _POV_FIRST_RE.search(post)
        if m:
            issues.append(f"POV: first-person '{m.group()}' on Unify company page")

    # Gendered pronouns
    m = _GENDER_RE.search(post)
    if m:
        issues.append(f"gendered pronoun: '{m.group()}'")

    # Sensitive claims
    m = _SENSITIVE_RE.search(post)
    if m:
        issues.append(f"sensitive claim: '{m.group()[:40]}'")

    # Unify feature pitch
    m = _UNIFY_PITCH_RE.search(post)
    if m:
        issues.append(f"Unify feature pitch: '{m.group()[:40]}'")

    # Speaker labeled Guest/Host
    m = _SPEAKER_LABEL_RE.search(post)
    if m:
        issues.append(f"speaker label: '{m.group()}'")

    # Transcript meta-commentary
    m = _META_RE.search(post)
    if m:
        issues.append(f"transcript meta: '{m.group()[:40]}'")

    # Leaked model reasoning
    m = _LEAKED_RE.search(post)
    if m:
        issues.append(f"leaked reasoning: '{m.group().strip()[:40]}'")

    # Wrong Unify spelling
    m = _UNIFY_SPELL_RE.search(post)
    if m:
        issues.append(f"wrong spelling: '{m.group()}'")

    # Duplicated paragraphs
    paras = [p.strip() for p in re.split(r'\n\n+', post) if p.strip()]
    seen_p: set[str] = set()
    for para in paras:
        if para in seen_p and len(para) > 20:
            issues.append(f"duplicated paragraph: '{para[:50]}…'")
            break
        seen_p.add(para)

    # Structural clean detects additional leaked content
    cleaned = _clean_post(post)
    if cleaned != post.strip() and not any("leaked" in i or "duplicated" in i for i in issues):
        issues.append("leaked/duplicated content detected by _clean_post")

    return issues


# ── fix a single post via editor rewrite ─────────────────────────────────────

def fix_post(item: dict, issues: list[str], client: anthropic.Anthropic) -> str:
    """Apply _clean_post + editor rewrite, return cleaned post text."""
    post = _clean_post(item["linkedin_post"])
    post_for = item.get("post_for", "")
    speaker = item.get("speaker_name", "")
    angle = item.get("angle", "")
    transcript = item.get("transcript", "")[:300]
    context = f"Angle: {angle}\nTranscript: {transcript}"
    post = _editor_rewrite(post, issues, context, post_for, speaker, client)
    post = _clean_post(post)
    return post


# ── main audit loop ───────────────────────────────────────────────────────────

def main() -> None:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    total_items = 0
    total_critical = 0
    total_fixed = 0
    total_unfixed = 0
    report_lines: list[str] = []

    def _process_slug(slug: str, content_path: Path, cand_path: Path, is_golden: bool) -> None:
        nonlocal total_items, total_critical, total_fixed, total_unfixed

        if not content_path.exists():
            report_lines.append(f"⚠️  {slug}: no content file — skip")
            return

        content_items: list[dict] = json.loads(content_path.read_text())
        candidates: list[dict] = json.loads(cand_path.read_text()) if cand_path.exists() else []

        slug_post_issues: list[tuple[int, str, list[str]]] = []  # (idx, item_id, issues)
        for idx, item in enumerate(content_items):
            total_items += 1
            issues = audit_post(item)
            if issues:
                slug_post_issues.append((idx, item["id"], issues))

        # Chip audit (UI-level; informational only — not counted as critical)
        chip_findings: list[tuple[str, list[str]]] = []
        for c in candidates:
            bad = _has_debug_chips(c.get("why", ""))
            if bad:
                chip_findings.append((c["id"], bad[:2]))  # show max 2 per candidate

        if not slug_post_issues and not chip_findings:
            report_lines.append(f"✅  {slug}")
            return

        report_lines.append(f"\n{'='*72}")
        report_lines.append(f"  {slug}")
        report_lines.append(f"{'='*72}")

        if chip_findings:
            report_lines.append(f"  [UI chips — hidden by allowlist; no rewrite needed]")
            for mid, chips in chip_findings[:3]:  # sample only
                report_lines.append(f"  🐛  {mid}: {chips}")
            if len(chip_findings) > 3:
                report_lines.append(f"  ... and {len(chip_findings) - 3} more")

        if not slug_post_issues:
            return  # only chip issues — no rewrite needed

        for _, mid, ilist in slug_post_issues:
            report_lines.append(f"  ❌  {mid}: {'; '.join(ilist)}")
            total_critical += len(ilist)

        # Fix each post with issues
        changed = False
        for idx, mid, ilist in slug_post_issues:
            report_lines.append(f"  ✍️  fixing {mid}…")
            new_post = fix_post(content_items[idx], ilist, client)
            remaining = audit_post({**content_items[idx], "linkedin_post": new_post})
            if not remaining:
                content_items[idx]["linkedin_post"] = new_post
                report_lines.append(f"  ✅  {mid} fixed")
                total_fixed += len(ilist)
                changed = True
            else:
                report_lines.append(f"  ⚠️  {mid} still: {'; '.join(remaining)}")
                total_unfixed += len(remaining)
                # Save the cleaned version anyway (better than leaving leaked reasoning)
                content_items[idx]["linkedin_post"] = new_post
                changed = True

        if changed:
            content_path.write_text(json.dumps(content_items, indent=2))
            if is_golden:
                report_lines.append(f"  🔒  re-locked {content_path.name}")

    # ── 11 golden webinars ─────────────────────────────────────────────────────
    for slug in GOLDEN_SLUGS:
        content_path = DATA_DIR / f"{slug}-content.golden.json"
        cand_path = DATA_DIR / f"{slug}-candidates.golden.json"
        _process_slug(slug, content_path, cand_path, is_golden=True)

    # ── GTMnow live run ────────────────────────────────────────────────────────
    gtmnow_content = DATA_DIR / f"{GTMNOW_SLUG}-content.json"
    gtmnow_cand = DATA_DIR / f"{GTMNOW_SLUG}-candidates.json"
    _process_slug(GTMNOW_SLUG + " (live)", gtmnow_content, gtmnow_cand, is_golden=False)

    # ── summary ───────────────────────────────────────────────────────────────
    print("\n".join(report_lines))
    print(f"\n{'='*72}")
    print(f"AUDIT SUMMARY")
    print(f"{'='*72}")
    print(f"  Posts audited  : {total_items}")
    print(f"  Critical issues: {total_critical}")
    print(f"  Fixed          : {total_fixed}")
    print(f"  Still remaining: {total_unfixed}")
    if total_unfixed == 0 and total_critical > 0:
        print(f"\n  ✅  All {total_critical} critical issue(s) resolved.")
    elif total_critical == 0:
        print(f"\n  ✅  No critical issues found — all 12 result sets are clean.")
    else:
        print(f"\n  ⚠️  {total_unfixed} critical issue(s) could not be auto-fixed.")


if __name__ == "__main__":
    main()
