from __future__ import annotations

from datetime import datetime, timedelta

from airflow.decorators import dag, task

default_args = {
    "owner": "video-intel",
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}


@dag(
    dag_id="ingest_videos",
    description="Copy video → Spark clips → VLM event scan with clocks → people → fingerprint → Neo4j.",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["ingest", "video", "spark"],
)
def ingest_videos():
    @task
    def list_new_videos() -> list[str]:
        from workers.ingest import list_new_videos as _list

        return _list()

    @task
    def ingest_video(source_path: str) -> dict:
        """Copy + ffprobe only. Heavy ffmpeg/models run on Spark."""
        from workers.ingest import run_ingest

        return run_ingest(source_path)

    @task
    def spark_process_clips(video: dict) -> dict:
        """One Spark worker per splice: cut, whisper, YOLO nano, splice. No captioner."""
        from workers.spark_job import process_video_on_spark

        return process_video_on_spark(video)

    @task
    def score_clips(video: dict) -> dict:
        from workers.score import score_video_clips

        return score_video_clips(video)

    @task
    def scan_events_vlm(video: dict) -> dict:
        """Fast parallel Gemini splice scan: event types + start clocks."""
        from workers.scan import scan_video_events

        return scan_video_events(video)

    @task
    def analyze_important_openrouter(video: dict) -> dict:
        """Gemini: splice for people/events; extra frames only on loud/arrest clips."""
        from workers.openrouter import enrich_video_clips

        return enrich_video_clips(video)

    @task
    def fingerprint_identities(video: dict) -> dict:
        """LangGraph agent: match people/vehicles/plates/objects across clips."""
        from workers.fingerprint import fingerprint_video

        return fingerprint_video(video)

    @task
    def write_graph(video: dict) -> dict:
        from workers.graph import write_video_graph

        return write_video_graph(video)

    @task
    def finalize(results: list[dict]) -> dict:
        return {
            "graphed": len(results),
            "video_ids": [item["video_id"] for item in results],
            "clip_counts": [item.get("clip_count") for item in results],
        }

    paths = list_new_videos()
    ingested = ingest_video.expand(source_path=paths)
    processed = spark_process_clips.expand(video=ingested)
    scored = score_clips.expand(video=processed)
    scanned = scan_events_vlm.expand(video=scored)
    analyzed = analyze_important_openrouter.expand(video=scanned)
    fingerprinted = fingerprint_identities.expand(video=analyzed)
    graphed = write_graph.expand(video=fingerprinted)
    finalize(graphed)


ingest_videos()
