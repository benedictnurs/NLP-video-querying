from __future__ import annotations

import json
import os
from pathlib import Path

from workers.clips import (
    clip_analysis_done,
    load_clip_records,
    save_clip_record,
    save_video_status,
    save_video_transcripts,
)
from workers.media import extract_audio
from workers.paths import models_dir, video_work_dir
from workers.split import clip_windows, cut_window
from workers.summarize import tag_and_summarize_local

_WORKER_MODELS: dict | None = None


def process_video_on_spark(video: dict) -> dict:
    _prepare_media(video)
    windows = list(enumerate(clip_windows(float(video["duration_s"]))))
    payloads = [(video, index, start, end) for index, (start, end) in windows]
    todo = [
        item
        for item in payloads
        if not clip_analysis_done(video["video_id"], f"clip_{item[1]:04d}")
    ]
    if todo:
        _run_spark(todo)

    records = load_clip_records(video["video_id"])
    records.sort(key=lambda item: item["index"])
    if len(records) < len(payloads):
        raise RuntimeError(
            f"{video['video_id']}: {len(records)}/{len(payloads)} clips finished; "
            "retry will resume remaining clips"
        )
    save_video_transcripts(video["video_id"])
    video = {
        **video,
        "clip_count": len(records),
        "clip_seconds": float(os.environ.get("CLIP_SECONDS", "180")),
        "status": "spark_processed",
    }
    return save_video_status(video, "spark_processed")


def _run_spark(todo: list[tuple]) -> None:
    from pyspark.sql import SparkSession

    workers = max(1, min(int(os.environ.get("SPARK_PARALLELISM", "4")), len(todo)))
    spark = (
        SparkSession.builder.appName("video-splices")
        .master(os.environ.get("SPARK_MASTER", "spark://spark-master:7077"))
        .config("spark.driver.host", os.environ.get("SPARK_DRIVER_HOST", "airflow-scheduler"))
        .config("spark.driver.bindAddress", "0.0.0.0")
        .config("spark.driver.port", os.environ.get("SPARK_DRIVER_PORT", "7078"))
        .config("spark.driver.blockManager.port", os.environ.get("SPARK_BLOCK_PORT", "7079"))
        .config("spark.executor.memory", os.environ.get("SPARK_EXECUTOR_MEMORY", "512m"))
        .config(
            "spark.executor.memoryOverhead",
            os.environ.get("SPARK_EXECUTOR_MEMORY_OVERHEAD", "512m"),
        )
        .config("spark.executor.cores", "1")
        .config("spark.cores.max", str(workers))
        .config("spark.task.cpus", "1")
        .config("spark.executorEnv.PYTHONPATH", "/opt/airflow")
        .config("spark.executorEnv.DATA_DIR", os.environ.get("DATA_DIR", "/opt/airflow/data"))
        .config("spark.executorEnv.HF_HOME", str(models_dir() / "huggingface"))
        .config("spark.executorEnv.WHISPER_MODEL", os.environ.get("WHISPER_MODEL", "Systran/faster-whisper-tiny.en"))
        .config(
            "spark.executorEnv.YOLO_ONNX_URL",
            os.environ.get(
                "YOLO_ONNX_URL",
                "https://huggingface.co/Kalray/yolov8/resolve/main/yolov8n.onnx",
            ),
        )
        .config("spark.executorEnv.CLIP_SECONDS", os.environ.get("CLIP_SECONDS", "180"))
        .config("spark.executorEnv.CLIP_OVERLAP_SECONDS", os.environ.get("CLIP_OVERLAP_SECONDS", "10"))
        .config(
            "spark.executorEnv.DEFINITIONS_PATH",
            os.environ.get("DEFINITIONS_PATH", "/opt/airflow/definitions.yaml"),
        )
        .config(
            "spark.executorEnv.DEFINITIONS_LEARNED_PATH",
            os.environ.get("DEFINITIONS_LEARNED_PATH", "/opt/airflow/data/definitions_learned.yaml"),
        )
        .config("spark.executorEnv.OMP_NUM_THREADS", "1")
        .config("spark.executorEnv.ORT_NUM_THREADS", "1")
        .config("spark.python.worker.reuse", "true")
        .getOrCreate()
    )
    try:
        spark.sparkContext.parallelize(todo, len(todo)).map(_process_splice).collect()
    finally:
        spark.stop()


def _prepare_media(video: dict) -> None:
    original = Path(video["original_uri"])
    audio = Path(video["audio_uri"])
    if not audio.exists() or audio.stat().st_size < 128:
        extract_audio(original, audio)
    (video_work_dir(video["video_id"]) / "clips").mkdir(parents=True, exist_ok=True)


def _worker_models() -> dict:
    global _WORKER_MODELS
    if _WORKER_MODELS is not None:
        return _WORKER_MODELS
    whisper = detector = None
    try:
        from workers.asr import load_whisper

        whisper = load_whisper()
    except Exception:
        whisper = None
    try:
        from workers.detect import load_yolo

        detector = load_yolo()
    except Exception:
        detector = None
    _WORKER_MODELS = {"whisper": whisper, "detector": detector}
    return _WORKER_MODELS


def _process_splice(payload: tuple) -> dict:
    video, index, _start, _end = payload
    clip_id = f"clip_{index:04d}"
    if clip_analysis_done(video["video_id"], clip_id):
        path = video_work_dir(video["video_id"]) / "clips" / clip_id / "clip.json"
        return json.loads(path.read_text())
    models = _worker_models()
    return _cut_and_analyze_clip(payload, models["whisper"], models["detector"])


def _cut_and_analyze_clip(payload: tuple, whisper=None, detector=None, captioner=None) -> dict:
    video, index, start_s, end_s = payload
    record = cut_window(video, index, start_s, end_s)
    return _analyze_clip(record, whisper, detector)


def _analyze_clip(record: dict, whisper=None, detector=None, captioner=None) -> dict:
    from workers.asr import transcribe_wav
    from workers.audio_signals import audio_signals
    from workers.detect import detect_clip_entities
    from workers.local_tag import attach_local_descriptions
    from workers.metadata import keyword_hits, repeated_commands

    transcript, segments, error = transcribe_wav(whisper, Path(record["audio_uri"]))
    signals = audio_signals(Path(record["audio_uri"]))
    entities = detect_clip_entities(detector, record)
    record["transcript"] = transcript
    record["transcript_segments"] = segments
    if error:
        record["asr_error"] = error
    record["keyword_hits"] = keyword_hits(transcript, segments, int(record.get("start_ms") or 0))
    record["signals"] = {
        **signals,
        "repeated_commands": repeated_commands(transcript),
        "yelling": signals["loud_speech"],
    }
    record["entities"] = entities
    attach_local_descriptions(record)
    record["model"] = {
        "asr": os.environ.get("WHISPER_MODEL", "Systran/faster-whisper-tiny.en"),
        "vad": "silero",
        "detector": "yolov8n-onnx-splice",
        "captioner": "skipped",
        "clothing": "hsv_torso",
        "tagger": "local",
        "audio": "rms_windows",
        "engine": "spark",
    }
    tagged = tag_and_summarize_local(record)
    save_clip_record(record["video_id"], tagged)
    return tagged
