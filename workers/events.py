from __future__ import annotations

import os
import re
import threading
from pathlib import Path

import yaml

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
    record["event_types"] = [item["definition"] for item in events]
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
                "definition": name,
                "title": spec.get("title") or name,
                "start_ms": record.get("start_ms"),
                "end_ms": record.get("end_ms"),
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
        events.append(
            {
                "definition": name,
                "title": spec.get("title") or name,
                "start_ms": record.get("start_ms"),
                "end_ms": record.get("end_ms"),
                "confidence": 0.6,
                "source": "local_signals",
                "context": _one_line(spec.get("description") or ""),
                "evidence": [{"modality": "signal", "value": name}],
            }
        )
    return events


def persist_new_definitions(proposals: list[dict]) -> list[str]:
    added = []
    if not proposals:
        return added
    with _LOCK:
        core = _read_yaml(definition_path())
        learned = _read_yaml(learned_path())
        for raw in proposals:
            spec = _proposal_to_spec(raw)
            if not spec:
                continue
            name = spec.pop("_id")
            if name in core or name in learned:
                continue
            learned[name] = spec
            added.append(name)
        if added:
            path = learned_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(learned, sort_keys=False) + "\n")
    return added


def _proposal_to_spec(raw: dict) -> dict | None:
    name = _slug(raw.get("id") or raw.get("definition") or raw.get("title") or "")
    if not name or _BLOCKED.search(name):
        return None
    description = (raw.get("description") or raw.get("context") or "").strip()
    if len(description) < 12:
        return None
    phrases = [str(item).strip() for item in (raw.get("transcript_any") or []) if str(item).strip()]
    return {
        "_id": name,
        "type": "event",
        "title": (raw.get("title") or name.replace("_", " ")).strip()[:80],
        "description": description[:600],
        "how_to_confirm": (raw.get("how_to_confirm") or "").strip()[:400],
        "transcript_any": phrases[:8],
        "source": "gemini_learned",
    }


def _normalize_events(record: dict, events: list[dict]) -> list[dict]:
    definitions = load_definitions()
    cleaned = []
    seen = set()
    for item in events:
        name = _canonical_name(_slug(item.get("definition") or item.get("id") or ""), definitions)
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
        seen.add(name)
        cleaned.append(
            {
                "definition": name,
                "title": item.get("title") or spec.get("title") or name,
                "start_ms": item.get("start_ms", record.get("start_ms")),
                "end_ms": item.get("end_ms", record.get("end_ms")),
                "confidence": item.get("confidence") or 0.6,
                "source": item.get("source") or "gemini",
                "summary": (item.get("summary") or item.get("context") or "").strip(),
                "analysis": (item.get("analysis") or "").strip(),
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


def _slug(value: str) -> str:
    return _SLUG_RE.sub("_", value.strip().lower()).strip("_")[:48]


def _one_line(value: str) -> str:
    return " ".join(value.split())
