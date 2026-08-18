from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from workers.paths import models_dir

HF = "https://huggingface.co/Xenova/vit-gpt2-image-captioning/resolve/main"
ENCODER_URL = os.environ.get(
    "CAPTION_ENCODER_URL",
    f"{HF}/onnx/encoder_model_quantized.onnx",
)
DECODER_URL = os.environ.get(
    "CAPTION_DECODER_URL",
    f"{HF}/onnx/decoder_model_quantized.onnx",
)
TOKENIZER_URL = os.environ.get(
    "CAPTION_TOKENIZER_URL",
    f"{HF}/tokenizer.json",
)
SIZE = 224
MEAN = 0.5
STD = 0.5
BOS = 50256
EOS = 50256
MAX_TOKENS = 16


def load_captioner():
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError:
        return None
    root = models_dir() / "caption"
    encoder_path = root / "encoder_quantized.onnx"
    decoder_path = root / "decoder_quantized.onnx"
    tokenizer_path = root / "tokenizer.json"
    try:
        _download(ENCODER_URL, encoder_path, min_bytes=1_000_000)
        _download(DECODER_URL, decoder_path, min_bytes=1_000_000)
        _download(TOKENIZER_URL, tokenizer_path, min_bytes=1000)
        encoder = ort.InferenceSession(str(encoder_path), providers=["CPUExecutionProvider"])
        decoder = ort.InferenceSession(str(decoder_path), providers=["CPUExecutionProvider"])
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        return {"encoder": encoder, "decoder": decoder, "tokenizer": tokenizer}
    except Exception:
        return None


def caption_crop(captioner, image_path: Path, box: list[int] | None = None) -> str:
    if captioner is None or not image_path.exists():
        return ""
    try:
        image = Image.open(image_path).convert("RGB")
        if box and len(box) == 4:
            x1, y1, x2, y2 = [int(v) for v in box]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(image.width, x2), min(image.height, y2)
            if x2 - x1 >= 16 and y2 - y1 >= 16:
                image = image.crop((x1, y1, x2, y2))
        pixels = _preprocess(image)
        encoder = captioner["encoder"]
        feeds = {inp.name: pixels for inp in encoder.get_inputs()}
        hidden = encoder.run(None, feeds)[0]
        token_ids = _decode(captioner["decoder"], hidden)
        text = captioner["tokenizer"].decode(token_ids, skip_special_tokens=True)
        return " ".join(text.split()).strip(" .,")
    except Exception:
        return ""


def _decode(decoder, hidden: np.ndarray) -> list[int]:
    tokens = [BOS]
    for _ in range(MAX_TOKENS):
        input_ids = np.array([tokens], dtype=np.int64)
        feeds = {}
        for inp in decoder.get_inputs():
            name = inp.name.lower()
            if "input_ids" in name:
                feeds[inp.name] = input_ids
            elif "hidden" in name or "encoder" in name:
                if "mask" in name:
                    feeds[inp.name] = np.ones((hidden.shape[0], hidden.shape[1]), dtype=np.int64)
                else:
                    feeds[inp.name] = hidden.astype(np.float32)
            elif "mask" in name:
                feeds[inp.name] = np.ones_like(input_ids, dtype=np.int64)
        logits = decoder.run(None, feeds)[0]
        if logits.ndim == 3:
            next_id = int(np.argmax(logits[0, -1]))
        else:
            next_id = int(np.argmax(logits[0]))
        if next_id == EOS and len(tokens) > 1:
            break
        tokens.append(next_id)
    return [tok for tok in tokens[1:] if tok != EOS]


def _preprocess(image: Image.Image) -> np.ndarray:
    image = image.resize((SIZE, SIZE), Image.Resampling.BILINEAR)
    array = np.asarray(image).astype(np.float32) / 255.0
    array = (array - MEAN) / STD
    return np.transpose(array, (2, 0, 1))[None, ...]


def _download(url: str, dest: Path, min_bytes: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > min_bytes:
        return
    tmp = dest.with_suffix(dest.suffix + ".partial")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
