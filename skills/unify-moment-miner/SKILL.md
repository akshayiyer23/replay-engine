---
name: unify-moment-miner
description: Cuts a Unify webinar transcript (sentence-level JSON with speaker, start, end, text) into 15-25 standalone 30-90 second clip candidates, and pitches a Unify-relevant content angle for each. Use before gtm-viral-judge.
---

# Unify Moment Miner

You find the moments in a Unify webinar that could stand alone as a short clip
or LinkedIn post. You do NOT pick the final winners. That is gtm-viral-judge's
job. Your job is to give it 15-25 strong, clean, varied candidates.

Read these first:
- `unify-context.md`: what Unify sells, its messages, personas, speakers, routing rules
- `moment-types.md`: the 6 moment types with examples

## Input
A JSON array of sentences:
```json
[{"i": 0, "speaker": "A", "start": 12340, "end": 15100, "text": "..."}]
```
- `start` and `end` are milliseconds.
- `i` is the sentence index. If it is missing, number sentences from 0 in order.
- A speaker map may be given, e.g. `{"A": "Ethan Dursht", "B": "Quintin Smith"}`.

## Step 1: Read the whole transcript once
Understand the flow before cutting. Note:
- the main topics and where each one starts and ends
- intros, housekeeping, Q&A logistics and sponsor/demo-signup talk (never clip these)

## Step 2: Find candidate moments
A candidate is a run of consecutive sentences that passes ALL six rules:

1. **One idea.** It makes a single point. If it drifts to a second point, end it before the drift.
2. **30-90 seconds.** `end` of last sentence minus `start` of first sentence. Sweet spot is 40-70s.
3. **Starts on a hook.** The first sentence is one of: a bold or contrarian claim, a number, a question, "here's exactly how / the way we do it," a mistake, or a prediction. If the hook is in sentence 3, start at sentence 3.
4. **Stands alone.** No "like I said," "as Quintin mentioned," "this slide," "going back to," or unexplained "it/that/they" in the first sentence. A viewer who saw nothing else must follow it.
5. **Ends on a payoff.** The last sentence lands the answer, result, or lesson. Never end mid-list or mid-story.
6. **Clean cut points.** Start and end exactly on sentence boundaries from the input. Never invent or round timestamps.

Rules for the set of candidates:
- Return 15-25 candidates. If fewer than 15 truly pass, return fewer. Do not pad with weak ones.
- Candidates may overlap by at most 1 sentence.
- Cover the whole webinar. Do not take 10 candidates from the first 5 minutes.
- Aim for variety: at least 4 different moment types across the set.
- Never clip: intros, "can you hear me," agenda, polls, "we'll share the recording," or a speaker pitching a demo signup.

## Step 3: Label each candidate
Use `unify-context.md` for every label.

- `moment_type`: one of contrarian, stat, how_to, story, mistake, prediction
- `pillar`: the Unify content pillar it fits (section 5)
- `connects_to`: the Unify product it quietly connects to (section 3), or "none"
- `persona`: the main audience (SDR, AE, sales leader, RevOps, growth)
- `speaker_name`: from the speaker map
- `post_for`: apply the routing rule in section 7. Current team goes to their own account. Former team goes to "Unify company page."
- `angle`: a one-line post idea a person would stop scrolling for. Plain words, no hype, no product pitch. Example: "Why generic intent data is lying to your SDRs"
- `hook_line`: the strongest opening sentence, quoted exactly from the transcript
- `standalone_check`: one short sentence explaining why it makes sense without context
- `risks`: anything that could hurt it (mentions a slide, names a private customer, mild product pitch, weak ending). Use "none" if clean.

## Step 4: Self-check before returning
For each candidate, confirm:
- [ ] Duration is 30-90 seconds (recompute from timestamps)
- [ ] First sentence passes the hook rule and has no dangling reference
- [ ] Last sentence is a payoff
- [ ] `hook_line` appears word for word in the transcript
- [ ] `post_for` follows the routing rule
Drop any candidate that fails. Do not fix by editing transcript text.

## Output
Return ONLY valid JSON, no extra text:
```json
{
  "webinar": "The Power of Custom AI Signals",
  "candidates": [
    {
      "id": "m01",
      "start_i": 42,
      "end_i": 51,
      "start_ms": 512300,
      "end_ms": 571900,
      "duration_s": 59.6,
      "speaker": "A",
      "speaker_name": "Ethan Dursht",
      "post_for": "Ethan Dursht",
      "moment_type": "contrarian",
      "pillar": "Custom AI signals",
      "connects_to": "Signals & Intent",
      "persona": "SDR",
      "angle": "Why generic intent data is lying to your SDRs",
      "hook_line": "exact first sentence from transcript",
      "transcript": "full text of the clip, sentences joined with spaces",
      "standalone_check": "Explains what a custom signal is before using the term.",
      "risks": "none"
    }
  ]
}
```

You do not score or rank. Leave that to gtm-viral-judge.
