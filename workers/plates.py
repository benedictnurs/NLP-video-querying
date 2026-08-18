from __future__ import annotations

from pathlib import Path

from PIL import Image

MIN_CROP = 24


def crop_plate_region(image_path: Path, box: list[int], dest: Path) -> dict:
    crop = _lower_vehicle_crop(image_path, box)
    if crop is None:
        return {"plate_crop_uri": None, "needs_plate_read": True}
    dest.parent.mkdir(parents=True, exist_ok=True)
    crop.save(dest, quality=95)
    from workers.annotate import stamp_label

    stamp_label(dest, "plate")
    return {
        "plate_crop_uri": str(dest),
        "needs_plate_read": True,
    }


def _lower_vehicle_crop(image_path: Path, box: list[int]) -> Image.Image | None:
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
    pad_x = int((x2 - x1) * 0.08)
    plate_y1 = y1 + int((y2 - y1) * 0.55)
    crop = image.crop((x1 + pad_x, plate_y1, x2 - pad_x, y2))
    if crop.size[0] < MIN_CROP or crop.size[1] < 12:
        return None
    return crop
