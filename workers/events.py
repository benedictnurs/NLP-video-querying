from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import yaml

from workers.clock import resolve_event_time
from workers.paths import data_dir

_LOCK = threading.Lock()
_SLUG_RE = re.compile(r"[^a-z0-9_]+")
_BLOCKED = re.compile(
    r"(guilt|guilty|intoxicat|drunk|crime_of|assaulted|murder|felon|illegal)",
    re.I,
)


def learned_path() -> Path:
    return Path(os.environ.get("DEFINITIONS_LEARNED_PATH", str(data_dir() / "definitions_learned.yaml")))


def definition_path() -> Path:
    return Path(os.environ.get("DEFINITIONS_PATH", "/opt/airflow/definitions.yaml"))


def load_definitions() -> dict:
    core = _read_yaml(definition_path())
    learned = _read_yaml(learned_path())
    return {**learned, **core}


def catalog_for_prompt(definitions: dict | None = None) -> str:
    definitions = definitions or load_definitions()
    lines = []
    for name, spec in definitions.items():
        title = spec.get("title") or name
        aliases = spec.get("aliases") or []
        alias_bit = f" (aliases: {', '.join(aliases)})" if aliases else ""
        lines.append(f"- {name}{alias_bit}: {title}")
        if spec.get("description"):
            lines.append(f"  context: {_one_line(spec['description'])}")
        if spec.get("how_to_confirm"):
            lines.append(f"  evidence: {_one_line(spec['how_to_confirm'])}")
        phrases = spec.get("transcript_any") or []
        if phrases:
            lines.append("  phrases: " + "; ".join(phrases[:6]))
    return "\n".join(lines)


def attach_event_metadata(record: dict, events: list[dict] | None = None) -> dict:
    events = _normalize_events(record, events if events is not None else record.get("events") or [])
    record["events"] = events
    record["event_types"] = [item.get("type") or item["definition"] for item in events]
    record["event_evidence"] = [
        f"{item['definition']} | {ev.get('modality') or 'note'} | {ev.get('value')}"
        for item in events
        for ev in item.get("evidence") or []
        if ev.get("value")
    ]
    return record


def events_from_local(record: dict, definitions: dict | None = None) -> list[dict]:
    definitions = definitions or load_definitions()
    events = []
    seen = set()
    for hit in record.get("keyword_hits") or []:
        name = hit.get("definition")
        if not name or name in seen:
            continue
        seen.add(name)
        spec = definitions.get(name) or {}
        events.append(
            {
                "type": name,
                "definition": name,
                "title": spec.get("title") or name,
                "start_ms": hit.get("start_ms") or record.get("start_ms"),
                "end_ms": record.get("end_ms"),
                "clock": hit.get("clock"),
                "confidence": 0.7,
                "source": "keyword",
                "context": _one_line(spec.get("description") or ""),
                "evidence": [{"modality": "transcript", "value": hit.get("phrase")}],
            }
        )
    for name in record.get("candidate_definitions") or []:
        if name in seen:
            continue
        spec = definitions.get(name) or {}
        seen.add(name)
        start_ms = record.get("start_ms")
        if name == "loud_impact":
            rel = (record.get("signals") or {}).get("impact_at_ms")
            if rel is not None:
                start_ms = int(record.get("start_ms") or 0) + int(rel)
        events.append(
            {
                "type": name,
                "definition": name,
                "title": spec.get("title") or name,
                "start_ms": start_ms,
                "end_ms": record.get("end_ms"),
                "confidence": 0.6,
                "source": "local_signals",
                "context": _one_line(spec.get("description") or ""),
                "evidence": [{"modality": "signal", "value": name}],
            }
        )
    return events


def persist_new_definitions(proposals: list[dict], updates: list[dict] | None = None) -> list[str]:
    changed: list[str] = []
    updates = updates or []
    if not proposals and not updates:
        return changed
    with _LOCK:
        core = _read_yaml(definition_path())
        learned = _read_yaml(learned_path())
        catalog = {**learned, **core}
        for raw in proposals:
            spec = _proposal_to_spec(raw)
            if not spec:
                continue
            name = spec.pop("_id")
            if name in catalog:
                merged = _merge_spec(catalog[name], spec)
                if merged != catalog[name]:
                    catalog[name] = merged
                    if name not in changed:
                        changed.append(name)
                continue
            catalog[name] = spec
            changed.append(name)
        for raw in updates:
            name = _slug(raw.get("id") or raw.get("definition") or "")
            if not name or name not in catalog:
                continue
            merged = _merge_spec(catalog[name], raw)
            if merged != catalog[name]:
                catalog[name] = merged
                if name not in changed:
                    changed.append(name)
        if changed:
            _write_definitions(catalog)
            learned_path().write_text("{}\n")
    return changed


def ensure_event_definitions(events: list[dict]) -> list[str]:
    """If Gemini used an event id that is not in the catalog, add it to definitions.yaml."""
    known = load_definitions()
    proposals = []
    seen = set()
    for item in events or []:
        name = _event_id(item)
        if not name or name in known or name in seen:
            continue
        seen.add(name)
        description = (
            item.get("summary")
            or item.get("analysis")
            or item.get("context")
            or f"Observable scene labeled {name.replace('_', ' ')}."
        )
        proposals.append(
            {
                "id": name,
                "title": item.get("title") or name.replace("_", " "),
                "description": str(description).strip(),
                "how_to_confirm": "Confirm from labeled frames and transcript quotes in the clip.",
                "aliases": item.get("aliases") or [],
                "transcript_any": [
                    ev.get("value")
                    for ev in (item.get("evidence") or [])
                    if isinstance(ev, dict) and ev.get("modality") == "transcript" and ev.get("value")
                ],
            }
        )
    return persist_new_definitions(proposals)


def _proposal_to_spec(raw: dict) -> dict | None:
    name = _slug(raw.get("id") or raw.get("definition") or raw.get("title") or "")
    if not name or _BLOCKED.search(name):
        return None
    description = (raw.get("description") or raw.get("context") or "").strip()
    if len(description) < 12:
        return None
    phrases = [str(item).strip() for item in (raw.get("transcript_any") or []) if str(item).strip()]
    aliases = [str(item).strip() for item in (raw.get("aliases") or []) if str(item).strip()]
    return {
        "_id": name,
        "type": "event",
        "title": (raw.get("title") or name.replace("_", " ")).strip()[:80],
        "description": description[:600],
        "how_to_confirm": (raw.get("how_to_confirm") or "").strip()[:400],
        "aliases": aliases[:8],
        "transcript_any": phrases[:8],
        "source": "gemini",
    }


def _merge_spec(existing: dict, raw: dict) -> dict:
    merged = dict(existing)
    title = (raw.get("title") or "").strip()
    if title:
        merged["title"] = title[:80]
    description = (raw.get("description") or raw.get("context") or "").strip()
    if len(description) >= 12:
        merged["description"] = description[:600]
    confirm = (raw.get("how_to_confirm") or "").strip()
    if confirm:
        merged["how_to_confirm"] = confirm[:400]
    merged["aliases"] = _merge_lists(existing.get("aliases"), raw.get("aliases"), 8)
    merged["transcript_any"] = _merge_lists(existing.get("transcript_any"), raw.get("transcript_any"), 12)
    merged["type"] = "event"
    merged["source"] = existing.get("source") or "gemini"
    return merged


def _merge_lists(old, new, cap: int) -> list[str]:
    seen: list[str] = []
    for item in [*(old or []), *(new or [])]:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen[:cap]


def _write_definitions(catalog: dict) -> None:
    path = definition_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Observable event buckets for police video. These are scene labels, not charges.\n"
        "# Each top-level id is the Event.type in Neo4j (e.g. traffic_stop).\n"
        "# Gemini may add a new id when nothing here fits, and may edit aliases/phrases on an existing id.\n\n"
    )
    path.write_text(
        header
        + yaml.safe_dump(
            catalog,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=88,
        )
    )


def _normalize_events(record: dict, events: list[dict]) -> list[dict]:
    definitions = load_definitions()
    cleaned = []
    seen = set()
    for item in events:
        name = _canonical_name(_event_id(item), definitions)
        if not name or name not in definitions or name in seen:
            continue
        spec = definitions.get(name) or {}
        evidence = item.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [{"modality": "note", "value": evidence}]
        evidence = [
            {
                "modality": (ev.get("modality") or "note") if isinstance(ev, dict) else "note",
                "value": (ev.get("value") if isinstance(ev, dict) else str(ev)) or "",
            }
            for ev in evidence
            if (ev.get("value") if isinstance(ev, dict) else str(ev))
        ]
        if not evidence and item.get("context"):
            evidence = [{"modality": "note", "value": item["context"]}]
        important = [
            str(fact).strip()
            for fact in (item.get("important") or record.get("important") or [])
            if str(fact).strip()
        ]
        for fact in important:
            if not any(ev.get("value") == fact for ev in evidence):
                evidence.append({"modality": "important", "value": fact})
        summary = (item.get("summary") or item.get("context") or record.get("summary") or "").strip()
        analysis = (item.get("analysis") or "").strip()
        timed = resolve_event_time(record, item)
        seen.add(name)
        cleaned.append(
            {
                "type": name,
                "definition": name,
                "title": item.get("title") or spec.get("title") or name,
                "start_ms": timed["start_ms"],
                "end_ms": timed["end_ms"],
                "clock": timed["clock"],
                "end_clock": timed["end_clock"],
                "seek_s": timed["seek_s"],
                "cell": timed["cell"],
                "confidence": item.get("confidence") or 0.6,
                "source": item.get("source") or "gemini",
                "summary": summary,
                "analysis": analysis,
                "important": important,
                "transcript": (record.get("transcript") or "").strip(),
                "context": item.get("context") or _one_line(spec.get("description") or ""),
                "people_ids": [str(pid) for pid in (item.get("people_ids") or []) if pid],
                "vehicle_ids": [str(vid) for vid in (item.get("vehicle_ids") or []) if vid],
                "evidence": evidence,
            }
        )
    return cleaned


def _canonical_name(name: str, definitions: dict) -> str:
    if not name:
        return name
    if name in definitions:
        return name
    for key, spec in definitions.items():
        aliases = [_slug(alias) for alias in (spec.get("aliases") or [])]
        if name in aliases:
            return key
        if name == _slug(spec.get("title") or ""):
            return key
    return name


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text()) or {}
    return payload if isinstance(payload, dict) else {}


def _event_id(item: dict) -> str:
    raw = item.get("type") or item.get("definition") or item.get("id") or ""
    name = _slug(str(raw))
    if name == "event":
        name = _slug(str(item.get("definition") or item.get("id") or ""))
    return name


def _slug(value: str) -> str:
    return _SLUG_RE.sub("_", value.strip().lower()).strip("_")[:48]


def _one_line(value: str) -> str:
    return " ".join(value.split())
