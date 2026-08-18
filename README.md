## NLP For Body Camera Querying

Investigative index for police bodycam and dashcam. Drop a file in `videos/`, Airflow runs it through speech, objects, event buckets, and identity matching, then writes a Neo4j graph. Codex (or Cursor) searches that graph through the **Code Four** MCP: events, people, vehicles, plates, and scene images.

This is a scene index, not a charging system. `potential_suspect` is who the officer is stopping or questioning. `is_cop` is uniform appearance only. Event ids (`traffic_stop`, `miranda_warning`, `field_sobriety_test`) are observable labels, not charges.

## Stack

| Layer | Tech |
| --- | --- |
| Orchestration | Apache Airflow (`ingest_videos` DAG), Docker Compose |
| Clip workers | Apache Spark, ffmpeg |
| Speech | Faster-Whisper `tiny.en` |
| Detection | YOLOv8n (ONNX) |
| Event catalog | `definitions.yaml` (ids, aliases, transcript phrases, topics) |
| Vision / NLP | Gemini via OpenRouter — Flash Lite for scan/enrich, Gemini 3.5 Flash for identity |
| Identity | LangGraph fingerprint agent across clips |
| Graph | Neo4j 5 |
| Query | FastMCP stdio server (`graph_mcp`), Cypher query blocks |

Local services after `docker compose up -d`:

- Airflow UI: [http://127.0.0.1:8080](http://127.0.0.1:8080) (`airflow` / `airflow`)
- Neo4j Browser: [http://127.0.0.1:7474/browser/](http://127.0.0.1:7474/browser/) (`neo4j` / password from `.env`)

## High-level flow

```text
videos/video_N.mp4
        │
        ▼
  ingest_videos (one file at a time)
        │
        ├─ copy + ffprobe          cache skip if already graphed
        ├─ Spark clips             ~3 min windows, overlap
        │     Whisper, YOLO, splice.jpg
        ├─ score                   YAML keyword / signal buckets
        ├─ VLM scan                Gemini labels Event.type + clocks
        ├─ enrich                  people, summaries; may edit YAML
        ├─ fingerprint             same person/car/object across clips
        └─ Neo4j                   Video, Clip, Event, Person, …
                │
                ▼
        Code Four MCP  ←  Codex / Cursor
                │
                ├─ query blocks (dui, miranda, timeframe, …)
                ├─ open splice in Finder
                └─ analyze_scene (images into the model)
```

Graphed files are cached (`data/videos/<stem>/` plus `data/registry.json`). Adding `video_2.mp4` does not reprocess `video_1`.

## Ingest pipeline (`ingest_videos`)

Manual DAG. `max_active_runs=1`. Each run takes the **next** ungraphed file in `videos/` (sorted by name), runs the stages below end to end, then queues another run if more files remain. Unrelated videos never share a batch.

1. **`ingest_video`** — Copy into `data/videos/<video_id>/`, ffprobe. `video_id` is the filename stem (`video_4.mp4` → `video_4`).
2. **`spark_process_clips`** — Cut ~180s clips with overlap. Whisper transcript, YOLOv8n boxes (person / vehicle / object), frame grid (`splice.jpg` / tagged splice). YAML `transcript_any` phrases become `keyword_hits`.
3. **`score_clips`** — Audio signals plus `definitions.yaml` rules → `candidate_definitions` and local events.
4. **`scan_events_vlm`** — Gemini Flash Lite sees the splice and the YAML catalog (`catalog_for_prompt`). It must bucket the clip into catalog ids (`traffic_stop`, `miranda_warning`, …) with `start_timestamp` clocks like `01:56`.
5. **`analyze_important_openrouter`** — Gemini enrich: people, vehicles, clip summary. **Agentic catalog:** it may `updated_definitions` (aliases/phrases on an existing id) or `new_definitions` only when nothing fits. Those write back to the YAML / learned file.
6. **`fingerprint_identities`** — LangGraph + Gemini 3.5 Flash. Canonical `person_N`, `vehicle_N`, `object_N`, plates across clips. Roles: officer / civilian / `potential_suspect`.
7. **`write_graph`** — Neo4j. `EventType` and `Topic` nodes come from the YAML (`dui` bundles `field_sobriety_test`). Media URIs are stored as host paths under `data/`.

Spark may parallelize **clips of the same video**. Separate files wait their turn.

### Event catalog (`definitions.yaml`)

Top-level keys (except `topics:`) are `Event.type` ids. Each has title, aliases, description, `how_to_confirm`, and `transcript_any`.

`topics:` are officer-facing bundles for search, not extra event types:

- `dui` → `field_sobriety_test`
- `miranda` → `miranda_warning`
- `traffic_stop`, `arrest` (arrest also bundles `handcuffing`)

Keyword matching and scoring use the YAML during Spark/score. Gemini scan/enrich use it as the bucket list. MCP `find_events("dui")` resolves through `topics` and aliases.

## Graph (Neo4j)

| Node | Role |
| --- | --- |
| Video | One ingested file |
| Clip | Time slice: clocks, transcript, summary, `clip_uri`, `tagged_splice_uri` |
| Event | Something that happened; `type` is a catalog id; `start_timestamp` / `end_timestamp` |
| EventType / Topic | Catalog + query bundles |
| Person / Vehicle / Object / Plate | Canonical across clips of that video |

Relationships include `(Clip)-[:CONTAINS]->(Event)`, `(Event)-[:INVOLVES {role}]->(Person)`, `(Event)-[:INSTANCE_OF]->(EventType)`, `(EventType)-[:IN_TOPIC]->(Topic)`.

Clocks on the graph are seekable `MM:SS` (and `seek_s`), not milliseconds.

## MCP (Code Four)

Package: `graph_mcp`. Stdio FastMCP. Neo4j on `bolt://127.0.0.1:7687`. Primer first: `explain_graph_context`.

**Query.** Prefer composable blocks over raw Cypher:

```text
run_recipe("all_dui")
run_recipe("all_miranda")
run_blocks("events | topic query=dui | timeframe start=01:00 end=20:00 | with_people | with_clip | return_events")
```

`list_query_blocks` is the catalog. Shortcuts (`find_events`, `find_people`, `events_in_timeframe`, …) are the same recipes. `run_custom_cypher` is read-only MATCH/RETURN.

**Corrections.** `correct_graph` writes Neo4j only when the user asked or corrected a fact (`user_request` quotes them, `confirmed=true`). Preview with `confirmed=false`. Do not update the graph after a scene analysis on your own.

**Media.** Graph URIs point at local `data/videos/...`.

- `analyze_scene(clip="clip_0009")` — sends tagged splice.jpg (optional frame cells) to the model and reveals Finder
- `open_clip_attachment` / `open_in_finder` — open the file only

### Run the MCP

```bash
./run_mcp.sh
# or
./scripts/install_codex_mcp.sh
```

Cursor: `.cursor/mcp.json` (gitignored). Codex: `~/.codex/config.toml` server `code-four`. Restart the client after install.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-mcp.txt
```

## Run ingest

1. Put files in `videos/` (e.g. `video_4.mp4`). Wait a few seconds after copy finishes.
2. `docker compose up -d`
3. Trigger **ingest_videos** at [http://127.0.0.1:8080](http://127.0.0.1:8080) or:

```bash
docker compose exec airflow-scheduler airflow dags trigger ingest_videos
```

Needs `.env` with Airflow Fernet, Neo4j password, and `OPENROUTER_API_KEY`. Do not commit `.env`.

## Layout

```text
videos/              drop zone
definitions.yaml     event buckets + topics
dags/ingest_video.py ingest_videos DAG
workers/             Spark, scan, enrich, fingerprint, graph write
graph_mcp/           Code Four MCP
data/videos/         per-video cache (clips, splice, ingest.json)
scripts/             Codex MCP installer
```
