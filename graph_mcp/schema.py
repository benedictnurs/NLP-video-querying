PRIMER = """
Police video intelligence graph
Purpose: help officers and analysts reconstruct bodycam/dashcam footage quickly — who was present, what happened, when, and which objects/vehicles/plates are the same across clips. This is an investigative index, not a charging system.

How to use this MCP
1. Call explain_graph_context first in every session (this primer).
2. Prefer a prebuilt query block (list_query_blocks, then the matching tool).
3. Use run_custom_cypher only when no prebuilt block fits. Read-only MATCH/RETURN only.

Nodes
- Video: one ingested file. id, source_name, duration_s, clip_count, status.
- Clip: ~3 minute slice with overlap. local_id (clip_0000), start_timestamp, end_timestamp (MM:SS in the full video), summary, transcript, clip_uri.
- Event: something that happened in a clip. type is a catalog id (traffic_stop, miranda_warning, arrest, ...). start_timestamp / end_timestamp are seekable clocks, not milliseconds. seek_s is seconds from video start. subject_ids are potential_suspect person local ids on that event.
- EventType: catalog definition for Event.type. Traffic stop, Miranda, arrest, etc. Scene labels, not charges.
- Person: canonical identity across clips (person_1, person_3). clothes, race, gender, hair, glasses, description, is_cop (uniform appearance only), role (officer | civilian | potential_suspect). potential_suspect is who the officer is stopping/arresting/questioning — not guilt.
- Vehicle: canonical car across clips. color, plate, analysis.
- Object: notable item (bottle, bag, phone). label, distinctive.
- Plate: license plate text. Linked from Vehicle when readable.

Relationships
- (Video)-[:STARTS_AT]->(Clip) then (Clip)-[:NEXT]->(Clip) for playback order.
- (Video)-[:HAS_PERSON|HAS_VEHICLE|HAS_OBJECT|HAS_PLATE|HAS_TYPE]->(...)
- (Clip)-[:CONTAINS]->(Person|Vehicle|Object|Plate|Event)
- (Clip)-[:HAS_TYPE]->(EventType)
- (Event)-[:INSTANCE_OF]->(EventType)
- (Event)-[:INVOLVES {role}]->(Person)  role = officer | potential_suspect | civilian | unknown
- (Event)-[:INVOLVES]->(Vehicle|Object)
- (Event)-[:CONTINUES]->(Event)  same type spanning adjacent clips (one traffic_stop across time)
- (Vehicle)-[:HAS_PLATE]->(Plate)

Timestamps
- Always use Event.start_timestamp / end_timestamp (and Clip.start_timestamp / end_timestamp), e.g. "01:56".
- seek_s is the numeric seek position in seconds.
- Do not use millisecond fields on the graph.

Roles (observable facts only)
- is_cop = true means a uniform/badge/duty belt is visible. Not legal identity.
- potential_suspect = the civilian the officer is talking to / stopping / cuffing / Mirandizing.
- Never treat potential_suspect as a conviction. Do not invent names, race, or gender.

Typical event.type values
traffic_stop, interrogation, miranda_warning, arrest, handcuffing, search_person, search_vehicle, verbal_escalation, physical_restraint, loud_impact, foot_pursuit, vehicle_pursuit, field_sobriety_test, medical_aid, welfare_check.

Example questions this graph answers
- All Miranda readings and when they started.
- Events between 01:00 and 05:00.
- Who is the potential suspect and their clothing snapshot.
- Same person across clips (canonical Person id).
- Vehicles, plates, and objects (bottle, bag) tied to an event.
""".strip()


EVENT_TYPES = [
    "traffic_stop",
    "interrogation",
    "miranda_warning",
    "arrest",
    "handcuffing",
    "search_person",
    "search_vehicle",
    "verbal_escalation",
    "physical_restraint",
    "loud_impact",
    "foot_pursuit",
    "vehicle_pursuit",
    "field_sobriety_test",
    "medical_aid",
    "welfare_check",
]
