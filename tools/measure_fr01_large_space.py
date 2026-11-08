"""Measure FR-01 response size and runtime for the S6 large-space scenarios."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.schemas.sunpath import SunpathPredictionRequest
from app.services.sunpath import SunpathValidationError, predict_scene
from app.ui_scene import UISpace, to_scene

SCENARIOS = ROOT / "tools" / "dummy_data" / "scenarios.json"


def _scenario(key: str) -> dict[str, object]:
    payload = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    return next(item for item in payload["scenarios"] if item["key"] == key)


def _request(item: dict[str, object], detail_level: str) -> SunpathPredictionRequest:
    space = item["space"]
    assert isinstance(space, dict)
    checks = item["checks"]
    assert isinstance(checks, list) and checks and isinstance(checks[0], dict)
    date = checks[0]["date"]
    assert isinstance(date, str)
    site = space["site"]
    assert isinstance(site, dict) and isinstance(site["timezone"], str)
    local_noon = datetime.fromisoformat(f"{date}T12:00:00").replace(
        tzinfo=ZoneInfo(site["timezone"])
    )
    offset = local_noon.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}"
    return SunpathPredictionRequest(
        start_time=f"{date}T00:00:00{offset}",
        duration_minutes=1440,
        interval_seconds=1800,
        detail_level=detail_level,
    )


def measure(key: str, detail_level: str, repeat: int, warmup: int) -> dict[str, object]:
    item = _scenario(key)
    space_payload = item["space"]
    assert isinstance(space_payload, dict)
    scene = to_scene(UISpace.model_validate(space_payload), revision=1)
    request = _request(item, detail_level)
    elapsed: list[float] = []
    response_bytes: int | None = None
    try:
        for index in range(warmup + repeat):
            started = time.perf_counter()
            result = predict_scene(scene, request)
            duration = time.perf_counter() - started
            if index >= warmup:
                elapsed.append(duration)
            response_bytes = len(result.model_dump_json().encode("utf-8"))
    except SunpathValidationError as error:
        return {
            "scenario": key,
            "detail_level": detail_level,
            "status": 422,
            "error": error.detail.model_dump(mode="json"),
        }
    return {
        "scenario": key,
        "detail_level": detail_level,
        "status": 200,
        "response_bytes": response_bytes,
        "elapsed_seconds": [round(value, 3) for value in elapsed],
        "median_elapsed_seconds": round(statistics.median(elapsed), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("S6a", "S6b"), default="S6a")
    parser.add_argument("--detail-level", choices=("summary", "diagnostic"), default="summary")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0:
        parser.error("repeat must be positive and warmup must be nonnegative")
    print(
        json.dumps(
            measure(args.scenario, args.detail_level, args.repeat, args.warmup),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
