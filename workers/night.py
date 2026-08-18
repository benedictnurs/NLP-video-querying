from __future__ import annotations

from PIL import Image

DARK = 78


def is_night(image: Image.Image) -> tuple[bool, float]:
    mean = _luma_mean(image)
    return mean < DARK, mean


def _luma_mean(image: Image.Image) -> float:
    gray = image.convert("L")
    hist = gray.histogram()
    total = sum(hist) or 1
    return sum(index * count for index, count in enumerate(hist)) / total
