"""Scene cuts (PySceneDetect, BSD-3) and an optional face centre (MediaPipe, Apache-2.0).

Both are optional: without them clips are still cut, just snapped to silences only and
cropped from the centre. Neither needs a model download at run time (the MediaPipe face
detector used here ships inside the wheel; newer MediaPipe builds without it are skipped).
"""
import logging
import statistics
from pathlib import Path

log = logging.getLogger("clip-finder")


def scenes_available() -> bool:
    try:
        import scenedetect  # noqa: F401
        return True
    except Exception:
        return False


def detect_cuts(path: Path, threshold: float = 27.0) -> list[float]:
    """Cut times in seconds (the start of every scene after the first)."""
    from scenedetect import ContentDetector, detect
    scene_list = detect(str(path), ContentDetector(threshold=threshold), show_progress=False)

    def seconds(tc) -> float:
        value = getattr(tc, "seconds", None)
        return float(value) if isinstance(value, (int, float)) else tc.get_seconds()

    return [round(seconds(start), 3) for start, _end in scene_list[1:]]


def _face_detector():
    """A MediaPipe face detector whose model ships in the wheel, or None."""
    try:
        import mediapipe as mp
        solutions = getattr(mp, "solutions", None)
        if solutions is None or not hasattr(solutions, "face_detection"):
            return None
        return solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
    except Exception as e:  # not installed, or no bundled model
        log.info("face detection unavailable: %s", type(e).__name__)
        return None


def faces_available() -> bool:
    det = _face_detector()
    if det is None:
        return False
    det.close()
    return True


def face_center_x(images: list[Path]) -> float | None:
    """Median horizontal centre (0..1) of the largest face over the given stills, or None when
    no detector is available or fewer than a third of the stills show a face."""
    det = _face_detector()
    if det is None or not images:
        return None
    try:
        import numpy as np
        from PIL import Image
        centres = []
        for p in images:
            rgb = np.asarray(Image.open(p).convert("RGB"))
            res = det.process(rgb)
            if not res.detections:
                continue
            best = max(res.detections, key=lambda d: d.location_data.relative_bounding_box.width)
            box = best.location_data.relative_bounding_box
            centres.append(min(1.0, max(0.0, box.xmin + box.width / 2)))
        if len(centres) * 3 < len(images):
            return None
        return round(statistics.median(centres), 3)
    finally:
        det.close()
