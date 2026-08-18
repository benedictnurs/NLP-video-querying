# Police Video Intelligence — Simplified Plan

Build a searchable graph of police video: clips, people, vehicles, plates, speech, and events. Users ask natural-language questions; the system answers from the graph with timestamps and evidence.

**Stack (v1):** Airflow on Docker ingests video and writes to Neo4j. FastMCP sits on Neo4j for retrieval.

---

## What we are building

Three services in one Docker Compose stack:

```
video file
    → Airflow DAG (ingest + extract + write)
    → Neo4j graph
    → FastMCP tools
    → chat client / Claude / Cursor
```

| Piece | Job |
|---|---|
| **Airflow** | Orchestrate one DAG per video. Workers do the processing. |
| **Neo4j** | Store entities, events, evidence, and relationships. |
| **FastMCP** | Expose safe read tools over the graph. No raw Cypher. |

Media stays on disk (or MinIO). Neo4j stores URIs and timestamps only.

---

## Recommended local models

Pick the smallest model that can emit a usable signal. These are indexers, not judges.

### Sound — quick detection

| Job | Use | Size / speed | Notes |
|---|---|---|---|
| Speech vs silence | **Silero VAD** | Tiny, CPU-real-time | Run first. Do not transcribe radio hiss or empty cabin. |
| Loudness / impact spike | **No model** — RMS vs rolling baseline | Free | Catches bangs, doors, collisions without a classifier. |
| Named audio events | **YAMNet** | ~3.7M params, ~15 MB | AudioSet 521 classes. Map `Yell`, `Scream`, `Shout`, `Gunshot, gunfire`, `Explosion`, `Slam`, `Siren`, `Emergency vehicle` into our signals. |
| Better audio tagging later | PANNs CNN14 or EfficientAT | 5–10× heavier | Only if YAMNet misses too many yells/impacts. |

YAMNet is the right v1 audio net: MobileNet-sized, ONNX/TFLite, no GPU required. Pair it with RMS so a single siren does not look like an event.

Do **not** use a big audio transformer (BEATs, AST) on every clip.

### Image — quick detection

| Job | Use | Size / speed | Notes |
|---|---|---|---|
| People + vehicles | **YOLO11n** (or YOLO26n if you standardize on Ultralytics 2026) | 2.6M, ~2–6 GFLOPs | COCO already has `person`, `car`, `truck`, `bus`, `motorcycle`. Sample at 2–5 fps on 640px. |
| Tracking | **ByteTrack** (built into Ultralytics) | Negligible | Turns detections into `person_track_7`. |
| Clothing color | **HSV histogram on the torso crop** | Free | “White shirt” does not need CLIP. |
| License plates | Small **plate-specialized YOLO** (e.g. fast-alpr / YOLOv8-plate) | Nano-class | Detect the plate box; do not OCR the whole frame. |
| OCR | **PaddleOCR PP-OCRv4 mobile** | Mobile | Run on plate crops and a few text crops only. |

Stay on nano. YOLO11s is the first upgrade if plates or distant people are missed. Skip RT-DETR / RF-DETR until you have a GPU worker and accuracy is the bottleneck.

Open-vocab models (YOLO-World, Florence-2, MobileCLIP) are slower and belong in a later “find unusual objects” pass, not the default indexer.

### Video transcription

| Job | Use | When |
|---|---|---|
| Runtime (NVIDIA / Linux Docker) | **faster-whisper** (CTranslate2, int8) | Default Airflow worker |
| Runtime (Apple Silicon laptop) | **whisper.cpp** or **mlx-whisper** | Local Mac testing; faster-whisper is CPU-only on Mac |
| Bulk English bodycam | **`small.en`** or **distil-whisper `small.en`** | Best cost/quality for messy field audio |
| First pipeline bring-up | **`base`** or **`tiny.en`** | Fast enough to prove the DAG |
| Noisy / multilingual later | `large-v3-turbo` | 2–4× slower; only if WER is bad |
| Extreme edge / English-only | Moonshine tiny/base | Smaller than Whisper; try if `tiny.en` is still too slow |

Always: Silero VAD → cut non-speech → whisper on speech segments only → keep word timestamps.

Whisper `tiny` is fine for keyword gating (Miranda phrases). Use `small.en` before you jump to Gemini for “what was said.”

NVIDIA Parakeet / Canary are faster on GPU but add NeMo weight. Not worth it until transcription is the bottleneck.

---

## Tech we are using

Keep this small. Everything else from the original spec is later.

| Layer | Choice | Why |
|---|---|---|
| Orchestration | Apache Airflow 2.x in Docker Compose | Batch ingest, retries, one task per clip |
| Object storage | Local volume (MinIO later if needed) | Simple; swap to S3 later |
| Speech activity | Silero VAD | Skip silent stretches before ASR |
| Transcription | faster-whisper `base` / `small.en` | Word-level transcript, timestamps |
| Audio events | YAMNet | Yell, scream, siren, gunshot, impact-ish classes |
| People / vehicles | YOLO11n + ByteTrack | Fast boxes + persistent tracks |
| Plates / text | plate YOLO crop + PaddleOCR mobile | Exhaustive plate + visible text |
| Event gate | Local rules on transcript + signals | Decides if a clip is worth a cloud call |
| Event analysis | Gemini Flash via OpenRouter | Only on gated clips; structured events |
| Graph | Neo4j 5 Community | Ontology + timestamps + evidence |
| Retrieval | FastMCP | Constrained tools for agents |

**Not in v1:** pyannote diarization, Florence-2, CLIP, BGE, PANNs, optical flow, YAML definition agents, frontier synthesis service, Airflow backfill-on-query.

See [Recommended local models](#recommended-local-models) for why these and what to upgrade to.

---

## Pipeline

One DAG per video. Airflow schedules workers; it does not decode video itself.

```
ingest_video
    → split_into_clips
    → process_clips          # local models, every clip
    → detect_event_candidates
    → analyze_events         # OpenRouter Gemini Flash, candidates only
    → write_graph
    → finalize_video
```

Local models run on **100% of footage**. Gemini runs on **the small subset that looks like an event**.

### 1. Ingest

- Copy the source file into `data/videos/{video_id}/original.mp4`
- Extract 16 kHz mono audio
- Write a lower-res processing copy
- Record duration, fps, source path

### 2. Split

- Cut on scene changes, with overlapping windows (~15–30s, 2s overlap)
- Keep exact `start_ms` / `end_ms` back to the original file
- Emit a clip list for dynamic task mapping

### 3. Process every clip (cheap local models)

This is the default path. No API cost. These models do not “understand” the scene — they dump structured metadata so later queries and the event gate have something to work with.

| Local model | Input | Metadata it writes |
|---|---|---|
| **Silero VAD** | 16 kHz audio | Speech segments; skip silence |
| **faster-whisper** (`base` / `small.en`) | Speech segments | Word-level transcript, `start_ms`/`end_ms` |
| **YAMNet** | 16 kHz audio | `yelling`, `siren`, `gunshot`, `impact`-like class scores |
| **Audio RMS** | Waveform | `loudness`, `loud_impact` vs rolling baseline |
| **YOLO11n + ByteTrack** | Low-res frames (~2–5 fps) | `person` / `vehicle` boxes, track IDs, time span |
| **HSV histogram on person crops** | Track crops | `upper_clothing_color`, rough uniform-ish flag |
| **Plate YOLO + PaddleOCR mobile** | Full-res crops | Plate string, other visible text, confidence |
| **Keyword scan** | Transcript | Hits for Miranda, “step back”, “get on the ground”, etc. |

Rule: local models answer **literal** questions. “White shirt”, “plate 8ABC123”, “someone said remain silent” never need Gemini.

Clip JSON after this step (always written, even if nothing interesting happened):

```yaml
clip:
  id: clip_128
  video_id: video_1
  start_ms: 120000
  end_ms: 155000
  transcript: "Step away from the vehicle. Step away from the vehicle."
  entities:
    - { id: person_track_7, type: person, upper_clothing_color: white }
    - { id: person_track_8, type: person, uniform_probability: 0.84 }
    - { id: vehicle_track_2, type: vehicle }
  plates:
    - { id: plate_track_12, text: "8ABC123", confidence: 0.94 }
  signals:
    yelling: 0.81            # loud speech vs baseline
    repeated_commands: 3     # same directive phrase 3x
    loud_impact: 0.21
    person_count: 2
  keyword_hits:
    - { phrase: "step away from the vehicle", count: 2 }
```

Tracks are **video-local and anonymous**. Do not merge people across videos by clothing.

How the cheap models stay cheap:

- Decode a **processing copy** (480p or 720p), not the original.
- Sample vision at **2–5 fps**, not every frame. OCR still uses a few full-res crops.
- Use the **nano/tiny** weights. Accuracy is “good enough to index,” not courtroom-grade.
- Run extractors in the Airflow worker container. No cloud round-trip for this step.
- Write JSON to `data/videos/{id}/clips/{clip_id}.json`. That file is the contract for Neo4j and for Gemini.

### 4. Event gate (still local, still free)

Local metadata is not an event. An event is a **candidate** the rules think Gemini should look at.

```
clip JSON
  → score against definitions.yaml
  → candidate? 
        no  → write metadata to Neo4j, stop
        yes → call OpenRouter Gemini Flash
```

Gate on **agreement across signals**, not one noisy number:

| Local evidence | Candidate? | Why |
|---|---|---|
| Loudness or motion alone | No | Wind, sirens, walking |
| Transcript keyword only (Miranda phrase) | Yes | Cheap and high-precision |
| Yelling + repeated commands | Yes | Verbal escalation |
| Impact + person count change / person down | Yes | Possible restraint / fall |
| Plate OCR disagreement across frames | Yes | Gemini checks the crop, not the whole video |
| Bright flash only | No | Likely emergency lights |

`definitions.yaml` is a checked-in list of these rules. Example:

```yaml
verbal_escalation:
  any: [yelling, repeated_commands]
  require_min_signals: 2
  exclude: [continuous_siren]

miranda_warning:
  transcript_any:
    - "you have the right to remain silent"
    - "anything you say can and will"
```

Most clips die here. That is the point.

### 5. Gemini Flash via OpenRouter (events only)

One env var: `OPENROUTER_API_KEY`. One model id, configurable:

```
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
```

OpenRouter is an OpenAI-compatible proxy. The worker posts to `https://openrouter.ai/api/v1/chat/completions`. We are not calling Google directly.

**When we call it:** `detect_event_candidates` emitted this clip.

**What we send** (keep tokens low):

- Clip video **or** 4–8 keyframes (prefer keyframes; cheaper and sharper than 1 fps video)
- The local transcript
- Track IDs already assigned (`person_track_7`, …)
- The candidate definition(s) and local signals
- For plates: the disputed crops only, not the clip

**What we ask it to return** — structured JSON, observable only:

```yaml
events:
  - definition: verbal_escalation
    start_ms: 128000
    end_ms: 149000
    confidence: 0.89
    participants:
      - { entity_id: person_track_8, role: speaker }
      - { entity_id: person_track_7, role: addressee }
    evidence:
      - { modality: transcript, timestamp_ms: 128000, value: "Step away from the vehicle." }
      - { modality: visual, timestamp_ms: 136000, value: "person_track_7 moves toward vehicle_track_2" }
```

Gemini may:

- Confirm or reject the local candidate
- Tighten timestamps
- Attach existing track IDs as participants
- Add a second related event in the same clip (e.g. escalation then restraint)

Gemini may not:

- Invent identities
- Infer intent, guilt, intoxication, or legal conclusions
- Create new definition types in v1 (it only picks from `definitions.yaml`)

Store: “officer reads rights”, “person loses balance twice”.  
Do not store: “driver was drunk”.

If OpenRouter fails, the clip still lands in Neo4j with local metadata. The candidate is marked `analysis_pending` and Airflow retries. We never drop the cheap facts because the cloud call failed.

### 6. Write graph

Idempotent Cypher upsert from clip JSON.

```
(Video)-[:CONTAINS]->(Clip)
(Clip)-[:CONTAINS]->(Event)
(Clip)-[:CONTAINS]->(Person)
(Clip)-[:CONTAINS]->(Vehicle)
(Vehicle)-[:HAS_VISIBLE_PLATE]->(Plate)
(Event)-[:INVOLVES]->(Person|Vehicle)
(Event)-[:SUPPORTED_BY]->(Observation)
(Event)-[:PRECEDED_BY]->(Event)
```

Every node that is a finding carries:

- `start_ms`, `end_ms`
- `confidence`
- `source_uri` (video or frame)
- `model` + `model_version`

---

## Neo4j ontology (v1)

Keep node types few:

| Node | Meaning |
|---|---|
| `Video` | One ingested file |
| `Clip` | Time window of that file |
| `Person` | Tracked person in this video |
| `Vehicle` | Tracked vehicle in this video |
| `Plate` | OCR’d plate text |
| `Event` | Labeled occurrence (escalation, Miranda, restraint, …) |
| `Observation` | Raw evidence (transcript line, signal, frame note) |

Roles (`officer`, `driver`) are properties on `INVOLVES` with confidence, not identity.

Starter event types (hardcoded, not a YAML agent):

- `verbal_escalation`
- `miranda_warning`
- `physical_restraint`
- `loud_impact`
- `visible_plate`
- `visible_text`

Add new types by editing a small `definitions.yaml` checked into the repo. No create-from-clip agents in v1.

---

## FastMCP

Read-only tools. The model never writes Cypher.

| Tool | Use |
|---|---|
| `search_events` | Filter by event type, video, time, participants |
| `find_visible_license_plates` | Plates for a video or across videos |
| `find_people_by_attributes` | e.g. white shirt, uniform |
| `search_transcripts` | Keyword / phrase over speech |
| `get_event_timeline` | Ordered events for a video or clip |
| `get_supporting_evidence` | Frames, transcript lines, signals for an event |
| `get_video_context` | Video + clip list + summary stats |

Example: `find_visible_license_plates(video_id="video_1")`

```json
[
  {
    "plate": "8ABC123",
    "confidence": 0.94,
    "timestamps": ["02:04–02:11"],
    "vehicle_id": "vehicle_track_2",
    "evidence_frame": "data/videos/video_1/frames/126400.jpg"
  }
]
```

Natural-language questions are answered by an MCP client (Claude, Cursor, etc.) calling these tools. No separate “frontier planner” service in v1.

---

## Docker Compose

```
airflow-webserver
airflow-scheduler
airflow-worker
postgres          # Airflow metadata
neo4j
mcp-server
```

Shared volume: `./data` for videos, clips, frames, and clip JSON.

Airflow workers need GPU optional; CPU is enough for a first demo (whisper-tiny / YOLO-nano).

---

## Repo layout

```
docker-compose.yml
dags/
  ingest_video.py
workers/
  ingest.py
  split.py
  transcribe.py
  detect_track.py
  ocr_plates.py
  score.py
  analyze_events.py          # OpenRouter Gemini Flash
  write_graph.py
mcp/
  server.py
  tools.py
neo4j/
  constraints.cypher
definitions.yaml
data/                 # gitignored
plan.md
```

---

## Build order

### Phase 1 — Skeleton (this week)

- Docker Compose: Airflow + Postgres + Neo4j
- DAG that accepts a video path, copies it, writes a `Video` node
- FastMCP with `get_video_context` only
- Prove: drop a file → node appears → MCP can read it

### Phase 2 — Exhaustive extractors

- Split clips
- Transcribe every clip
- Detect + track people/vehicles
- OCR plates
- Write `Clip`, `Person`, `Vehicle`, `Plate`, `Observation`
- MCP: `search_transcripts`, `find_visible_license_plates`, `find_people_by_attributes`

### Phase 3 — Events

- Local gate from transcript phrases + signals
- OpenRouter worker: Gemini Flash on candidates only
- Write confirmed `Event` nodes + evidence
- MCP: `search_events`, `get_event_timeline`, `get_supporting_evidence`

### Phase 4 — Later (out of scope until Phase 3 works)

- MinIO / S3
- YAML definition agents (call / update / create)
- Speaker diarization, audio-event models, CLIP embeddings
- Cross-video identity (do not do this casually)
- On-demand Airflow backfills from MCP
- Dedicated synthesis model

---

## Design rules

1. **Every finding points at evidence.** Timestamp, source URI, model version, confidence.
2. **Tracks are not identities.** `person_track_7` lives inside one video.
3. **Plates are exhaustive.** Never gated on salience.
4. **Airflow orchestrates; workers process.**
5. **MCP reads; it does not write the graph.**
6. **Observable facts only.** No legal or intent labels from the model.
7. **Local by default, OpenRouter on events.** Every clip gets metadata. Gemini Flash is a confirm/structure step, not the indexer.

---

## Example questions this stack should answer after Phase 3

- “Find clips where someone says Miranda / you have the right to remain silent.”
- “List every license plate in this video and what it says.”
- “Find people wearing white shirts.”
- “Find events labeled verbal_escalation near physical_restraint.”
- “Show the timeline and evidence for video_1.”
