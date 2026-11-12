# Content Rules — Unify LinkedIn Post Generation

Every LinkedIn post must satisfy ALL rules below.
The editor gate checks each rule programmatically and rewrites violations.
The audit script verifies every golden post against this file.
This file is loaded verbatim into the generation system prompt — keep it terse.

---

## R01 · Hook ≤ 8 words
The first line must be 8 words or fewer. Never start with "I". No question marks.
Prefer a STAT opener if the transcript contains a specific number.

## R02 · Ends with a question
The last non-empty line must end with "?". ≤ 10 words. Invites a real comment.

## R03 · Length ≤ 1,300 chars
The full post must be 1,300 characters or fewer.

## R04 · POV — speaker's personal account
Write in FIRST PERSON. Never refer to the speaker by last name or in third person.
Wrong: "Hughes's answer", "Austin says". Right: use "I", "we", "my", "our".

## R05 · POV — Unify company page
Write in THIRD PERSON. Credit the speaker by full name on first reference.
Never use I/me/my/myself. "We" or "our" for Unify the company is fine.

## R06 · Name usage
Full name on first reference only. First name only after that.
Wrong: "Burns mentioned" on second reference. Right: "Stephanie mentioned".

## R07 · No gendered pronouns
Never use he/she/his/her/him. Rewrite around the name ("the approach", "that answer").

## R08 · Facts grounded in transcript
Every claim must be directly in the transcript — no inference, no extrapolation.
Numbers must preserve qualifiers: "close to 30", "around 40%" — not bare "30" or "40%".

## R09 · Hook must not change scope
The hook must not broaden, narrow, or shift who/what/how-often vs the transcript.
Wrong: "Half our conversations start with LinkedIn" when the transcript says "it comes up about half the time".

## R10 · Count-promise delivery
If the post promises N items ("3 signals", "5 steps"), it must deliver exactly N.
Change the number or add the missing item — never promise N and deliver N±1.

## R11 · No product pitch
Never describe Unify features in a sales context ("Unify has 40 data providers").
Mention Unify by name as context only ("a Unify workflow", "inside Unify").

## R12 · No sensitive claims
Never name a real company, school, or person in a negative/legal/compliance context.
Anonymize: "a large school district", "one company". Drop if it can't stand without it.

## R13 · Known-name spelling
"Unify" — never UniFi or Unifi. "Ramp" — never Rampit.
All other company and person names must use their canonical spelling.

## R14 · No speaker labels
Never use "Guest", "Host", "Speaker A", "Speaker B" as attribution.
Use the person's actual name or a role description.

## R15 · No transcript meta-commentary
Never reference the transcript, the recording, or uncertainty about source accuracy.
Wrong: "the transcript puts that number at…", "based on the recording".
Right: express uncertainty inline — "around 420", "roughly 30%".

## R16 · No leaked reasoning
Never let model self-correction appear in the post.
Wrong: "Wait,", "Corrected version:", "Note:", "The issue list says", "The original post".

## R17 · No subject-less sentences
Never open a sentence with a subject-less present participle.
Wrong: "Sitting down to plan…". Right: "Every Sunday I sit down to plan…".

## R18 · No awkward prepositions
Never write "in the phone" or "in the call". Rephrase naturally.

## R19 · No disclaimer/meta sentences
Never include sentences that disclaim accuracy or add commentary about the post itself.
Wrong: "It's worth noting that…", "[Speaker] was clear about…", "To be fair…".
These are meta — cut them entirely.

## R20 · No repetitive opening
The first three paragraphs must not restate the same core idea.
If the hook says "420 demos in 48 hours", paragraph 2 must not say the same thing differently.

## R21 · Unknown person names
If the post names a person who is not the speaker, not a known Unify team member,
and not a named guest from the webinar, replace with a role.
Wrong: "as John explained". Right: "as the RevOps lead explained".

---

## Formatting rules (non-negotiable)
- Short paragraphs: 1–3 sentences. Blank line between paragraphs. Plain text only.
- Line 2: open loop — tease the insight, no payoff yet. No "Here's what I mean."
- Zero explicit pitches: no "sign up", "try X", "book a demo", "check out".
- Written prose: cut all filler ("and so", "you know", "right?", "I mean", "sort of").
- Carousel: each slide = one punchy line (8–12 words). No bullets inside a slide.
- Caption: 3–6 words max for the first 3 seconds of on-screen text.
