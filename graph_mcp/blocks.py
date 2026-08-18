from __future__ import annotations

from workers.clock import parse_clock
from workers.events import resolve_event_query

BLOCKS = {
    "events": {
        "kind": "source",
        "title": "Start from events",
        "use": "First block for Miranda, DUI, arrests, stops.",
        "params": [],
        "binds": "e",
    },
    "people": {
        "kind": "source",
        "title": "Start from people",
        "use": "Officers, civilians, potential suspects.",
        "params": [],
        "binds": "p",
    },
    "vehicles": {
        "kind": "source",
        "title": "Start from vehicles",
        "use": "Cars by color or plate.",
        "params": [],
        "binds": "v",
    },
    "objects": {
        "kind": "source",
        "title": "Start from objects",
        "use": "Bottle, bag, phone.",
        "params": [],
        "binds": "o",
    },
    "plates": {
        "kind": "source",
        "title": "Start from plates",
        "use": "License plate text.",
        "params": [],
        "binds": "pl",
    },
    "clips": {
        "kind": "source",
        "title": "Start from clips",
        "use": "Transcript / seek windows.",
        "params": [],
        "binds": "c",
    },
    "videos": {
        "kind": "source",
        "title": "Start from videos",
        "use": "List ingested files.",
        "params": [],
        "binds": "vid",
    },
    "topic": {
        "kind": "filter",
        "title": "Filter by definition topic",
        "use": "dui, miranda rights, traffic stop, arrest. Resolves aliases across videos.",
        "params": ["query"],
        "needs": "e",
    },
    "event_type": {
        "kind": "filter",
        "title": "Filter by Event.type",
        "use": "miranda_warning, field_sobriety_test, arrest.",
        "params": ["event_type"],
        "needs": "e",
    },
    "video": {
        "kind": "filter",
        "title": "Limit to one video",
        "use": "video_id=video_1. Omit to search every file.",
        "params": ["video_id"],
        "needs": "any",
    },
    "timeframe": {
        "kind": "filter",
        "title": "Event start in a clock window",
        "use": "start=01:00 end=15:00",
        "params": ["start", "end"],
        "needs": "e",
    },
    "at_time": {
        "kind": "filter",
        "title": "Events near a clock",
        "use": "timestamp=01:56",
        "params": ["timestamp"],
        "needs": "e",
    },
    "role": {
        "kind": "filter",
        "title": "Person role",
        "use": "officer | civilian | potential_suspect",
        "params": ["role"],
        "needs": "p",
    },
    "potential_suspect": {
        "kind": "filter",
        "title": "Potential suspects only",
        "use": "Scene role, not a charge.",
        "params": [],
        "needs": "p",
    },
    "is_cop": {
        "kind": "filter",
        "title": "Uniformed officers only",
        "use": "Appearance, not identity.",
        "params": [],
        "needs": "p",
    },
    "clothes": {
        "kind": "filter",
        "title": "Clothes contains",
        "use": "white sweatshirt, POLICE",
        "params": ["clothes"],
        "needs": "p",
    },
    "person_id": {
        "kind": "filter",
        "title": "One person",
        "use": "person_3",
        "params": ["person_id"],
        "needs": "p",
    },
    "vehicle_color": {
        "kind": "filter",
        "title": "Vehicle color",
        "use": "grey, white",
        "params": ["color"],
        "needs": "v",
    },
    "plate": {
        "kind": "filter",
        "title": "Plate text",
        "use": "Partial plate.",
        "params": ["text"],
        "needs": "pl",
    },
    "object_label": {
        "kind": "filter",
        "title": "Object label",
        "use": "bottle, bag",
        "params": ["label"],
        "needs": "o",
    },
    "transcript": {
        "kind": "filter",
        "title": "Transcript contains",
        "use": "under arrest, Miranda",
        "params": ["phrase"],
        "needs": "c",
    },
    "with_clip": {
        "kind": "join",
        "title": "Attach clip",
        "use": "Adds clip id, uri, clocks.",
        "params": [],
        "needs": "e",
    },
    "with_people": {
        "kind": "join",
        "title": "Attach people on the event",
        "use": "INVOLVES.role + clothes.",
        "params": [],
        "needs": "e",
    },
    "with_vehicles": {
        "kind": "join",
        "title": "Attach vehicles on the event",
        "use": "Cars involved.",
        "params": [],
        "needs": "e",
    },
    "with_objects": {
        "kind": "join",
        "title": "Attach objects on the event",
        "use": "Bottle, bag, etc.",
        "params": [],
        "needs": "e",
    },
    "with_topic": {
        "kind": "join",
        "title": "Attach topic names",
        "use": "dui, miranda on each row.",
        "params": [],
        "needs": "e",
    },
    "with_events": {
        "kind": "join",
        "title": "Attach events for a person/object/vehicle",
        "use": "After people, objects, or vehicles.",
        "params": [],
        "needs": "p|o|v",
    },
    "involving_person": {
        "kind": "join",
        "title": "Events that involve a person",
        "use": "person_id=person_3 on an events source.",
        "params": ["person_id"],
        "needs": "e",
    },
    "involving_object": {
        "kind": "join",
        "title": "Events that involve an object label",
        "use": "label=bottle",
        "params": ["label"],
        "needs": "e",
    },
    "continues": {
        "kind": "join",
        "title": "Event CONTINUES chains",
        "use": "One stop spanning clips.",
        "params": [],
        "needs": "e",
    },
    "return_events": {
        "kind": "output",
        "title": "Return event rows",
        "use": "video, clocks, summary, joined people/clip.",
        "params": [],
    },
    "return_people": {
        "kind": "output",
        "title": "Return person rows",
        "use": "Role, clothes, snapshot.",
        "params": [],
    },
    "return_vehicles": {
        "kind": "output",
        "title": "Return vehicles",
        "use": "Color and plate.",
        "params": [],
    },
    "return_plates": {"kind": "output", "title": "Return plates", "use": "Plate text.", "params": []},
    "return_objects": {"kind": "output", "title": "Return objects", "use": "Label and distinctive.", "params": []},
    "return_clips": {"kind": "output", "title": "Return clips", "use": "Transcript window.", "params": []},
    "return_videos": {"kind": "output", "title": "Return videos", "use": "Ingested files.", "params": []},
    "count": {
        "kind": "output",
        "title": "Count rows",
        "use": "How many matched.",
        "params": [],
    },
    "count_by_type": {
        "kind": "output",
        "title": "Count events by type",
        "use": "All Miranda / arrest totals.",
        "params": [],
        "needs": "e",
    },
    "count_by_video": {
        "kind": "output",
        "title": "Count events by video",
        "use": "Per-file totals.",
        "params": [],
        "needs": "e",
    },
}

RECIPES = {
    "all_dui": {
        "title": "All DUI / field sobriety across videos",
        "blocks": [
            {"block": "events"},
            {"block": "topic", "query": "dui"},
            {"block": "with_clip"},
            {"block": "with_people"},
            {"block": "with_topic"},
            {"block": "return_events"},
        ],
    },
    "all_miranda": {
        "title": "All Miranda readings across videos",
        "blocks": [
            {"block": "events"},
            {"block": "topic", "query": "miranda"},
            {"block": "with_clip"},
            {"block": "with_people"},
            {"block": "with_topic"},
            {"block": "return_events"},
        ],
    },
    "all_arrests": {
        "title": "All arrests across videos",
        "blocks": [
            {"block": "events"},
            {"block": "topic", "query": "arrest"},
            {"block": "with_clip"},
            {"block": "with_people"},
            {"block": "return_events"},
        ],
    },
    "all_stops": {
        "title": "All traffic stops across videos",
        "blocks": [
            {"block": "events"},
            {"block": "topic", "query": "traffic stop"},
            {"block": "with_clip"},
            {"block": "with_people"},
            {"block": "return_events"},
        ],
    },
    "events_in_timeframe": {
        "title": "Events in a clock window",
        "blocks": [
            {"block": "events"},
            {"block": "timeframe"},
            {"block": "with_clip"},
            {"block": "with_people"},
            {"block": "return_events"},
        ],
    },
    "count_events": {
        "title": "Count events by type",
        "blocks": [{"block": "events"}, {"block": "count_by_type"}],
    },
    "suspects": {
        "title": "Potential suspects with snapshots",
        "blocks": [
            {"block": "people"},
            {"block": "potential_suspect"},
            {"block": "with_events"},
            {"block": "return_people"},
        ],
    },
    "person_timeline": {
        "title": "One person's events over time",
        "blocks": [
            {"block": "people"},
            {"block": "person_id"},
            {"block": "with_events"},
            {"block": "return_people"},
        ],
    },
    "objects": {
        "title": "Objects and the events they appear in",
        "blocks": [
            {"block": "objects"},
            {"block": "with_events"},
            {"block": "return_objects"},
        ],
    },
    "list_videos": {
        "title": "Ingested videos",
        "blocks": [{"block": "videos"}, {"block": "return_videos"}],
    },
}


def clock_seconds(value) -> float | None:
    parsed = parse_clock(value)
    if parsed is None:
        return None
    if parsed < 1000 and str(value).isdigit():
        return float(parsed)
    return parsed / 1000.0


def catalog() -> dict:
    return {
        "how": (
            "Put blocks together in order: one source, then filters, then joins, then one output. "
            "Call preview_blocks to see Cypher, run_blocks to execute. "
            "Or run_recipe with a named pipeline like all_dui / all_miranda."
        ),
        "example": [
            {"block": "events"},
            {"block": "topic", "query": "dui"},
            {"block": "timeframe", "start": "01:00", "end": "20:00"},
            {"block": "with_people"},
            {"block": "with_clip"},
            {"block": "return_events"},
        ],
        "blocks": BLOCKS,
        "recipes": RECIPES,
    }


def parse_pipeline(raw) -> list[dict]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [_step(item) for item in raw]
    if isinstance(raw, dict):
        if raw.get("recipe"):
            return recipe_steps(raw["recipe"], raw)
        if raw.get("block"):
            return [_step(raw)]
        if raw.get("blocks"):
            return [_step(item) for item in raw["blocks"]]
    text = str(raw).strip()
    if text.startswith("[") or text.startswith("{"):
        import json

        data = json.loads(text)
        return parse_pipeline(data)
    if "|" in text:
        steps = []
        for part in text.split("|"):
            part = part.strip()
            if not part:
                continue
            bits = _split_token(part)
            if bits and bits[0] in RECIPES:
                steps.extend(recipe_steps(bits[0], _kv_pairs(bits[1:])))
            else:
                steps.append(_parse_token(part))
        return steps
    bits = _split_token(text)
    if bits and bits[0] in RECIPES:
        extra = _kv_pairs(bits[1:])
        return recipe_steps(bits[0], extra)
    if text in RECIPES:
        return recipe_steps(text, {})
    return [_parse_token(text)]


def recipe_steps(name: str, extra: dict) -> list[dict]:
    recipe = RECIPES.get(name)
    if not recipe:
        raise ValueError(f"Unknown recipe {name}. Try: {', '.join(RECIPES)}")
    steps = [dict(item) for item in recipe["blocks"]]
    skip = {"recipe", "block", "blocks", "title", "limit", "name"}
    for key, value in extra.items():
        if key in skip or value in (None, ""):
            continue
        applied = False
        for step in steps:
            spec = BLOCKS.get(step["block"]) or {}
            if key in (spec.get("params") or []):
                step[key] = value
                applied = True
                break
        if not applied:
            _inject_param(steps, key, value)
    return steps


def assemble(steps: list[dict]) -> tuple[str, dict, list[str]]:
    ctx = {
        "source": None,
        "bound": set(),
        "match": [],
        "optional": [],
        "where": [],
        "params": {},
        "joins": set(),
        "output": None,
        "order": None,
    }
    names = []
    for step in steps:
        name = step.get("block") or ""
        if name not in BLOCKS:
            raise ValueError(f"Unknown block {name}")
        names.append(name)
        _apply(name, step, ctx)
    if not ctx["source"]:
        raise ValueError("Pipeline needs a source block first: events, people, vehicles, objects, plates, clips, videos.")
    if not ctx["output"]:
        _apply(_default_output(ctx["source"]), {}, ctx)
    cypher = _render(ctx)
    return cypher, ctx["params"], names


def _apply(name: str, args: dict, ctx: dict) -> None:
    spec = BLOCKS[name]
    kind = spec["kind"]
    if kind == "source":
        if ctx["source"]:
            raise ValueError("Only one source block. Start with events or people, then add filters.")
        ctx["source"] = name
        _source(name, ctx)
        return
    if kind == "filter":
        _filter(name, args, ctx)
        return
    if kind == "join":
        _join(name, args, ctx)
        return
    if kind == "output":
        ctx["output"] = name
        return


def _source(name: str, ctx: dict) -> None:
    mapping = {
        "events": ("MATCH (e:Event)", "e"),
        "people": ("MATCH (p:Person)", "p"),
        "vehicles": ("MATCH (v:Vehicle)", "v"),
        "objects": ("MATCH (o:Object)", "o"),
        "plates": ("MATCH (pl:Plate)", "pl"),
        "clips": ("MATCH (c:Clip)", "c"),
        "videos": ("MATCH (vid:Video)", "vid"),
    }
    clause, bind = mapping[name]
    ctx["match"].append(clause)
    ctx["bound"].add(bind)


def _filter(name: str, args: dict, ctx: dict) -> None:
    params = ctx["params"]
    if name == "topic":
        _need(ctx, "e")
        resolved = resolve_event_query(str(args.get("query") or args.get("topic") or ""))
        types = resolved.get("event_types") or []
        if not types:
            raise ValueError(f"No event definition for {args.get('query')!r}")
        params["event_types"] = types
        params["_resolved"] = resolved
        ctx["where"].append("e.type IN $event_types")
        return
    if name == "event_type":
        _need(ctx, "e")
        params["event_type"] = str(args.get("event_type") or "").strip()
        ctx["where"].append("e.type = $event_type")
        return
    if name == "video":
        video_id = str(args.get("video_id") or "").strip()
        params["video_id"] = video_id
        if "e" in ctx["bound"]:
            ctx["where"].append("e.video_id = $video_id")
        elif "p" in ctx["bound"]:
            ctx["where"].append("p.video_id = $video_id")
        elif "v" in ctx["bound"]:
            ctx["where"].append("v.video_id = $video_id")
        elif "o" in ctx["bound"]:
            ctx["where"].append("o.video_id = $video_id")
        elif "c" in ctx["bound"]:
            ctx["where"].append("c.video_id = $video_id")
        elif "vid" in ctx["bound"]:
            ctx["where"].append("vid.id = $video_id")
        return
    if name == "timeframe":
        _need(ctx, "e")
        params["start_s"] = _clock(args.get("start") or args.get("start_timestamp"), "start")
        params["end_s"] = _clock(args.get("end") or args.get("end_timestamp"), "end")
        ctx["where"].append("e.seek_s >= $start_s AND e.seek_s <= $end_s")
        return
    if name == "at_time":
        _need(ctx, "e")
        params["seek_s"] = _clock(args.get("timestamp") or args.get("at"), "timestamp")
        ctx["where"].append("abs(e.seek_s - $seek_s) <= 30")
        return
    if name == "role":
        _need(ctx, "p")
        params["role"] = str(args.get("role") or "").strip()
        ctx["where"].append("p.role = $role")
        return
    if name == "potential_suspect":
        _need(ctx, "p")
        ctx["where"].append("p.potential_suspect = true")
        return
    if name == "is_cop":
        _need(ctx, "p")
        ctx["where"].append("p.is_cop = true")
        return
    if name == "clothes":
        _need(ctx, "p")
        params["clothes"] = str(args.get("clothes") or "").strip()
        ctx["where"].append("toLower(p.clothes) CONTAINS toLower($clothes)")
        return
    if name == "person_id":
        _need(ctx, "p")
        params["person_id"] = str(args.get("person_id") or "").strip()
        ctx["where"].append("(p.local_id = $person_id OR p.id ENDS WITH $person_id)")
        return
    if name == "vehicle_color":
        _need(ctx, "v")
        params["color"] = str(args.get("color") or "").strip()
        ctx["where"].append("toLower(v.color) CONTAINS toLower($color)")
        return
    if name == "plate":
        params["plate"] = str(args.get("text") or args.get("plate") or "").upper()
        if "pl" in ctx["bound"]:
            ctx["where"].append("pl.text CONTAINS $plate")
        elif "v" in ctx["bound"]:
            ctx["where"].append("v.plate CONTAINS $plate")
        else:
            raise ValueError("plate filter needs plates or vehicles source")
        return
    if name == "object_label":
        _need(ctx, "o")
        params["label"] = str(args.get("label") or "").strip()
        ctx["where"].append("toLower(o.label) CONTAINS toLower($label)")
        return
    if name == "transcript":
        _need(ctx, "c")
        params["phrase"] = str(args.get("phrase") or "").strip()
        ctx["where"].append("toLower(c.transcript) CONTAINS toLower($phrase)")
        return
    raise ValueError(f"Unhandled filter {name}")


def _join(name: str, args: dict, ctx: dict) -> None:
    if name in ctx["joins"]:
        return
    ctx["joins"].add(name)
    if name == "with_clip":
        _need(ctx, "e")
        ctx["optional"].append("OPTIONAL MATCH (c:Clip)-[:CONTAINS]->(e)")
        ctx["bound"].add("c")
        return
    if name == "with_people":
        _need(ctx, "e")
        ctx["optional"].append("OPTIONAL MATCH (e)-[rpe:INVOLVES]->(p:Person)")
        ctx["bound"].add("p")
        return
    if name == "with_vehicles":
        _need(ctx, "e")
        ctx["optional"].append("OPTIONAL MATCH (e)-[:INVOLVES]->(v:Vehicle)")
        ctx["bound"].add("v")
        return
    if name == "with_objects":
        _need(ctx, "e")
        ctx["optional"].append("OPTIONAL MATCH (e)-[:INVOLVES]->(o:Object)")
        ctx["bound"].add("o")
        return
    if name == "with_topic":
        _need(ctx, "e")
        ctx["optional"].append("OPTIONAL MATCH (e)-[:INSTANCE_OF]->(et:EventType)-[:IN_TOPIC]->(topic:Topic)")
        ctx["bound"].add("topic")
        return
    if name == "with_events":
        if "p" in ctx["bound"]:
            ctx["optional"].append("OPTIONAL MATCH (e:Event)-[rpe:INVOLVES]->(p)")
        elif "o" in ctx["bound"]:
            ctx["optional"].append("OPTIONAL MATCH (e:Event)-[:INVOLVES]->(o)")
        elif "v" in ctx["bound"]:
            ctx["optional"].append("OPTIONAL MATCH (e:Event)-[:INVOLVES]->(v)")
        else:
            raise ValueError("with_events needs people, objects, or vehicles")
        ctx["bound"].add("e")
        return
    if name == "involving_person":
        _need(ctx, "e")
        ctx["params"]["person_id"] = str(args.get("person_id") or "").strip()
        ctx["match"].append("MATCH (e)-[rpe:INVOLVES]->(p:Person)")
        ctx["where"].append("(p.local_id = $person_id OR p.id ENDS WITH $person_id)")
        ctx["bound"].add("p")
        return
    if name == "involving_object":
        _need(ctx, "e")
        ctx["params"]["label"] = str(args.get("label") or "").strip()
        ctx["match"].append("MATCH (e)-[:INVOLVES]->(o:Object)")
        ctx["where"].append("toLower(o.label) CONTAINS toLower($label)")
        ctx["bound"].add("o")
        return
    if name == "continues":
        _need(ctx, "e")
        ctx["optional"].append("OPTIONAL MATCH (e)-[:CONTINUES]->(nxt:Event)")
        ctx["bound"].add("nxt")
        return
    raise ValueError(f"Unhandled join {name}")


def _render(ctx: dict) -> str:
    parts = list(ctx["match"])
    if ctx["where"]:
        parts.append("WHERE " + " AND ".join(ctx["where"]))
    parts.extend(ctx["optional"])
    output = ctx["output"]
    with_bits = []
    if output == "return_events":
        keys = ["e"]
        if "c" in ctx["bound"]:
            keys.append("c")
        collect = []
        if "p" in ctx["bound"]:
            collect.append(
                "collect(DISTINCT {id: p.local_id, role: rpe.role, clothes: p.clothes, potential_suspect: p.potential_suspect}) AS people"
            )
        if "v" in ctx["bound"]:
            collect.append("collect(DISTINCT {id: v.id, color: v.color, plate: v.plate}) AS vehicles")
        if "o" in ctx["bound"]:
            collect.append("collect(DISTINCT {id: o.local_id, label: o.label}) AS objects")
        if "topic" in ctx["bound"]:
            collect.append("collect(DISTINCT topic.id) AS topics")
        if collect:
            parts.append("WITH " + ", ".join(keys + collect))
        returns = [
            "e.video_id AS video_id",
            "e.source_name AS source_name",
            "e.type AS event_type",
            "e.start_timestamp AS start_timestamp",
            "e.end_timestamp AS end_timestamp",
            "e.seek_s AS seek_s",
            "e.summary AS summary",
        ]
        if "c" in ctx["bound"]:
            returns.extend(
                [
                    "c.local_id AS clip",
                    "c.clip_uri AS clip_uri",
                    "c.splice_uri AS splice_uri",
                    "c.tagged_splice_uri AS tagged_splice_uri",
                ]
            )
        if "p" in ctx["bound"]:
            returns.append("people")
        if "v" in ctx["bound"]:
            returns.append("vehicles")
        if "o" in ctx["bound"]:
            returns.append("objects")
        if "topic" in ctx["bound"]:
            returns.append("topics")
        if "nxt" in ctx["bound"]:
            returns.append("nxt.start_timestamp AS continues_to")
        parts.append("RETURN " + ",\n       ".join(returns))
        parts.append("ORDER BY e.video_id, e.seek_s")
        return "\n".join(parts)
    if output == "return_people":
        if "e" in ctx["bound"]:
            parts.append(
                "WITH p, collect(DISTINCT {type: e.type, start_timestamp: e.start_timestamp, video_id: e.video_id}) AS events"
            )
            with_bits = ["events"]
        returns = [
            "p.local_id AS person_id",
            "p.video_id AS video_id",
            "p.role AS role",
            "p.is_cop AS is_cop",
            "p.potential_suspect AS potential_suspect",
            "p.clothes AS clothes",
            "p.description AS description",
            "p.suspect_snapshot AS snapshot",
            "p.suspect_at AS suspect_at",
            "p.suspect_event AS suspect_event",
        ]
        returns.extend(with_bits)
        parts.append("RETURN " + ",\n       ".join(returns))
        parts.append("ORDER BY p.video_id, p.local_id")
        return "\n".join(parts)
    if output == "return_plates":
        if "v" not in ctx["bound"]:
            parts.append("OPTIONAL MATCH (v:Vehicle)-[:HAS_PLATE]->(pl)")
        parts.append(
            "RETURN pl.text AS plate, pl.confidence AS confidence, pl.video_id AS video_id, v.color AS vehicle_color, v.id AS vehicle_id"
        )
        return "\n".join(parts)
    if output == "return_vehicles":
        parts.append(
            "RETURN v.id AS vehicle_id, v.video_id AS video_id, v.color AS color, v.plate AS plate, v.analysis AS analysis"
        )
        return "\n".join(parts)
    if output == "return_objects":
        if "e" in ctx["bound"]:
            parts.append(
                "WITH o, collect(DISTINCT {type: e.type, start_timestamp: e.start_timestamp, video_id: e.video_id}) AS events"
            )
            parts.append(
                "RETURN o.local_id AS object_id, o.video_id AS video_id, o.label AS label, o.distinctive AS distinctive, events"
            )
        else:
            parts.append(
                "RETURN o.local_id AS object_id, o.video_id AS video_id, o.label AS label, o.distinctive AS distinctive"
            )
        return "\n".join(parts)
    if output == "return_clips":
        parts.append(
            "RETURN c.video_id AS video_id, c.local_id AS clip, c.start_timestamp AS start_timestamp, "
            "c.end_timestamp AS end_timestamp, c.summary AS summary, c.clip_uri AS clip_uri, "
            "c.splice_uri AS splice_uri, c.tagged_splice_uri AS tagged_splice_uri, c.audio_uri AS audio_uri"
        )
        parts.append("ORDER BY c.video_id, c.index")
        return "\n".join(parts)
    if output == "return_videos":
        parts.append("OPTIONAL MATCH (vid)-[:HAS_PERSON]->(p:Person)")
        parts.append("WITH vid, count(DISTINCT p) AS people")
        parts.append(
            "RETURN vid.id AS video_id, vid.source_name AS source_name, vid.duration_s AS duration_s, "
            "vid.clip_count AS clip_count, vid.status AS status, people"
        )
        return "\n".join(parts)
    if output == "count":
        parts.append("RETURN count(*) AS n")
        return "\n".join(parts)
    if output == "count_by_type":
        _need(ctx, "e")
        parts.append("RETURN e.type AS event_type, count(*) AS n")
        parts.append("ORDER BY n DESC")
        return "\n".join(parts)
    if output == "count_by_video":
        _need(ctx, "e")
        parts.append("RETURN e.video_id AS video_id, e.source_name AS source_name, e.type AS event_type, count(*) AS n")
        parts.append("ORDER BY e.video_id, n DESC")
        return "\n".join(parts)
    raise ValueError(f"Unhandled output {output}")


def _default_output(source: str) -> str:
    return {
        "events": "return_events",
        "people": "return_people",
        "vehicles": "return_vehicles",
        "objects": "return_objects",
        "plates": "return_plates",
        "clips": "return_clips",
        "videos": "return_videos",
    }[source]


def _need(ctx: dict, bind: str) -> None:
    if bind not in ctx["bound"]:
        raise ValueError(f"Block needs {bind} in scope. Start with the matching source or join.")


def _clock(value, name: str) -> float:
    seconds = clock_seconds(value)
    if seconds is None:
        raise ValueError(f"{name} must look like 01:56")
    return seconds


def _step(item) -> dict:
    if isinstance(item, str):
        return _parse_token(item)
    if not isinstance(item, dict) or not item.get("block"):
        raise ValueError("Each step needs {\"block\": \"name\", ...params}")
    return dict(item)


def _split_token(token: str) -> list[str]:
    import shlex

    try:
        return shlex.split(token)
    except ValueError:
        return token.split()


def _kv_pairs(bits: list[str]) -> dict:
    extra = {}
    for piece in bits:
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        extra[key.strip()] = value.strip().strip('"').strip("'")
    return extra


def _parse_token(token: str) -> dict:
    bits = _split_token(token)
    if not bits:
        raise ValueError("Empty pipeline step")
    name = bits[0]
    if name not in BLOCKS:
        raise ValueError(f"Unknown block {name}. See list_query_blocks.")
    step = {"block": name, **_kv_pairs(bits[1:])}
    if len(bits) > 1 and "=" not in bits[1] and BLOCKS[name].get("params"):
        step[BLOCKS[name]["params"][0]] = bits[1]
    return step


def _source_bind(steps: list[dict]) -> str | None:
    for step in steps:
        spec = BLOCKS.get(step.get("block") or "")
        if spec and spec["kind"] == "source":
            return spec.get("binds")
    return None


def _needs_ok(needs: str, bind: str | None) -> bool:
    if not needs or needs == "any":
        return True
    if not bind:
        return False
    return bind == needs or bind in needs.split("|")


def _inject_param(steps: list[dict], key: str, value) -> None:
    bind = _source_bind(steps)
    for fname, spec in BLOCKS.items():
        params = spec.get("params") or []
        if key not in params:
            continue
        if spec["kind"] not in ("filter", "join"):
            continue
        if not _needs_ok(str(spec.get("needs") or ""), bind):
            continue
        existing = next((step for step in steps if step.get("block") == fname), None)
        if existing is not None:
            existing[key] = value
            return
        insert_at = len(steps)
        if steps and BLOCKS.get(steps[-1].get("block") or "", {}).get("kind") == "output":
            insert_at = len(steps) - 1
        steps.insert(insert_at, {"block": fname, key: value})
        return
    raise ValueError(f"No block in this pipeline accepts {key}={value!r}")


def public_params(params: dict) -> dict:
    return {key: value for key, value in params.items() if not str(key).startswith("_")}
