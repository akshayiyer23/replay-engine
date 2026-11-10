#!/usr/bin/env python3
"""
Build candidate clips from a sentences file and a labels file.

Usage:
    python scripts/build_candidates.py <sentences.json> <labels.json> [output.json]

Sentences file: list of {speaker, start, end, text}
Labels file:    list of {id, start_i, end_i, moment_type, pillar, connects_to,
                          persona, post_for, angle, standalone_check, risks}

Adds: speaker, speaker_name, start_ms, end_ms, duration_s, hook_line, transcript
Flags candidates whose duration falls outside [30, 90] seconds.
"""

import json
import sys
from collections import Counter
from pathlib import Path

DURATION_MIN_S = 30
DURATION_MAX_S = 90


def build_candidates(
    sentences: list[dict],
    labels: list[dict],
    speaker_map: dict | None = None,
) -> list[dict]:
    if speaker_map is None:
        speaker_map = {}
    candidates = []
    for label in labels:
        start_i = label["start_i"]
        end_i = label["end_i"]  # inclusive

        span = sentences[start_i : end_i + 1]
        if not span:
            print(f"[WARN] label {label.get('id', '?')}: empty span [{start_i}, {end_i}]")
            continue

        start_ms = span[0]["start"]
        end_ms = span[-1]["end"]
        duration_s = (end_ms - start_ms) / 1000.0

        # Majority speaker: most common speaker code in the span
        speaker_code = Counter(s["speaker"] for s in span).most_common(1)[0][0]
        speaker_name = speaker_map.get(speaker_code, speaker_code)

        hook_line = span[0]["text"]
        transcript = " ".join(s["text"] for s in span)

        out_of_range = not (DURATION_MIN_S <= duration_s <= DURATION_MAX_S)

        candidate = {
            **label,
            "speaker": speaker_code,
            "speaker_name": speaker_name,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_s": round(duration_s, 2),
            "hook_line": hook_line,
            "transcript": transcript,
            "duration_flag": "OUT_OF_RANGE" if out_of_range else "OK",
        }
        candidates.append(candidate)

        if out_of_range:
            print(
                f"[FLAG] label {label['id']}: duration {duration_s:.1f}s is outside "
                f"{DURATION_MIN_S}–{DURATION_MAX_S}s"
            )

    return candidates


def main():
    if len(sys.argv) < 3:
        print("Usage: build_candidates.py <sentences.json> <labels.json> [output.json]")
        sys.exit(1)

    sentences_path = Path(sys.argv[1])
    labels_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3]) if len(sys.argv) > 3 else labels_path.with_stem(labels_path.stem + "-candidates")

    sentences = json.loads(sentences_path.read_text())
    labels = json.loads(labels_path.read_text())

    candidates = build_candidates(sentences, labels)

    output_path.write_text(json.dumps(candidates, indent=2))
    print(f"Saved {len(candidates)} candidates → {output_path}")


if __name__ == "__main__":
    main()
