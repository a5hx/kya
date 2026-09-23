"""Face detection and recognition with OpenCV's YuNet + SFace (ONNX, CPU, pip-only).

Models are fetched once from the official opencv_zoo repository into ./models.
"""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from . import liveness
from . import template as ft

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
MODELS = {
    "yunet": ("face_detection_yunet_2023mar.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
              "face_detection_yunet_2023mar.onnx"),
    "sface": ("face_recognition_sface_2021dec.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/"
              "face_recognition_sface_2021dec.onnx"),
}


def ensure_models() -> dict[str, Path]:
    MODELS_DIR.mkdir(exist_ok=True)
    paths = {}
    for key, (name, url) in MODELS.items():
        path = MODELS_DIR / name
        if not path.exists() or path.stat().st_size < 10_000:
            _download(url, path)
        paths[key] = path
    return paths


def _download(url: str, path: Path) -> None:
    tmp = path.with_suffix(".part")
    try:
        import httpx

        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    except Exception:  # some networks time out on httpx; curl ships with Windows 10+
        subprocess.run(["curl", "-sSL", "--fail", "-o", str(tmp), url], check=True, timeout=600)
    tmp.replace(path)


def decode_image(data: str | bytes) -> np.ndarray:
    """Accepts raw bytes or a data URL; returns a BGR image."""
    import cv2

    if isinstance(data, str):
        data = base64.b64decode(data.split(",", 1)[1] if data.startswith("data:") else data)
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode image")
    return img


class FacePipeline:
    def __init__(self):
        import cv2

        paths = ensure_models()
        self.cv2 = cv2
        self.detector = cv2.FaceDetectorYN.create(str(paths["yunet"]), "", (320, 320), 0.8, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(paths["sface"]), "")

    def detect(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(img)
        return faces if faces is not None else np.zeros((0, 15), np.float32)

    def embed(self, img: np.ndarray, face_row: np.ndarray) -> np.ndarray:
        aligned = self.recognizer.alignCrop(img, face_row)
        return ft.normalize(self.recognizer.feature(aligned))

    def single_face(self, img: np.ndarray) -> tuple[np.ndarray | None, str]:
        faces = self.detect(img)
        if len(faces) == 0:
            return None, "no face found"
        if len(faces) > 1:
            return None, "more than one face found"
        return faces[0], "ok"

    def analyse_frames(self, frames: list[str], direction: str) -> dict[str, Any]:
        """Liveness over the frame burst + an embedding from the most frontal frame."""
        track, best = [], None
        for data in frames:
            try:
                img = decode_image(data)
            except ValueError:
                track.append({"faces": 0, "yaw": None})
                continue
            faces = self.detect(img)
            if len(faces) == 1:
                yaw = liveness.yaw_from_landmarks(faces[0])
                track.append({"faces": 1, "yaw": yaw})
                if best is None or abs(yaw) < best[0]:
                    best = (abs(yaw), img, faces[0])
            else:
                track.append({"faces": int(len(faces)), "yaw": None})
        result = liveness.evaluate(track, direction)
        embedding = self.embed(best[1], best[2]) if best else None
        return {"liveness": result, "embedding": embedding}
