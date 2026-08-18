from __future__ import annotations

import re

from workers.events import load_definitions

COMMAND_RE = re.compile(
    r"\b(step away|get back|get down|get on the ground|don't move|hands up|stop)\b",
    re.I,
)


def keyword_hits(transcript: str) -> list[dict]:
    text = (transcript or "").lower()
    hits = []
    for name, spec in load_definitions().items():
        for phrase in spec.get("transcript_any") or []:
            count = text.count(phrase.lower())
            if count:
                hits.append({"definition": name, "phrase": phrase, "count": count})
    return hits


def repeated_commands(transcript: str) -> int:
    return len(COMMAND_RE.findall(transcript or ""))
