"""Stateless integration API. Deploy behind the team's authenticated gateway."""

import cv2
from fastapi import FastAPI, HTTPException
from starlette.responses import JSONResponse
from pydantic import Field
from .models import PlanRequest, MeasureRequest, DetectRequest, Strict
from .core import plan, measure, compose_scene
from .pose import detect
from .fr06 import export_fragment


class SceneRequest(Strict):
    measurements: list[dict] = Field(min_length=1, max_length=64)


class FragmentRequest(Strict):
    measurement: dict


class BodyLimit:
    def __init__(self, app, maximum=16_000_000):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        messages = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > self.maximum:
                return await JSONResponse(
                    {"detail": "Request body exceeds 16 MB"}, status_code=413
                )(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        return await self.app(scope, replay, send)


def create_app():
    app = FastAPI(
        title="Sunsync furniture registration",
        version="0.1.0",
        description="Integration release; metric accuracy and mobile adapters require validation.",
    )
    app.add_middleware(BodyLimit)

    def run(operation, value):
        try:
            return operation(value)
        except (ValueError, KeyError, TypeError, cv2.error) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/v1/plans")
    def make_plan(request: PlanRequest):
        return run(plan, request)

    @app.post("/v1/detect")
    def detect_image(request: DetectRequest):
        return run(detect, request)

    @app.post("/v1/measurements")
    def make_measurement(request: MeasureRequest):
        return run(measure, request)

    @app.post("/v1/scenes")
    def make_scene(request: SceneRequest):
        return run(compose_scene, request.measurements)

    @app.post("/v1/fr06")
    def make_fragment(request: FragmentRequest):
        return run(export_fragment, request.measurement)

    return app


app = create_app()
