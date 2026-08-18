from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from workers.annotate import annotate_image, appearance_tag
from workers.caption import caption_crop, load_captioner
from workers.clips import load_clip_records, save_clip_record, save_video_status
from workers.clothing import describe_person_clothing
from workers.local_tag import attach_local_descriptions
from workers.paths import models_dir
from workers.plates import crop_plate_region
from workers.splice import build_frame_splice, compose_splice

YOLO_URL = os.environ.get(
    "YOLO_ONNX_URL",
    "https://huggingface.co/Kalray/yolov8/resolve/main/yolov8s.onnx",
)
COCO_KEEP = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    6: "train",
    7: "truck",
    8: "boat",
    9: "traffic light",
    11: "stop sign",
    13: "bench",
    14: "bird",
    15: "cat",
    16: "dog",
    24: "backpack",
    25: "umbrella",
    26: "handbag",
    27: "tie",
    28: "suitcase",
    32: "sports ball",
    34: "baseball bat",
    39: "bottle",
    43: "knife",
    63: "laptop",
    67: "cell phone",
    73: "book",
}
VEHICLE_IDS = {1, 2, 3, 5, 6, 7, 8}
INPUT_SIZE = 640
SCORE_THRESH = 0.28
NIGHT_SCORE_THRESH = 0.22
IOU_THRESH = 0.45


def load_yolo():
    return _load_yolo()


def detect_video_clips(video: dict) -> dict:
    session = load_yolo()
    captioner = load_captioner()
    for record in load_clip_records(video["video_id"]):
        entities = []
        if session is not None:
            entities = detect_clip_entities(session, record, captioner)
        models = dict(record.get("model") or {})
        models["detector"] = _detector_name(session)
        models["captioner"] = "vit-gpt2-onnx" if captioner is not None else "unavailable"
        record["entities"] = entities
        attach_local_descriptions(record)
        record["model"] = models
        save_clip_record(video["video_id"], record)
    return save_video_status(video, "detected")


def _load_yolo():
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    name = Path(YOLO_URL.split("?", 1)[0]).name or "yolov8s.onnx"
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


def detect_clip_entities(session, record: dict, captioner=None) -> list[dict]:
    if session is None:
        record["splice_uri"] = None
        record["tagged_splice_uri"] = None
        record["entities"] = []
        return []
    entities, splice = _detect_clip(session, record, captioner)
    record["entities"] = entities
    record["splice_uri"] = splice.get("splice_uri")
    record["tagged_splice_uri"] = splice.get("tagged_splice_uri")
    record["splice_cells"] = splice.get("cell_count") or 0
    return entities


def _detector_name(session) -> str:
    if session is None:
        return "unavailable"
    name = Path(YOLO_URL.split("?", 1)[0]).stem or "yolov8s"
    return f"{name}-onnx"


def _detect_clip(session, record: dict, captioner=None) -> tuple[list[dict], dict]:
    clip = Path(record["clip_uri"])
    start_s = record["start_ms"] / 1000.0
    end_s = record["end_ms"] / 1000.0
    duration = max(end_s - start_s, 0.1)
    frame_count = max(6, min(12, int(duration / 15) or 6))
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
    _with_captions(entities, captioner)
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
        if score < thresh or class_id not in COCO_KEEP:
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


def _with_captions(detections: list[dict], captioner) -> list[dict]:
    if captioner is None:
        return detections
    for item in detections:
        if item.get("type") != "person":
            continue
        frame = Path(item.get("frame_uri") or "")
        text = caption_crop(captioner, frame, item.get("box"))
        if not text:
            continue
        item["caption"] = text
        item["image_tag"] = text
        item["clothing_source"] = "caption"
        item["needs_vision"] = False
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
        if path is not None:
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
