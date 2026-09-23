"""Active liveness: a randomised head-turn challenge.

The browser sends ~15 frames over ~2.5 s. For each frame we estimate a yaw
proxy from YuNet's five landmarks:

    yaw = (nose_x - mid_eye_x) / inter_eye_distance

A static photo or printout gives a flat yaw track, and it cannot follow a
direction it does not know in advance. Limitations are stated in the README:
this does not stop a real-time deepfake or an injected virtual camera feed.
"""
from __future__ import annotations

import statistics
from typing import Any

MIN_FACE_FRAMES = 6
FRONTAL_MAX = 0.28
TURN_DELTA = 0.22
STATIC_STD = 0.015
# Frames arrive un-mirrored from the camera. When the user turns to *their*
# left, their nose moves towards image-right, so yaw increases.
DIRECTION_SIGN = {"LEFT": 1.0, "RIGHT": -1.0}


def yaw_from_landmarks(face_row) -> float:
    """face_row: YuNet output [x, y, w, h, reye_x, reye_y, leye_x, leye_y, nose_x, nose_y, ...]."""
    rx, lx, nx = float(face_row[4]), float(face_row[6]), float(face_row[8])
    inter = abs(lx - rx) or 1.0
    return (nx - (rx + lx) / 2) / inter


def evaluate(track: list[dict[str, Any]], direction: str) -> dict[str, Any]:
    """track: per-frame dicts with ``faces`` (count) and ``yaw`` (when exactly one face)."""
    sign = DIRECTION_SIGN[direction]
    usable = [f for f in track if f.get("faces") == 1 and f.get("yaw") is not None]
    yaws = [round(f["yaw"], 3) for f in usable]
    base = {"direction": direction, "frames": len(track), "face_frames": len(usable), "yaw_track": yaws}

    def fail(reason: str) -> dict[str, Any]:
        return {**base, "passed": False, "reason": reason}

    if any(f.get("faces", 0) > 1 for f in track):
        return fail("more than one face in view")
    if len(usable) < MIN_FACE_FRAMES:
        return fail(f"face visible in only {len(usable)}/{len(track)} frames")
    start = statistics.median(yaws[:3])
    if abs(start) > FRONTAL_MAX:
        return fail("challenge must start facing the camera")
    if statistics.pstdev(yaws) < STATIC_STD:
        return fail("no head movement (static image or photo)")
    toward = max(sign * (y - start) for y in yaws)
    away = max(-sign * (y - start) for y in yaws)
    base.update(start=round(start, 3), toward=round(toward, 3), away=round(away, 3))
    if toward < TURN_DELTA:
        if away >= TURN_DELTA:
            return fail(f"turned the wrong way (asked {direction})")
        return fail(f"did not turn {direction} far enough")
    return {**base, "passed": True, "reason": f"followed the random '{direction}' challenge"}


def synthetic_track(direction: str | None, frames: int = 15, faces: int = 1, jitter: float = 0.004) -> list[dict]:
    """Deterministic landmark tracks for tests/eval: a turn, a static photo, or the wrong way."""
    out = []
    for i in range(frames):
        t = i / (frames - 1)
        yaw = 0.0
        if direction:
            peak = DIRECTION_SIGN[direction] * 0.45
            yaw = peak * min(1.0, max(0.0, (t - 0.25) / 0.4))
        yaw += jitter * ((i * 7919) % 5 - 2) / 2 if direction else 0.0
        out.append({"faces": faces, "yaw": yaw if faces == 1 else None})
    return out
