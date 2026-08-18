from __future__ import annotations

import json
import os

from neo4j import GraphDatabase

from workers.clips import load_clip_records
from workers.events import load_definitions
from workers.ingest import load_registry, save_registry_entry
from workers.paths import video_work_dir


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
                source_path=video.get("source_path"),
                original_uri=video.get("original_uri"),
                audio_uri=video.get("audio_uri"),
                processing_uri=video.get("processing_uri"),
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
        save_registry_entry(source_name, video["video_id"], fingerprint, "graphed")
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
            c.start_ms = $start_ms,
            c.end_ms = $end_ms,
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
        """,
        video_id=video["video_id"],
        clip_id=clip_id,
        local_id=record["id"],
        index=record["index"],
        start_ms=record["start_ms"],
        end_ms=record["end_ms"],
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
        splice_uri=record.get("splice_uri"),
        tagged_splice_uri=record.get("tagged_splice_uri"),
        clip_uri=record.get("clip_uri"),
        audio_uri=record.get("audio_uri"),
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
    if kind == "person":
        _write_person(session, video_id, clip_id, entity)
        return
    if kind == "vehicle":
        _write_vehicle(session, video_id, clip_id, entity)
        return
    entity_id = f"{clip_id}:{entity.get('id') or entity.get('label')}"
    session.run(
        """
        MATCH (c:Clip {id: $clip_id})
        MERGE (n:Object {id: $entity_id})
        SET n.label = $label_name,
            n.video_id = $video_id
        MERGE (c)-[:CONTAINS]->(n)
        """,
        clip_id=clip_id,
        entity_id=entity_id,
        video_id=video_id,
        label_name=entity.get("label"),
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
            p.signature = $signature,
            p.description = $description,
            p.is_cop = $is_cop
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
        signature=entity.get("signature") or "",
        description=entity.get("description") or "",
        is_cop=entity.get("is_cop"),
    )


def _write_vehicle(session, video_id: str, clip_id: str, entity: dict) -> None:
    plate = entity.get("plate") or ""
    local_id = entity.get("id") or "vehicle"
    vehicle_id = f"{video_id}:vehicle:{plate}" if plate else f"{video_id}:{local_id}"
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
    plate_id = f"{clip_id}:plate:{plate['text']}"
    session.run(
        """
        MATCH (c:Clip {id: $clip_id})
        MERGE (p:Plate {id: $plate_id})
        SET p.text = $text,
            p.confidence = $confidence,
            p.source = $source
        MERGE (c)-[:CONTAINS]->(p)
        """,
        clip_id=clip_id,
        plate_id=plate_id,
        text=plate["text"],
        confidence=plate.get("confidence"),
        source=plate.get("source") or "gemini",
    )
    graph_vehicle = f"{video_id}:vehicle:{plate['text']}"
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
        )


def _merge_event_type(session, type_id: str, title: str, description: str, aliases: list) -> None:
    session.run(
        """
        MERGE (t:EventType {id: $type_id})
        SET t.title = $title,
            t.name = $title,
            t.description = $description,
            t.aliases = $aliases
        """,
        type_id=type_id,
        title=title,
        description=" ".join(str(description).split()),
        aliases=list(aliases),
    )


def _write_event(session, clip_id: str, clip: dict, event: dict) -> None:
    type_id = event.get("definition") or "event"
    title = event.get("title") or type_id.replace("_", " ")
    event_id = f"{clip_id}:{type_id}:{event.get('start_ms', clip['start_ms'])}"
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
        SET e.definition = $type_id,
            e.title = $title,
            e.summary = $summary,
            e.analysis = $analysis,
            e.context = $context,
            e.start_ms = $start_ms,
            e.end_ms = $end_ms,
            e.confidence = $confidence,
            e.source = $source,
            e.evidence = $evidence
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
        summary=event.get("summary") or event.get("context") or "",
        analysis=event.get("analysis") or "",
        context=event.get("context"),
        start_ms=event.get("start_ms", clip["start_ms"]),
        end_ms=event.get("end_ms", clip["end_ms"]),
        confidence=event.get("confidence"),
        source=event.get("source") or "model",
        evidence=json.dumps(event.get("evidence") or []),
    )
    video_id = clip.get("video_id") or ""
    for person_id in event.get("people_ids") or []:
        session.run(
            """
            MATCH (e:Event {id: $event_id})
            MATCH (p:Person {id: $person_id})
            MERGE (e)-[:INVOLVES]->(p)
            """,
            event_id=event_id,
            person_id=f"{video_id}:{person_id}",
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
        CREATE INDEX person_is_cop IF NOT EXISTS
        FOR (p:Person) ON (p.is_cop)
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
        CREATE INDEX event_definition IF NOT EXISTS
        FOR (e:Event) ON (e.definition)
        """
    )
