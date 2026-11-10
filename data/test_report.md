# Replay Engine — Test Report
**Date:** 2026-11-10  
**Pipeline version:** Step 6 batch run  
**Tester:** Claude Code

---

## Summary

| # | Video | Status | Time | Top Score | Judge Parser Fix |
|---|-------|--------|------|-----------|-----------------|
| 1 | Winning on LinkedIn in 2026 | ✅ PASS | 2:00 | 99 | — |
| 2 | Cold Email Deliverability | ✅ PASS (re-run) | 1:41 | 73 | ✅ Required fix |
| 3 | Claude for RevOps | ✅ PASS | 2:16 | 80 | — |
| 4 | Outbound Email Masterclass | ✅ PASS | 2:07 | 72 | — |

**Bug discovered and fixed:** Cold Email Deliverability initially FAILed because the judge JSON parser used a dict-only fallback regex that missed "Extra data" errors (trailing text after valid JSON). Fixed by adding `json.decoder.JSONDecoder().raw_decode()` as the first fallback step in `judge_candidates()` in `pipeline.py`.

---

## Webinar 1 — Winning on LinkedIn in 2026

**URL:** https://www.youtube.com/watch?v=MUJb8_EScMc  
**Duration:** 34:22 · **Upload date:** 2026-02-25  
**Pipeline time:** 120s (download + transcribe + mine + judge + content)

### Speakers detected
| Code | Name | Confidence | Post routing |
|------|------|-----------|--------------|
| A | Tommy Clark | 75% | Unify company page |
| B | Austin Hughes | 85% | Austin Hughes |
| C | Speaker C | 0% | Unify company page |

### Results
- Moments mined: 22  
- Top score: 99 (m0464, story)  
- Moments with content: 6  

### Top 6 posts
| ID | Score | Speaker | Type | First line |
|----|-------|---------|------|------------|
| m0464 | 99 | Austin Hughes | story | "10 employees. 10 audiences. One announcement." |
| m1126 | 95 | Austin Hughes | story | "The Dreamforce stunt lost to its LinkedIn post." |
| m0619 | 78 | Tommy Clark | how_to | "Your origin story is your best-performing post." |
| m0873 | 77 | Tommy Clark | how_to | "The easiest founder post to write." |
| m1844 | 72 | Austin Hughes | contrarian | "Tens of demos booked per month." |
| m0116 | 68 | Tommy Clark | contrarian | "Anyone can post on LinkedIn now." |

### QA gate results
| Gate | Result |
|------|--------|
| label_fields | ✅ pass=22 |
| numbers_in_transcript | ✅ pass=22 |
| duration | ✅ pass=22 |
| diversity | 🔧 auto-fixed 1 |
| min3 | ✅ pass |
| editor | 🔧 auto-fixed 1 |

---

## Webinar 2 — Cold Email Deliverability

**URL:** https://www.youtube.com/watch?v=51GS5O52sQI  
**Duration:** 33:22 · **Upload date:** 2026-10-20  
**Pipeline time:** 101s (cached transcript — download/transcribe skipped)

### Initial run — FAILED
**Error:** Judge crashed with `json.JSONDecodeError: Extra data: line 3 column 1 (char 228)`  
**Root cause:** The judge response contained trailing commentary after the JSON object. The original fallback `re.search(r"\{.*\}", raw, re.DOTALL)` only handles malformed JSON — it does not handle well-formed JSON followed by extra text. Python's `json.loads` raises "Extra data" in this case, and the regex substitution was never reached.  
**Fix:** Added `json.decoder.JSONDecoder().raw_decode(raw.lstrip())` as the primary fallback before the regex, so trailing text is silently ignored.

### Re-run after fix — PASSED

### Speakers detected
| Code | Name | Confidence | Post routing |
|------|------|-----------|--------------|
| A | Speaker A (Alex, host) | 55% | Unify company page |
| B | Kyle Tymo | 95% | Kyle Tymo |
| C | Speaker C | 0% | Unify company page |

### Results
- Moments mined: 21  
- Top score: 73 (m1077, story)  
- Moments with content: 6  

### Top 6 posts
| ID | Score | Speaker | Type | First line |
|----|-------|---------|------|------------|
| m1077 | 73 | Kyle Tymo | story | "40 people. One company. One week." |
| m0902 | 65 | Kyle Tymo | contrarian | "10,000 of the same email. That's the problem." |
| m1256 | 64 | Kyle Tymo | how_to | "Four teams. One cold email program." |
| m1013 | 62 | Kyle Tymo | mistake | "New mailbox. Reply rates tanked." |
| m1410 | 62 | Kyle Tymo | how_to | "Three things to do this week." |
| m0174 | 57 | Kyle Tymo | stat | "10,000 emails. 36 meetings versus 28." |

### QA gate results
| Gate | Result |
|------|--------|
| label_fields | ✅ pass=21 |
| numbers_in_transcript | ❌ fail=2 (non-blocking) |
| duration | ✅ pass=21 |
| diversity | 🔧 auto-fixed 1 |
| min3 | ✅ pass |
| editor | ❌ fail=1 — filler phrase "you know" persisted after retry |

---

## Webinar 3 — Claude for RevOps

**URL:** https://www.youtube.com/watch?v=7UUXmRhy-g8  
**Duration:** 46:24 · **Upload date:** 2026-03-31  
**Pipeline time:** 136s (download + transcribe + mine 5 chunks + judge + content)

### Speakers detected
| Code | Name | Confidence | Post routing |
|------|------|-----------|--------------|
| A | Quintin Smith | 80% | Unify company page |
| B | Jeff Ignacio | 95% | Unify company page |
| C | Hassan Irshad | 90% | Hassan Irshad |

### Results
- Moments mined: 27  
- Top score: 80 (m2652, story)  
- Moments with content: 6  

### Top 6 posts
| ID | Score | Speaker | Type | First line |
|----|-------|---------|------|------------|
| m2652 | 80 | Jeff Ignacio | story | "3 years of renewals. Never tracked." |
| m1339 | 72 | Hassan Irshad | story | ""Closes this month." It never does." |
| m2717 | 69 | Hassan Irshad | mistake | "I vibe coded a Salesforce MCP connector." |
| m1269 | 66 | Hassan Irshad | how_to | "Stop writing your own prompts." |
| m1935 | 64 | Jeff Ignacio | how_to | "$30 to score 15,000 accounts." |
| m2086 | 63 | Hassan Irshad | stat | "A week of analysis. Now a couple hours." |

### QA gate results
| Gate | Result |
|------|--------|
| label_fields | ✅ pass=27 |
| numbers_in_transcript | ❌ fail=1 (non-blocking) |
| duration | ✅ pass=27 |
| diversity | 🔧 auto-fixed 1 |
| min3 | ✅ pass |
| editor | 🔧 auto-fixed 1 |

---

## Webinar 4 — Outbound Email Masterclass

**URL:** https://www.youtube.com/watch?v=64fC3BdDMYk  
**Duration:** 42:13 · **Upload date:** 2026-05-20  
**Pipeline time:** 127s (download + transcribe + mine 5 chunks + judge + content)

### Speakers detected
| Code | Name | Confidence | Post routing |
|------|------|-----------|--------------|
| A | Speaker A (host/moderator) | 40% | Unify company page |
| B | Cole Courson | 95% | Unify company page |
| C | Bobby Foose | 90% | Unify company page |

*Note: Speaker A is the unnamed moderator; Cole Courson left Unify (now at OpenAI per LinkedIn), so both he and Bobby Foose route to Unify company page per the current-team rule.*

### Results
- Moments mined: 23  
- Top score: 72 (m0777, how_to)  
- Moments with content: 6  
- Stat rescue: 1 moment auto-rescued from large-number claim  

### Top 6 posts
| ID | Score | Speaker | Type | First line |
|----|-------|---------|------|------------|
| m0777 | 72 | Cole Courson | how_to | "3 buckets. That's how we sort every signal." |
| m0608 | 69 | Cole Courson | how_to | "200, 300, 400k emails a month." |
| m2013 | 69 | Cole Courson | mistake | "100K emails after 50 a day gets mailboxes can…" |
| m1092 | 68 | Bobby Foose | contrarian | ""Saw you on our website" is lazy." |
| m2336 | 68 | Cole Courson | contrarian | "Gmail vs Outlook is the wrong deliverability…" |
| m0355 | 64 | Cole Courson | stat | "300 to 400K emails a month." |

### QA gate results
| Gate | Result |
|------|--------|
| label_fields | ✅ pass=23 |
| numbers_in_transcript | ❌ fail=2 (non-blocking) |
| duration | ❌ fail=1 — m0000 is 25.3s (too short, below 30s floor) |
| diversity | ✅ pass |
| min3 | ✅ pass |
| editor | ❌ fail=1 — hook still 9 words after retry |

---

## Failure Tests

### Test F1 — Video under 5 minutes

**Input:** https://www.youtube.com/watch?v=vP3rHYv5nd0 ("Unify: Selling is Human", 0:34)  
**Expected:** Clean error, no stack trace  
**Result:** ✅ PASS

```
❌  Video is 0.6 min — too short to find multiple clip moments (min 5 min).
```

Error fires before any download is attempted. Exit code 1. No stack trace.

---

### Test F2 — Pasted transcript with no timestamps

**Input:** `test-no-timestamps.txt` — 820-word plain text, no timestamps, no speaker labels  
**Expected:** Either clean results or a friendly error  
**Result:** ✅ PASS — clean results

**Pipeline behavior:**
- Detected input type as `txt`
- Estimated timestamps at 150 WPM → 74 sentences, 5.9 min
- Detected 1 speaker: Sarah Chen (VP Sales at GrowthStack, conf 95%)
- Sarah Chen is not a Unify team member → routes to "Unify company page"
- Mined 5 moments, judged all 5 (top score 95)
- All 6 QA gates passed

**Top 3 posts:**
| ID | Score | Type | First line |
|----|-------|------|------------|
| m0163 | 95 | mistake | "30% of their team, gone. We sent congratulations." |
| m0282 | 68 | contrarian | "8 meetings a month to 44. One quarter." |
| m0058 | 67 | how_to | "2% to 11% in 90 days." |

---

## Bug Found and Fixed

**Bug:** `judge_candidates()` in `pipeline.py` — JSON fallback did not handle "Extra data" errors  
**Symptom:** Cold Email Deliverability (20+ moments) caused Claude to include a trailing sentence after the JSON response. `json.loads()` raised `JSONDecodeError: Extra data` and the existing fallback (`re.search(r"\{.*\}", ...)`) was never reached because the JSON itself was valid — just had extra text after it.  
**Fix:** Added `json.decoder.JSONDecoder().raw_decode(raw.lstrip())` as the first fallback, which parses one complete JSON value and ignores everything after it.  
**Lines changed:** `pipeline.py` lines ~971–979  

---

## Precomputed / Locked

All 4 webinars locked and saved to `data/precomputed/<slug>/index.json`.

| Webinar | Slug | Golden locked |
|---------|------|--------------|
| Winning on LinkedIn 2026 | `winning-on-linkedin-in-2026-founder-led-content-pipeline-gro` | ✅ |
| Cold Email Deliverability | `cold-email-deliverability-the-hidden-infrastructure-behind-o` | ✅ |
| Claude for RevOps | `claude-for-revops-how-to-leverage-ai-to-take-revops-from-tac` | ✅ |
| Outbound Email Masterclass | `outbound-email-masterclass-webinar` | ✅ |
