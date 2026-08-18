## NLP For Body Camera Querying

Investigative index for police bodycam and dashcam. Drop a file in `videos/`, Docker runs it through speech, objects, event buckets, and identity matching, then writes a Neo4j graph. Codex or Cursor searches that graph through the **Code Four** MCP: events, people, vehicles, plates, and scene images.

This is a scene index, not a charging system. `potential_suspect` is who the officer is stopping or questioning. `is_cop` is uniform appearance only. Event ids (`traffic_stop`, `miranda_warning`, `field_sobriety_test`) are observable labels, not charges.

## How to use

1. Put a file in `videos/` (for example `video_4.mp4`). Wait a few seconds after the copy finishes so the file is stable.
2. Start Docker (see [Setup](#setup)). Trigger **ingest_videos** in Airflow. One ungraphed file runs end to end, then the next file in `videos/` is queued. Already-graphed files are skipped.
3. Watch the run at [http://127.0.0.1:8080](http://127.0.0.1:8080) (`airflow` / `airflow`). When status is graphed, the video is in Neo4j.
4. Run the setup script in /scripts to install the MCP in Codex.
5. In Codex or Cursor, ask Code Four MCP. Then search anything using natural language:

Local UIs after Docker is up:

- Airflow: [http://127.0.0.1:8080](http://127.0.0.1:8080) (`airflow` / `airflow`)
- Neo4j Browser: [http://127.0.0.1:7474/browser/](http://127.0.0.1:7474/browser/) (`neo4j` / password from `.env`)

## Setup

Need Docker Desktop (or another Compose runtime), Python 3.12+, and an [OpenRouter](https://openrouter.ai/) key for Gemini. Do not commit `.env`.

### Docker (ingest + Neo4j)

This stack is Airflow, Spark, Postgres, and Neo4j. The MCP process runs on the host and talks to Neo4j at `bolt://127.0.0.1:7687`. Airflow inside Compose uses `bolt://neo4j:7687`.

```bash
cd "/path/to/NLP video querying"

cp .env.example .env
mkdir -p videos data

python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Put that Fernet string in `.env` as `AIRFLOW__CORE__FERNET_KEY`. Set `NEO4J_PASSWORD` (must match what you use in Neo4j Browser) and `OPENROUTER_API_KEY`. Leave `NEO4J_URI=bolt://127.0.0.1:7687` for host tools.

First start (builds the Airflow image; later starts are faster):

```bash
docker compose up -d --build
```

Wait until the Airflow UI answers. Then either drop files in `videos/` and trigger the DAG from the UI, or:

```bash
docker compose exec airflow-scheduler airflow dags trigger ingest_videos
```

Useful checks:

```bash
docker compose ps
docker compose logs -f airflow-scheduler
```

Stop without deleting volumes: `docker compose down`.

### MCP (Code Four)

The graph server is stdio FastMCP in `graph_mcp/`. It needs the venv and a running Neo4j from Docker.

**Codex** (creates `.venv`, installs deps, writes `~/.codex/config.toml` as server `code-four`):

```bash
./scripts/install_codex_mcp.sh
```

Restart Codex after install. Optional: `./scripts/install_codex_mcp.sh --codex-allow-all` so Codex does not prompt on every tool.

**Manual venv** (Cursor, or if you skipped the installer):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-mcp.txt
chmod +x run_mcp.sh
./run_mcp.sh
```

`./run_mcp.sh` is the stdio entrypoint. You do not leave it running in a terminal for Codex/Cursor; the client starts it.

**Cursor** — repo-local `.cursor/mcp.json` (gitignored). Point command at this repo’s venv:

```json
{
  "mcpServers": {
    "code-four": {
      "command": "/ABS/PATH/TO/NLP video querying/.venv/bin/python",
      "args": ["-m", "graph_mcp"],
      "cwd": "/ABS/PATH/TO/NLP video querying",
      "env": {
        "PYTHONPATH": "/ABS/PATH/TO/NLP video querying",
        "NEO4J_URI": "bolt://127.0.0.1:7687",
        "DEFINITIONS_PATH": "/ABS/PATH/TO/NLP video querying/definitions.yaml",
        "DATA_DIR": "/ABS/PATH/TO/NLP video querying/data"
      }
    }
  }
}
```

Restart Cursor after saving. If graph URIs still look like `/opt/airflow/data/...`, call `rewrite_media_paths` once so they map to local `data/`.

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

`correct_graph` updates or creates Person / Vehicle / Event / Object / Plate (not Video or Clip). `link_graph` adds `INVOLVES`, `CONTAINS`, `CONTINUES`, `HAS_*`. `merge_graph` collapses two people or cars into `keep_id` and deletes `drop_id`. Writes only when the user asked or corrected a fact.

## Layout

```text
videos/              drop zone
definitions.yaml     event buckets + topics
dags/ingest_video.py ingest_videos DAG
workers/             Spark, scan, enrich, fingerprint, graph write
graph_mcp/           Code Four MCP
data/videos/         per-video cache (clips, splice, ingest.json)
scripts/             Codex MCP installer
.env.example         copy to .env (Fernet, Neo4j password, OpenRouter key)
```
