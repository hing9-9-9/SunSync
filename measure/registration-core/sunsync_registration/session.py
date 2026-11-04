"""Reference state machine to port to any mobile client; no global server state."""

from .models import PlanRequest, Board, Camera, Capture, MeasureRequest
from .core import plan


class CaptureSession:
    def __init__(self, request: PlanRequest, board: Board):
        self.request = request
        self.board = board
        self.guide = plan(request)
        self.captures = []

    @property
    def current_step(self):
        for step in self.guide["steps"]:
            count = sum(c.point_id == step["point_id"] for c in self.captures)
            if count < step["required_samples"]:
                return {**step, "accepted_samples": count}
        return None

    def accept(
        self,
        capture_id: str,
        camera: Camera,
        detection_response: dict,
        camera_to_point_m=(0, 0, 0),
    ):
        step = self.current_step
        if step is None:
            raise ValueError("All steps completed")
        if any(c.capture_id == capture_id for c in self.captures):
            raise ValueError("Duplicate capture_id")
        if detection_response["pose"]["status"] != "ok":
            return False
        capture = Capture(
            capture_id=capture_id,
            point_id=step["point_id"],
            camera=camera,
            detections=detection_response["detections"],
            camera_to_point_m=camera_to_point_m,
        )
        self.captures.append(capture)
        return True

    def retake(self, point_id):
        if point_id not in {s["point_id"] for s in self.guide["steps"]}:
            raise ValueError("Unknown point")
        self.captures = [c for c in self.captures if c.point_id != point_id]

    def measurement_request(self, **reference):
        if self.current_step is not None:
            raise ValueError("Capture all steps first")
        return MeasureRequest(
            plan=self.request, board=self.board, captures=self.captures, **reference
        )
