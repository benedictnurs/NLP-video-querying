from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

CLOTHING_CONF_MIN = 0.45
MIN_CROP = 20

# PIL HSV hue is 0–255 (~1.4° per unit).
HUE_BINS = (
    (15, "red"),
    (40, "orange"),
    (70, "yellow"),
    (160, "green"),
    (250, "blue"),
    (290, "purple"),
    (345, "pink"),
)


def describe_person_clothing(image_path: Path, box: list[int], crop_dest: Path | None = None) -> dict:
    crop = _torso_crop(image_path, box)
    if crop is None:
        return _unknown(crop_dest)
    color, confidence = _dominant_color(crop)
    if crop_dest is not None:
        from workers.annotate import stamp_label

        crop_dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(crop_dest, quality=95)
        stamp_label(crop_dest, f"{color} torso" if color != "unknown" else "person torso")
    needs_vision = color == "unknown" or confidence < CLOTHING_CONF_MIN
    return {
        "upper_clothing_color": color,
        "clothing_confidence": confidence,
        "clothing_source": "hsv",
        "needs_vision": needs_vision,
        "uniform_like": color in {"blue", "black"} and confidence >= 0.5,
        "crop_uri": str(crop_dest) if crop_dest is not None else None,
    }


def _unknown(crop_dest: Path | None) -> dict:
    return {
        "upper_clothing_color": "unknown",
        "clothing_confidence": 0.0,
        "clothing_source": "hsv",
        "needs_vision": True,
        "uniform_like": False,
        "crop_uri": str(crop_dest) if crop_dest is not None else None,
    }


def _torso_crop(image_path: Path, box: list[int]) -> Image.Image | None:
    try:
        image = Image.open(image_path).convert("RGB")
    except OSError:
        return None
    x1, y1, x2, y2 = [int(v) for v in box]
    width, height = image.size
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 - x1 < MIN_CROP or y2 - y1 < MIN_CROP:
        return None
    pad_x = int((x2 - x1) * 0.12)
    torso_y2 = y1 + int((y2 - y1) * 0.48)
    crop = image.crop((x1 + pad_x, y1, x2 - pad_x, max(y1 + MIN_CROP, torso_y2)))
    if crop.size[0] < MIN_CROP or crop.size[1] < MIN_CROP:
        return None
    return crop


def _dominant_color(crop: Image.Image) -> tuple[str, float]:
    hsv = np.asarray(crop.convert("HSV"))
    hue = hsv[:, :, 0].astype(np.int32)
    sat = hsv[:, :, 1].astype(np.int32)
    val = hsv[:, :, 2].astype(np.int32)
    pixels = hue.size
    if pixels == 0:
        return "unknown", 0.0

    achromatic = sat < 40
    chromatic = ~achromatic
    achromatic_share = float(achromatic.mean())
    if achromatic_share >= 0.62:
        values = val[achromatic]
        black = float((values < 50).mean()) if values.size else 0.0
        white = float((values >= 170).mean()) if values.size else 0.0
        gray = max(0.0, 1.0 - black - white)
        color, share = max(
            (("black", black), ("white", white), ("gray", gray)),
            key=lambda item: item[1],
        )
        return color, round(min(achromatic_share * share + 0.15, 1.0), 3)

    hues = hue[chromatic]
    degrees = hues.astype(np.float32) * (360.0 / 255.0)
    counts = {name: 0 for _, name in HUE_BINS}
    counts["red"] = 0
    for value in degrees:
        counts[_hue_name(float(value))] += 1
    color, count = max(counts.items(), key=lambda item: item[1])
    confidence = count / max(chromatic.sum(), 1)
    return color, round(float(confidence), 3)


def _hue_name(degree: float) -> str:
    wrapped = degree % 360
    if wrapped < 15 or wrapped >= 345:
        return "red"
    for bound, name in HUE_BINS:
        if wrapped < bound:
            return name
    return "red"
