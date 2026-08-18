from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from workers.annotate import annotate_image, appearance_tag, stamp_clock
from workers.clock import format_clock
from workers.clothing import describe_person_clothing
from workers.paths import models_dir
from workers.plates import crop_plate_region
from workers.splice import build_frame_splice, compose_splice

YOLO_URL = os.environ.get(
    "YOLO_ONNX_URL",
    "https://huggingface.co/Kalray/yolov8/resolve/main/yolov8n.onnx",
)
COCO_KEEP = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    9: "traffic light",
    11: "stop sign",
    24: "backpack",
    26: "handbag",
    28: "suitcase",
    39: "bottle",
    43: "knife",
    67: "cell phone",
}
VEHICLE_IDS = {1, 2, 3, 5, 7}
INPUT_SIZE = 640
SCORE_THRESH = 0.28
NIGHT_SCORE_THRESH = 0.22
IOU_THRESH = 0.45


def load_yolo():
    return _load_yolo()


def _load_yolo():
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    name = Path(YOLO_URL.split("?", 1)[0]).name or "yolov8n.onnx"
    weights = models_dir() / name
    try:
        _download(YOLO_URL, weights)
        return ort.InferenceSession(str(weights), providers=["CPUExecutionProvider"])
    except Exception:
        return None


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return
    tmp = dest.with_suffix(".partial")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)


def detect_clip_entities(session, record: dict) -> list[dict]:
    if session is None:
        record["splice_uri"] = None
        record["tagged_splice_uri"] = None
        record["entities"] = []
        return []
    entities, splice = _detect_clip(session, record)
    record["entities"] = entities
    record["splice_uri"] = splice.get("splice_uri")
    record["tagged_splice_uri"] = splice.get("tagged_splice_uri")
    record["splice_cells"] = splice.get("cell_count") or 0
    record["cells"] = [
        {
            "index": cell.get("index"),
            "at_s": cell.get("at_s"),
            "start_ms": cell.get("start_ms"),
            "clock": cell.get("clock"),
            "uri": cell.get("uri"),
        }
        for cell in splice.get("cells") or []
    ]
    return entities


def _detect_clip(session, record: dict) -> tuple[list[dict], dict]:
    clip = Path(record["clip_uri"])
    start_s = record["start_ms"] / 1000.0
    end_s = record["end_ms"] / 1000.0
    duration = max(end_s - start_s, 0.1)
    frame_count = max(4, min(6, int(duration / 30) or 4))
    clip_dir = clip.parent
    frame_dir = clip_dir / "frames"
    clothing_dir = clip_dir / "clothing"
    frame_dir.mkdir(parents=True, exist_ok=True)
    clothing_dir.mkdir(parents=True, exist_ok=True)
    splice = build_frame_splice(
        clip,
        clip_dir / "splice.jpg",
        duration,
        frame_count,
        clip_start_ms=int(record.get("start_ms") or 0),
    )
    detections_by_cell: list[list[dict]] = []
    night_cells = 0
    for cell in splice.get("cells") or []:
        jpeg = Path(cell["uri"])
        cell_hits = []
        thresh = NIGHT_SCORE_THRESH if cell.get("night") else SCORE_THRESH
        for item in _infer(session, jpeg, thresh):
            item["frame_uri"] = str(jpeg)
            item["frame_index"] = cell["index"]
            item["at_s"] = cell["at_s"]
            item["splice_uri"] = splice.get("splice_uri")
            cell_hits.append(item)
        if cell.get("night"):
            night_cells += 1
        _stamp_clothing(cell_hits)
        detections_by_cell.append(cell_hits)
    record["night"] = night_cells >= max(1, len(splice.get("cells") or []) // 2)
    entities = _with_clothing(_clip_inventory(detections_by_cell), clothing_dir)
    _with_plate_crops(entities, clothing_dir)
    _stamp_image_tags(entities)
    for hits in detections_by_cell:
        _stamp_image_tags(hits)
    labeled = _annotate_cells(splice, detections_by_cell, clip_dir / "splice.jpg")
    splice["splice_uri"] = labeled
    splice["tagged_splice_uri"] = labeled
    for item in entities:
        item["tagged_splice_uri"] = labeled
        item["splice_uri"] = labeled
    return entities, splice


def _infer(session, image_path: Path, thresh: float = SCORE_THRESH) -> list[dict]:
    image = Image.open(image_path).convert("RGB")
    tensor, ratio, pad = _letterbox(image)
    input_name = session.get_inputs()[0].name
    output = session.run(None, {input_name: tensor})[0]
    return _parse_output(output, image.size, ratio, pad, thresh)


def _letterbox(image: Image.Image) -> tuple[np.ndarray, float, tuple[float, float]]:
    width, height = image.size
    scale = min(INPUT_SIZE / width, INPUT_SIZE / height)
    new_w, new_h = int(round(width * scale)), int(round(height * scale))
    resized = image.resize((new_w, new_h), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (INPUT_SIZE, INPUT_SIZE), (114, 114, 114))
    pad_x = (INPUT_SIZE - new_w) / 2
    pad_y = (INPUT_SIZE - new_h) / 2
    canvas.paste(resized, (int(pad_x), int(pad_y)))
    array = np.asarray(canvas).astype(np.float32) / 255.0
    tensor = np.transpose(array, (2, 0, 1))[None, ...]
    return tensor, scale, (pad_x, pad_y)


def _parse_output(output: np.ndarray, orig_size: tuple[int, int], scale: float, pad: tuple[float, float], thresh: float = SCORE_THRESH) -> list[dict]:
    pred = np.squeeze(output)
    if pred.ndim != 2:
        return []
    if pred.shape[0] in (84, 85) and pred.shape[1] > pred.shape[0]:
        pred = pred.T
    boxes = []
    scores = []
    classes = []
    for row in pred:
        class_scores = row[4:]
        class_id = int(np.argmax(class_scores))
        score = float(class_scores[class_id])
        min_score = thresh if class_id in {0, 2, 3, 5, 7} else max(thresh, 0.45)
        if score < min_score or class_id not in COCO_KEEP:
            continue
        cx, cy, w, h = row[:4]
        x1 = (cx - w / 2 - pad[0]) / scale
        y1 = (cy - h / 2 - pad[1]) / scale
        x2 = (cx + w / 2 - pad[0]) / scale
        y2 = (cy + h / 2 - pad[1]) / scale
        boxes.append([x1, y1, x2, y2])
        scores.append(score)
        classes.append(class_id)
    keep = _nms(np.array(boxes), np.array(scores)) if boxes else []
    results = []
    orig_w, orig_h = orig_size
    for idx in keep:
        x1, y1, x2, y2 = boxes[idx]
        box = [
            int(max(0, x1)),
            int(max(0, y1)),
            int(min(orig_w, x2)),
            int(min(orig_h, y2)),
        ]
        kind = _entity_type(classes[idx])
        item = {
            "type": kind,
            "label": COCO_KEEP[classes[idx]],
            "confidence": round(float(scores[idx]), 3),
            "box": box,
        }
        results.append(item)
    return results


def _nms(boxes: np.ndarray, scores: np.ndarray) -> list[int]:
    if len(boxes) == 0:
        return []
    order = scores.argsort()[::-1]
    keep = []
    while len(order):
        i = int(order[0])
        keep.append(i)
        if len(order) == 1:
            break
        rest = order[1:]
        ious = _iou(boxes[i], boxes[rest])
        order = rest[ious < IOU_THRESH]
    return keep


def _iou(box: np.ndarray, others: np.ndarray) -> np.ndarray:
    x1 = np.maximum(box[0], others[:, 0])
    y1 = np.maximum(box[1], others[:, 1])
    x2 = np.minimum(box[2], others[:, 2])
    y2 = np.minimum(box[3], others[:, 3])
    inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    area_box = (box[2] - box[0]) * (box[3] - box[1])
    area_others = (others[:, 2] - others[:, 0]) * (others[:, 3] - others[:, 1])
    return inter / np.maximum(area_box + area_others - inter, 1e-6)


def _clip_inventory(detections_by_cell: list[list[dict]]) -> list[dict]:
    best_people: list[dict] = []
    best_score = -1.0
    unique: dict[tuple[str, str], dict] = {}
    for cell_hits in detections_by_cell:
        people = [item for item in cell_hits if item["type"] == "person"]
        score = len(people) + (sum(item["confidence"] for item in people) / 10.0)
        if score > best_score:
            best_people = people
            best_score = score
        for item in cell_hits:
            if item["type"] == "person":
                continue
            key = (item["type"], item["label"])
            current = unique.get(key)
            if current is None or item["confidence"] > current["confidence"]:
                unique[key] = item
    combined = [*best_people, *unique.values()]
    for index, item in enumerate(combined):
        item["id"] = f"{item['type']}_{index}"
    return combined


def _with_clothing(detections: list[dict], frame_dir: Path) -> list[dict]:
    for item in detections:
        if item["type"] != "person":
            continue
        frame = Path(item.get("frame_uri") or "")
        if not frame.exists():
            item.update(_unknown_clothing())
            continue
        crop = frame_dir / f"{item['id']}_torso.jpg"
        item.update(describe_person_clothing(frame, item["box"], crop))
    return detections


def _stamp_clothing(hits: list[dict]) -> None:
    for item in hits:
        if item.get("type") != "person" or item.get("upper_clothing_color"):
            continue
        frame = Path(item.get("frame_uri") or "")
        if not frame.exists():
            item.update(_unknown_clothing())
            continue
        item.update(describe_person_clothing(frame, item["box"]))


def _unknown_clothing() -> dict:
    return {
        "upper_clothing_color": "unknown",
        "clothing_confidence": 0.0,
        "clothing_source": "hsv",
        "needs_vision": True,
        "uniform_like": False,
    }


def _with_plate_crops(detections: list[dict], dest_dir: Path) -> list[dict]:
    for item in detections:
        if item.get("type") != "vehicle":
            continue
        frame = Path(item.get("frame_uri") or "")
        if not frame.exists():
            item["needs_plate_read"] = True
            continue
        crop = dest_dir / f"{item['id']}_plate.jpg"
        item.update(crop_plate_region(frame, item.get("box") or [], crop))
    return detections


def _stamp_image_tags(hits: list[dict]) -> None:
    for item in hits:
        if not item.get("image_tag"):
            item["image_tag"] = appearance_tag(item)


def _annotate_cells(splice: dict, detections_by_cell: list[list[dict]], dest: Path) -> str | None:
    cells = splice.get("cells") or []
    for cell, hits in zip(cells, detections_by_cell):
        jpeg = Path(cell["uri"])
        path = annotate_image(jpeg, hits, jpeg)
        clock = cell.get("clock") or format_clock(cell.get("start_ms"))
        label = f"cell_{int(cell.get('index') or 0):02d} {clock}"
        if path is not None:
            stamp_clock(path, label, path)
            cell["tagged_uri"] = str(path)
            cell["uri"] = str(path)
            for item in hits:
                item["tagged_frame_uri"] = str(path)
                item["frame_uri"] = str(path)
    return compose_splice(cells, dest, uri_key="uri")


def _entity_type(class_id: int) -> str:
    if class_id == 0:
        return "person"
    if class_id in VEHICLE_IDS:
        return "vehicle"
    return "object"
