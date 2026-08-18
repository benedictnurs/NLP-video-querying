from __future__ import annotations

import json
import os
from pathlib import Path

from neo4j import GraphDatabase

from workers.clips import load_clip_records
from workers.clock import format_clock
from workers.events import load_definitions, load_topics
from workers.fingerprint import RARE_ON_ROAD, WATER_LABELS
from workers.ingest import load_registry, save_registry_entry
from workers.paths import persist_media_uri, video_work_dir


def write_video_graph(video: dict) -> dict:
    clips = sorted(
        load_clip_records(video["video_id"]),
        key=lambda item: (int(item.get("index") or 0), int(item.get("start_ms") or 0)),
    )
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    try:
        with driver.session() as session:
            _ensure_constraints(session)
            _upsert_event_types(session)
            _upsert_topics(session)
            for old_id in _previous_ids(video):
                if old_id != video["video_id"]:
                    _wipe_video_graph(session, old_id)
            session.run(
                """
                MERGE (v:Video {id: $video_id})
                SET v.source_name = $source_name,
                    v.source_path = $source_path,
                    v.original_uri = $original_uri,
                    v.audio_uri = $audio_uri,
                    v.processing_uri = $processing_uri,
                    v.duration_s = $duration_s,
                    v.fps = $fps,
                    v.width = $width,
                    v.height = $height,
                    v.size_bytes = $size_bytes,
                    v.ingested_at = $ingested_at,
                    v.clip_count = $clip_count,
                    v.status = 'graphed'
                """,
                video_id=video["video_id"],
                source_name=video.get("source_name"),
                source_path=persist_media_uri(video.get("source_path")),
                original_uri=persist_media_uri(video.get("original_uri")),
                audio_uri=persist_media_uri(video.get("audio_uri")),
                processing_uri=persist_media_uri(video.get("processing_uri")),
                duration_s=video.get("duration_s"),
                fps=video.get("fps"),
                width=video.get("width"),
                height=video.get("height"),
                size_bytes=video.get("size_bytes"),
                ingested_at=video.get("ingested_at"),
                clip_count=len(clips),
            )
            _reset_video_observations(session, video["video_id"])
            clip_ids = []
            for record in clips:
                record["video_id"] = video["video_id"]
                record["source_name"] = video.get("source_name") or ""
                clip_id = f"{video['video_id']}:{record['id']}"
                clip_ids.append(clip_id)
                _write_clip(session, video, record, clip_id)
                _replace_observations(session, clip_id, record)
                _clear_clip_types(session, clip_id)
                for event in record.get("events") or []:
                    _write_event(session, clip_id, record, event)
            _link_clip_chain(session, video["video_id"], clip_ids)
    finally:
        driver.close()

    video = {**video, "status": "graphed", "clip_count": len(clips)}
    (video_work_dir(video["video_id"]) / "ingest.json").write_text(json.dumps(video, indent=2) + "\n")
    source_name = video.get("source_name")
    if source_name:
        registry = load_registry()
        fingerprint = (registry.get(source_name) or {}).get("fingerprint", "")
        save_registry_entry(
            source_name,
            video["video_id"],
            fingerprint,
            "graphed",
            video.get("size_bytes"),
        )
    return video


def _write_clip(session, video: dict, record: dict, clip_id: str) -> None:
    signals = record.get("signals") or {}
    session.run(
        """
        MATCH (v:Video {id: $video_id})
        MERGE (c:Clip {id: $clip_id})
        SET c.video_id = $video_id,
            c.local_id = $local_id,
            c.index = $index,
            c.start_timestamp = $start_timestamp,
            c.end_timestamp = $end_timestamp,
            c.transcript = $transcript,
            c.summary = $summary,
            c.summary_source = $summary_source,
            c.tags = $tags,
            c.object_labels = $object_labels,
            c.clothing_colors = $clothing_colors,
            c.people_descriptions = $people_descriptions,
            c.plates = $plates,
            c.keyword_phrases = $keyword_phrases,
            c.event_types = $event_types,
            c.event_evidence = $event_evidence,
            c.person_count = $person_count,
            c.vehicle_count = $vehicle_count,
            c.object_count = $object_count,
            c.loudness = $loudness,
            c.loud_impact = $loud_impact,
            c.yelling = $yelling,
            c.repeated_commands = $repeated_commands,
            c.needs_vision = $needs_vision,
            c.important = $important,
            c.analysis_status = $analysis_status,
            c.splice_uri = $splice_uri,
            c.tagged_splice_uri = $tagged_splice_uri,
            c.clip_uri = $clip_uri,
            c.audio_uri = $audio_uri
        REMOVE c.start_ms, c.end_ms, c.start_clock, c.end_clock
        """,
        video_id=video["video_id"],
        clip_id=clip_id,
        local_id=record["id"],
        index=record["index"],
        start_timestamp=format_clock(record.get("start_ms")),
        end_timestamp=format_clock(record.get("end_ms")),
        transcript=record.get("transcript") or "",
        summary=record.get("summary") or "",
        summary_source=record.get("summary_source"),
        tags=record.get("tags") or [],
        object_labels=record.get("object_labels") or [],
        clothing_colors=record.get("clothing_colors") or [],
        people_descriptions=record.get("people_descriptions") or [],
        plates=[item.get("text") if isinstance(item, dict) else item for item in (record.get("plates") or [])],
        keyword_phrases=[item.get("phrase") for item in (record.get("keyword_hits") or []) if item.get("phrase")],
        event_types=record.get("event_types") or [],
        event_evidence=record.get("event_evidence") or [],
        person_count=record.get("person_count") or 0,
        vehicle_count=record.get("vehicle_count") or 0,
        object_count=record.get("object_count") or 0,
        loudness=signals.get("loudness"),
        loud_impact=signals.get("loud_impact"),
        yelling=signals.get("yelling"),
        repeated_commands=signals.get("repeated_commands"),
        needs_vision=bool(record.get("needs_vision")),
        important=record.get("important") or [],
        analysis_status=record.get("analysis_status"),
        splice_uri=persist_media_uri(record.get("splice_uri")),
        tagged_splice_uri=persist_media_uri(record.get("tagged_splice_uri")),
        clip_uri=persist_media_uri(record.get("clip_uri")),
        audio_uri=persist_media_uri(record.get("audio_uri")),
    )


def _link_clip_chain(session, video_id: str, clip_ids: list[str]) -> None:
    session.run(
        """
        MATCH (c:Clip {video_id: $video_id})-[rel:NEXT]->(:Clip)
        DELETE rel
        """,
        video_id=video_id,
    )
    session.run(
        """
        MATCH (v:Video {id: $video_id})-[rel:STARTS_AT]->(:Clip)
        DELETE rel
        """,
        video_id=video_id,
    )
    if not clip_ids:
        return
    session.run(
        """
        MATCH (v:Video {id: $video_id})
        MATCH (first:Clip {id: $first_id})
        MERGE (v)-[:STARTS_AT]->(first)
        """,
        video_id=video_id,
        first_id=clip_ids[0],
    )
    pairs = [[left, right] for left, right in zip(clip_ids, clip_ids[1:])]
    if not pairs:
        return
    session.run(
        """
        UNWIND $pairs AS pair
        MATCH (prev:Clip {id: pair[0]})
        MATCH (nxt:Clip {id: pair[1]})
        MERGE (prev)-[:NEXT]->(nxt)
        """,
        pairs=pairs,
    )


def _previous_ids(video: dict) -> list[str]:
    ids = []
    for key in ("video_id", "previous_video_id"):
        value = video.get(key)
        if value and value not in ids:
            ids.append(value)
    source = video.get("source_name") or ""
    if source:
        from workers.ingest import make_video_id

        stem_id = make_video_id(Path(source))
        if stem_id not in ids:
            ids.append(stem_id)
    return ids


def _wipe_video_graph(session, video_id: str) -> None:
    session.run(
        """
        MATCH (n)
        WHERE n.video_id = $video_id
        DETACH DELETE n
        """,
        video_id=video_id,
    )
    session.run(
        """
        MATCH (n)
        WHERE (n:Video OR n:Clip OR n:Event) AND (n.id = $video_id OR n.id STARTS WITH $prefix)
        DETACH DELETE n
        """,
        video_id=video_id,
        prefix=f"{video_id}:",
    )


def _reset_video_observations(session, video_id: str) -> None:
    session.run(
        """
        MATCH (p:Person {video_id: $video_id})
        DETACH DELETE p
        """,
        video_id=video_id,
    )
    session.run(
        """
        MATCH (v:Vehicle {video_id: $video_id})
        DETACH DELETE v
        """,
        video_id=video_id,
    )
    session.run(
        """
        MATCH (o:Object {video_id: $video_id})
        DETACH DELETE o
        """,
        video_id=video_id,
    )
    session.run(
        """
        MATCH (p:Plate {video_id: $video_id})
        DETACH DELETE p
        """,
        video_id=video_id,
    )


def _replace_observations(session, clip_id: str, record: dict) -> None:
    session.run(
        """
        MATCH (c:Clip {id: $clip_id})-[rel:CONTAINS]->()
        DELETE rel
        """,
        clip_id=clip_id,
    )
    session.run(
        """
        MATCH (n)
        WHERE (n:Object OR n:Event OR n:Plate) AND n.id STARTS WITH $prefix
        DETACH DELETE n
        """,
        prefix=f"{clip_id}:",
    )
    video_id = record.get("video_id") or ""
    for entity in record.get("entities") or []:
        _write_entity(session, video_id, clip_id, entity)
    for plate in record.get("plates") or []:
        if isinstance(plate, dict) and plate.get("text"):
            _write_plate(session, video_id, clip_id, plate)


def _write_entity(session, video_id: str, clip_id: str, entity: dict) -> None:
    kind = entity.get("type") or "object"
    label = (entity.get("label") or "").lower()
    if label in RARE_ON_ROAD | WATER_LABELS:
        return
    if kind == "person":
        _write_person(session, video_id, clip_id, entity)
        return
    if kind == "vehicle":
        _write_vehicle(session, video_id, clip_id, entity)
        return
    entity_id = f"{video_id}:{entity.get('id') or entity.get('label') or 'object'}"
    session.run(
        """
        MATCH (c:Clip {id: $clip_id})
        MATCH (v:Video {id: $video_id})
        MERGE (n:Object {id: $entity_id})
        SET n.label = $label_name,
            n.video_id = $video_id,
            n.distinctive = $distinctive,
            n.local_id = $local_id
        MERGE (c)-[:CONTAINS]->(n)
        MERGE (v)-[:HAS_OBJECT]->(n)
        """,
        clip_id=clip_id,
        entity_id=entity_id,
        video_id=video_id,
        label_name=entity.get("label"),
        distinctive=entity.get("distinctive") or "",
        local_id=entity.get("id"),
    )


def _write_person(session, video_id: str, clip_id: str, entity: dict) -> None:
    local_id = entity.get("id") or "person"
    person_id = f"{video_id}:{local_id}"
    session.run(
        """
        MATCH (c:Clip {id: $clip_id})
        MATCH (v:Video {id: $video_id})
        MERGE (p:Person {id: $person_id})
        SET p.video_id = $video_id,
            p.local_id = $local_id,
            p.race = $race,
            p.gender = $gender,
            p.hair = $hair,
            p.glasses = $glasses,
            p.clothes = $clothes,
            p.shoes = $shoes,
            p.bag = $bag,
            p.distinctive = $distinctive,
            p.signature = $signature,
            p.description = $description,
            p.is_cop = $is_cop,
            p.role = $role,
            p.potential_suspect = $potential_suspect,
            p.suspect_reason = $suspect_reason,
            p.suspect_at = $suspect_at,
            p.suspect_event = $suspect_event,
            p.suspect_snapshot = $suspect_snapshot
        MERGE (c)-[:CONTAINS]->(p)
        MERGE (v)-[:HAS_PERSON]->(p)
        """,
        clip_id=clip_id,
        video_id=video_id,
        person_id=person_id,
        local_id=local_id,
        race=entity.get("race") or "unknown",
        gender=entity.get("gender") or "unknown",
        hair=entity.get("hair") or "unknown",
        glasses=entity.get("glasses") or "unknown",
        clothes=entity.get("clothes") or entity.get("clothing") or "",
        shoes=entity.get("shoes") or "",
        bag=entity.get("bag") or "",
        distinctive=entity.get("distinctive") or "",
        signature=entity.get("signature") or "",
        description=entity.get("description") or "",
        is_cop=entity.get("is_cop"),
        role=entity.get("role")
        or ("officer" if entity.get("is_cop") is True else "civilian"),
        potential_suspect=bool(entity.get("potential_suspect")),
        suspect_reason=entity.get("suspect_reason") or "",
        suspect_at=entity.get("suspect_at") or "",
        suspect_event=entity.get("suspect_event") or "",
        suspect_snapshot=entity.get("suspect_snapshot") or "",
    )


def _write_vehicle(session, video_id: str, clip_id: str, entity: dict) -> None:
    plate = entity.get("plate") or ""
    local_id = entity.get("id") or "vehicle"
    vehicle_id = f"{video_id}:{local_id}"
    session.run(
        """
        MATCH (c:Clip {id: $clip_id})
        MATCH (v:Video {id: $video_id})
        MERGE (car:Vehicle {id: $vehicle_id})
        SET car.video_id = $video_id,
            car.color = $color,
            car.plate = $plate,
            car.analysis = $analysis
        MERGE (c)-[:CONTAINS]->(car)
        MERGE (v)-[:HAS_VEHICLE]->(car)
        """,
        clip_id=clip_id,
        video_id=video_id,
        vehicle_id=vehicle_id,
        color=(entity.get("color") or "").strip().lower(),
        plate=plate,
        analysis=entity.get("analysis") or "",
    )


def _write_plate(session, video_id: str, clip_id: str, plate: dict) -> None:
    text = plate["text"]
    plate_id = f"{video_id}:plate:{text}"
    session.run(
        """
        MATCH (c:Clip {id: $clip_id})
        MATCH (vid:Video {id: $video_id})
        MERGE (p:Plate {id: $plate_id})
        SET p.text = $text,
            p.video_id = $video_id,
            p.confidence = $confidence,
            p.source = $source
        MERGE (c)-[:CONTAINS]->(p)
        MERGE (vid)-[:HAS_PLATE]->(p)
        """,
        clip_id=clip_id,
        video_id=video_id,
        plate_id=plate_id,
        text=text,
        confidence=plate.get("confidence"),
        source=plate.get("source") or "gemini",
    )
    vehicle_local = plate.get("vehicle_id") or ""
    if not vehicle_local:
        return
    graph_vehicle = (
        vehicle_local if str(vehicle_local).startswith(f"{video_id}:") else f"{video_id}:{vehicle_local}"
    )
    session.run(
        """
        MATCH (p:Plate {id: $plate_id})
        MATCH (v:Vehicle {id: $vehicle_id})
        MERGE (v)-[:HAS_PLATE]->(p)
        SET v.plate = $text
        """,
        plate_id=plate_id,
        vehicle_id=graph_vehicle,
        text=plate["text"],
    )


def _clear_clip_types(session, clip_id: str) -> None:
    session.run(
        """
        MATCH (c:Clip {id: $clip_id})-[rel:HAS_TYPE]->(:EventType)
        DELETE rel
        """,
        clip_id=clip_id,
    )


def _upsert_event_types(session) -> None:
    for name, spec in load_definitions().items():
        _merge_event_type(
            session,
            name,
            spec.get("title") or name.replace("_", " "),
            spec.get("description") or "",
            spec.get("aliases") or [],
            spec.get("topics") or [],
        )


def _upsert_topics(session) -> None:
    for topic_id, spec in load_topics().items():
        session.run(
            """
            MERGE (topic:Topic {id: $topic_id})
            SET topic.title = $title,
                topic.description = $description,
                topic.aliases = $aliases
            """,
            topic_id=topic_id,
            title=spec.get("title") or topic_id.replace("_", " "),
            description=" ".join(str(spec.get("description") or "").split()),
            aliases=list(spec.get("aliases") or []),
        )
        for type_id in spec.get("event_types") or []:
            session.run(
                """
                MERGE (t:EventType {id: $type_id})
                MERGE (topic:Topic {id: $topic_id})
                MERGE (t)-[:IN_TOPIC]->(topic)
                MERGE (topic)-[:BUNDLES]->(t)
                """,
                type_id=str(type_id),
                topic_id=topic_id,
            )


def _merge_event_type(session, type_id: str, title: str, description: str, aliases: list, topics: list | None = None) -> None:
    session.run(
        """
        MERGE (t:EventType {id: $type_id})
        SET t.title = $title,
            t.name = $title,
            t.description = $description,
            t.aliases = $aliases,
            t.topics = $topics
        """,
        type_id=type_id,
        title=title,
        description=" ".join(str(description).split()),
        aliases=list(aliases),
        topics=list(topics or []),
    )
    for topic_id in topics or []:
        session.run(
            """
            MERGE (t:EventType {id: $type_id})
            MERGE (topic:Topic {id: $topic_id})
            MERGE (t)-[:IN_TOPIC]->(topic)
            MERGE (topic)-[:BUNDLES]->(t)
            """,
            type_id=type_id,
            topic_id=str(topic_id),
        )


def _write_event(session, clip_id: str, clip: dict, event: dict) -> None:
    type_id = event.get("type") or event.get("definition") or "event"
    title = event.get("title") or type_id.replace("_", " ")
    times = _event_window(event, clip)
    event_id = f"{clip_id}:{type_id}:{times['start_ms']}"
    session.run(
        """
        MATCH (c:Clip {id: $clip_id})
        MERGE (t:EventType {id: $type_id})
        ON CREATE SET t.title = $title,
                      t.name = $title,
                      t.description = $context,
                      t.aliases = []
        ON MATCH SET t.title = coalesce(t.title, $title),
                     t.name = coalesce(t.name, $title)
        MERGE (e:Event {id: $event_id})
        SET e.type = $type_id,
            e.definition = $type_id,
            e.title = $title,
            e.summary = $summary,
            e.analysis = $analysis,
            e.important = $important,
            e.transcript = $transcript,
            e.context = $context,
            e.video_id = $video_id,
            e.source_name = $source_name,
            e.start_timestamp = $start_timestamp,
            e.end_timestamp = $end_timestamp,
            e.seek_s = $seek_s,
            e.cell = $cell,
            e.confidence = $confidence,
            e.source = $source,
            e.evidence = $evidence
        REMOVE e.start_ms, e.end_ms, e.clock, e.end_clock
        MERGE (c)-[:CONTAINS]->(e)
        MERGE (c)-[:HAS_TYPE]->(t)
        MERGE (e)-[:INSTANCE_OF]->(t)
        WITH t
        MATCH (vid:Video {id: $video_id})
        MERGE (vid)-[:HAS_TYPE]->(t)
        """,
        clip_id=clip_id,
        video_id=clip.get("video_id") or "",
        type_id=type_id,
        event_id=event_id,
        title=title,
        summary=(event.get("summary") or clip.get("summary") or event.get("context") or ""),
        analysis=event.get("analysis") or "",
        important=event.get("important") or clip.get("important") or [],
        transcript=(event.get("transcript") or clip.get("transcript") or ""),
        context=event.get("context"),
        source_name=clip.get("source_name") or "",
        start_timestamp=times["start_timestamp"],
        end_timestamp=times["end_timestamp"],
        seek_s=times["seek_s"],
        cell=event.get("cell"),
        confidence=event.get("confidence"),
        source=event.get("source") or "model",
        evidence=json.dumps(event.get("evidence") or []),
    )
    continues_from = event.get("continues_from")
    if continues_from:
        session.run(
            """
            MATCH (prev:Event {id: $prev_id})
            MATCH (e:Event {id: $event_id})
            MERGE (prev)-[:CONTINUES]->(e)
            """,
            prev_id=continues_from,
            event_id=event_id,
        )
    video_id = clip.get("video_id") or ""
    roles = event.get("people_roles") or {}
    for person_id in event.get("people_ids") or []:
        session.run(
            """
            MATCH (e:Event {id: $event_id})
            MATCH (p:Person {id: $person_id})
            MERGE (e)-[rel:INVOLVES]->(p)
            SET rel.role = $role
            """,
            event_id=event_id,
            person_id=f"{video_id}:{person_id}",
            role=roles.get(person_id) or "unknown",
        )
    if event.get("subject_ids"):
        session.run(
            """
            MATCH (e:Event {id: $event_id})
            SET e.subject_ids = $subject_ids
            """,
            event_id=event_id,
            subject_ids=event.get("subject_ids") or [],
        )
    for vehicle_id in event.get("vehicle_ids") or []:
        session.run(
            """
            MATCH (e:Event {id: $event_id})
            MATCH (v:Vehicle)
            WHERE v.id = $vehicle_id OR v.plate = $plate OR v.id ENDS WITH $local
            MERGE (e)-[:INVOLVES]->(v)
            """,
            event_id=event_id,
            vehicle_id=f"{video_id}:{vehicle_id}",
            plate=str(vehicle_id).upper(),
            local=vehicle_id,
        )
    for object_id in event.get("object_ids") or []:
        session.run(
            """
            MATCH (e:Event {id: $event_id})
            MATCH (n:Object)
            WHERE n.id = $object_id OR n.id ENDS WITH $local
            MERGE (e)-[:INVOLVES]->(n)
            """,
            event_id=event_id,
            object_id=f"{video_id}:{object_id}",
            local=object_id,
        )


def _event_window(event: dict, clip: dict) -> dict:
    start_ms = int(event.get("start_ms", clip["start_ms"]))
    end_ms = int(event.get("end_ms", clip["end_ms"]))
    start_timestamp = (
        event.get("start_timestamp")
        or event.get("start_clock")
        or event.get("clock")
        or format_clock(start_ms)
    )
    end_timestamp = event.get("end_timestamp") or event.get("end_clock") or format_clock(end_ms)
    seek_s = event.get("seek_s")
    if seek_s is None:
        seek_s = round(start_ms / 1000.0, 3)
    return {
        "start_ms": start_ms,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "seek_s": seek_s,
    }


def _ensure_constraints(session) -> None:
    session.run(
        """
        CREATE CONSTRAINT video_id IF NOT EXISTS
        FOR (v:Video) REQUIRE v.id IS UNIQUE
        """
    )
    session.run(
        """
        CREATE CONSTRAINT clip_id IF NOT EXISTS
        FOR (c:Clip) REQUIRE c.id IS UNIQUE
        """
    )
    session.run(
        """
        CREATE CONSTRAINT event_id IF NOT EXISTS
        FOR (e:Event) REQUIRE e.id IS UNIQUE
        """
    )
    session.run(
        """
        CREATE CONSTRAINT person_id IF NOT EXISTS
        FOR (p:Person) REQUIRE p.id IS UNIQUE
        """
    )
    session.run(
        """
        CREATE CONSTRAINT vehicle_id IF NOT EXISTS
        FOR (v:Vehicle) REQUIRE v.id IS UNIQUE
        """
    )
    session.run(
        """
        CREATE CONSTRAINT object_id IF NOT EXISTS
        FOR (o:Object) REQUIRE o.id IS UNIQUE
        """
    )
    session.run(
        """
        CREATE CONSTRAINT plate_id IF NOT EXISTS
        FOR (p:Plate) REQUIRE p.id IS UNIQUE
        """
    )
    session.run(
        """
        CREATE CONSTRAINT event_type_id IF NOT EXISTS
        FOR (t:EventType) REQUIRE t.id IS UNIQUE
        """
    )
    session.run(
        """
        CREATE CONSTRAINT topic_id IF NOT EXISTS
        FOR (t:Topic) REQUIRE t.id IS UNIQUE
        """
    )
    session.run(
        """
        CREATE INDEX person_is_cop IF NOT EXISTS
        FOR (p:Person) ON (p.is_cop)
        """
    )
    session.run(
        """
        CREATE INDEX person_potential_suspect IF NOT EXISTS
        FOR (p:Person) ON (p.potential_suspect)
        """
    )
    session.run(
        """
        CREATE INDEX person_clothes IF NOT EXISTS
        FOR (p:Person) ON (p.clothes)
        """
    )
    session.run(
        """
        CREATE INDEX event_type IF NOT EXISTS
        FOR (e:Event) ON (e.type)
        """
    )
    session.run("DROP INDEX event_start IF EXISTS")
    session.run("DROP INDEX event_clock IF EXISTS")
    session.run(
        """
        CREATE INDEX event_start_timestamp IF NOT EXISTS
        FOR (e:Event) ON (e.start_timestamp)
        """
    )
    session.run(
        """
        CREATE INDEX event_end_timestamp IF NOT EXISTS
        FOR (e:Event) ON (e.end_timestamp)
        """
    )
