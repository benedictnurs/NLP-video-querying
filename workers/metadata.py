from __future__ import annotations

import re

from workers.events import load_definitions
from workers.clock import format_clock as _clock

COMMAND_RE = re.compile(
    r"\b(step away|get back|get down|get on the ground|don't move|hands up|stop)\b",
    re.I,
)


def keyword_hits(transcript: str, segments: list[dict] | None = None, clip_start_ms: int = 0) -> list[dict]:
    text = (transcript or "").lower()
    hits = []
    for name, spec in load_definitions().items():
        for phrase in spec.get("transcript_any") or []:
            count = text.count(phrase.lower())
            if not count:
                continue
            start_ms = clip_start_ms
            needle = phrase.lower()
            for item in segments or []:
                if needle in (item.get("text") or "").lower():
                    start_ms = clip_start_ms + int(item.get("start_ms") or 0)
                    break
            hits.append(
                {
                    "definition": name,
                    "phrase": phrase,
                    "count": count,
                    "start_ms": start_ms,
                    "clock": _clock(start_ms),
                }
            )
    return hits


def repeated_commands(transcript: str) -> int:
    return len(COMMAND_RE.findall(transcript or ""))
