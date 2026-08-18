from __future__ import annotations

import json
import subprocess
from pathlib import Path


def ffprobe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    fmt = payload.get("format") or {}
    fps = None
    raw_fps = stream.get("r_frame_rate")
    if raw_fps and "/" in raw_fps:
        num, den = raw_fps.split("/", 1)
        if float(den) != 0:
            fps = round(float(num) / float(den), 3)
    duration = fmt.get("duration")
    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": fps,
        "duration_s": float(duration) if duration else None,
    }


def run_ffmpeg(args: list[str]) -> str:
    result = subprocess.run(
        ["ffmpeg", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:] or "ffmpeg failed")
    return result.stderr


def extract_audio(source: Path, dest: Path) -> None:
    run_ffmpeg(
        [
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(dest),
        ]
    )


PROCESSING_VF = "scale='min(854,iw)':-2,fps=5"


def make_processing_copy(source: Path, dest: Path) -> None:
    run_ffmpeg(
        [
            "-y",
            "-i",
            str(source),
            "-vf",
            PROCESSING_VF,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-an",
            str(dest),
        ]
    )


def cut_clip(source: Path, dest: Path, start_s: float, duration_s: float) -> None:
    cut_processing_clip(source, dest, start_s, duration_s, already_processed=True)


def cut_processing_clip(
    source: Path,
    dest: Path,
    start_s: float,
    duration_s: float,
    already_processed: bool = False,
) -> None:
    args = [
        "-y",
        "-ss",
        f"{start_s:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration_s:.3f}",
    ]
    if not already_processed:
        args += ["-vf", PROCESSING_VF]
    args += [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-an",
        str(dest),
    ]
    run_ffmpeg(args)


def cut_audio(source: Path, dest: Path, start_s: float, duration_s: float) -> None:
    run_ffmpeg(
        [
            "-y",
            "-ss",
            f"{start_s:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration_s:.3f}",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(dest),
        ]
    )


def extract_jpeg(source: Path, dest: Path, at_s: float) -> None:
    run_ffmpeg(
        [
            "-y",
            "-ss",
            f"{max(at_s, 0):.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(dest),
        ]
    )
