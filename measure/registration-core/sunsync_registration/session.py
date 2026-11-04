"""Capture-time tickets prevent stale asynchronous responses from changing steps."""

from threading import RLock
from .models import PlanRequest, Board, Camera, Capture, MeasureRequest
from .core import plan


class CaptureSession:
    def __init__(self, request: PlanRequest, board: Board):
        self.request = request
        self.board = board
        self.guide = plan(request)
        self.captures = []
        self._pending = {}
        self._used_ids = set()
        self._lock = RLock()

    @property
    def current_step(self):
        with self._lock:
            for step in self.guide["steps"]:
                count = sum(c.point_id == step["point_id"] for c in self.captures)
                if count < step["required_samples"]:
                    return {**step, "accepted_samples": count}
            return None

    def begin_capture(self, capture_id: str):
        """Call at shutter time, before sending the image to the detector."""
        with self._lock:
            step = self.current_step
            if step is None:
                raise ValueError("All steps completed")
            if not capture_id or len(capture_id) > 100:
                raise ValueError("Invalid capture_id")
            if capture_id in self._used_ids:
                raise ValueError("Duplicate capture_id")
            self._used_ids.add(capture_id)
            self._pending[capture_id] = step["point_id"]
            return {"capture_id": capture_id, "point_id": step["point_id"]}

    def accept(
        self,
        capture_id: str,
        point_id: str,
        camera: Camera,
        detection_response: dict,
        camera_to_point_m=(0, 0, 0),
    ):
        with self._lock:
            captured_point = self._pending.get(capture_id)
            if captured_point is None:
                raise ValueError("Unknown, already handled, or invalidated capture")
            if point_id != captured_point:
                raise ValueError("point_id disagrees with capture-time ticket")
            step = self.current_step
            if step is None or step["point_id"] != captured_point:
                del self._pending[capture_id]
                raise ValueError("Stale response: capture point is no longer active")
            if detection_response["pose"]["status"] != "ok":
                del self._pending[capture_id]
                return False
            capture = Capture(
                capture_id=capture_id,
                point_id=captured_point,
                camera=camera,
                detections=detection_response["detections"],
                camera_to_point_m=camera_to_point_m,
            )
            del self._pending[capture_id]
            self.captures.append(capture)
            return True

    def retake(self, point_id):
        with self._lock:
            if point_id not in {s["point_id"] for s in self.guide["steps"]}:
                raise ValueError("Unknown point")
            self.captures = [c for c in self.captures if c.point_id != point_id]
            self._pending.clear()

    def measurement_request(self, **reference):
        with self._lock:
            if self.current_step is not None:
                raise ValueError("Capture all steps first")
            return MeasureRequest(
                plan=self.request,
                board=self.board,
                captures=list(self.captures),
                **reference
            )
