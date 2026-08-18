from __future__ import annotations

import base64
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from workers.clips import load_clip_records, save_clip_record, save_video_status
from workers.clock import cell_timeline, format_clock, transcript_timeline
from workers.events import attach_event_metadata, catalog_for_prompt, events_from_local

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def scan_video_events(video: dict) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    records = sorted(
        load_clip_records(video["video_id"]),
        key=lambda item: int(item.get("index") or 0),
    )
    workers = max(1, min(int(os.environ.get("SUMMARY_CONCURRENCY", "2")), len(records) or 1))
    if api_key and len(records) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(scan_clip, record, api_key) for record in records]
            for future in as_completed(futures):
                future.result()
    else:
        for record in records:
            scan_clip(record, api_key)
    for record in records:
        save_clip_record(video["video_id"], record)
    video = {**video, "status": "scanned"}
    return save_video_status(video, "scanned")


def scan_clip(record: dict, api_key: str = "") -> dict:
    local = events_from_local(record)
    events = local
    if api_key:
        try:
            parsed = _vlm_scan(record, api_key)
            events = parsed.get("events") or local
            record["scan_source"] = "vlm"
        except Exception as exc:
            record["scan_error"] = str(exc)[:500]
            record["scan_source"] = "local"
    else:
        record["scan_source"] = "local"
    attach_event_metadata(record, events)
    record["scan_status"] = "done"
    from workers.fingerprint import is_key_clip

    record["key_clip"] = is_key_clip(record)
    return record


def _vlm_scan(record: dict, api_key: str) -> dict:
    start = int(record.get("start_ms") or 0)
    end = int(record.get("end_ms") or start)
    prompt = (
        "Quick scan of a police bodycam/dashcam splice. Return JSON only. "
        "Observable scene labels only. No names, guilt, or charges.\n"
        "Each event MUST have the VIDEO time it starts, not the clip start. "
        "Use start_clock like 03:12 and start_ms as milliseconds from the beginning of the full video. "
        "Use the cell clocks burned into the splice (cell_00 02:50) and the transcript times.\n\n"
        f"Event catalog:\n{catalog_for_prompt()}\n\n"
        f"Clip {record.get('id')} covers {format_clock(start)}–{format_clock(end)} "
        f"({start}–{end} ms).\n"
        f"Splice cells:\n{cell_timeline(record)}\n\n"
        f"Transcript:\n{transcript_timeline(record)}\n\n"
        f"Keyword hits: {json.dumps(record.get('keyword_hits') or [])}\n"
        f"Signals: {json.dumps(record.get('signals') or {})}\n"
        "If nothing in the catalog happened, return {\"events\": []}.\n"
        'Schema: {"events":[{"type":str,"start_ms":int,"end_ms":int,'
        '"start_clock":str,"end_clock":str,"cell":int,"confidence":float,'
        '"evidence":[{"modality":str,"value":str}]}]}'
    )
    content = [{"type": "text", "text": prompt}]
    splice = record.get("tagged_splice_uri") or record.get("splice_uri")
    if splice and Path(splice).exists():
        encoded = base64.b64encode(Path(splice).read_bytes()).decode()
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            }
        )
    payload = {
        "model": os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite"),
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:8080",
            "X-Title": "video-intel-scan",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        body = json.loads(response.read().decode())
    text = body["choices"][0]["message"]["content"]
    return json.loads(text)
