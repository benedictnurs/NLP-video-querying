from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PERSON_COLOR = (0, 220, 90)
VEHICLE_COLOR = (255, 170, 40)
OBJECT_COLOR = (80, 170, 255)
TEXT_BG = (0, 0, 0)


def appearance_tag(item: dict) -> str:
    if item.get("type") == "person":
        return "person"
    if item.get("image_tag"):
        return str(item["image_tag"]).strip()
    return item.get("label") or item.get("type") or "object"


def annotate_image(image_path: Path, detections: list[dict], dest: Path | None = None) -> Path | None:
    if not image_path.exists():
        return None
    dest = dest or image_path
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _font(image)
    stroke = max(3, image.height // 240)
    drawn = 0
    for item in detections:
        box = item.get("box") or []
        if len(box) != 4:
            continue
        x1, y1, x2, y2 = [int(v) for v in box]
        color = _color(item.get("type"))
        draw.rectangle([x1, y1, x2, y2], outline=color, width=stroke)
        _draw_label(draw, appearance_tag(item), x1, y1, font, color, image.height)
        drawn += 1
    if drawn == 0:
        _draw_label(draw, "no detections", 8, 8, font, OBJECT_COLOR, image.height)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, quality=95)
    return dest


def stamp_clock(image_path: Path, clock: str, dest: Path | None = None) -> Path | None:
    if not image_path.exists():
        return None
    dest = dest or image_path
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _font(image)
    text = (clock or "").strip()
    if not text:
        return dest
    pad = 6
    box = draw.textbbox((pad, pad), text, font=font)
    width = box[2] - box[0]
    height = box[3] - box[1]
    x = pad
    y = max(pad, image.height - height - pad * 2)
    draw.rectangle([x - 2, y - 2, x + width + 4, y + height + 4], fill=TEXT_BG)
    draw.text((x, y), text, fill=(255, 220, 40), font=font)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, quality=95)
    return dest


def stamp_label(image_path: Path, label: str, dest: Path | None = None) -> Path | None:
    if not image_path.exists():
        return None
    dest = dest or image_path
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _font(image)
    _draw_label(draw, label, 6, 6, font, PERSON_COLOR, image.height)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, quality=95)
    return dest


def _draw_label(draw, label: str, x: int, y: int, font, color, image_height: int) -> None:
    text = (label or "").strip() or "object"
    text_box = draw.textbbox((x, y), text, font=font)
    text_h = text_box[3] - text_box[1]
    top = y - text_h - 6
    if top < 0:
        top = min(y + 4, max(0, image_height - text_h - 2))
    text_box = draw.textbbox((x, top), text, font=font)
    draw.rectangle(text_box, fill=TEXT_BG)
    draw.text((text_box[0], text_box[1]), text, fill=color, font=font)


def _color(kind: str | None) -> tuple[int, int, int]:
    if kind == "person":
        return PERSON_COLOR
    if kind == "vehicle":
        return VEHICLE_COLOR
    return OBJECT_COLOR


def _font(image: Image.Image):
    size = max(16, min(36, image.height // 28))
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()
