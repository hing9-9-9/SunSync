"""Regenerate published schemas after changing API or fragment models."""

import json
from pathlib import Path
from sunsync_registration.api import app
from sunsync_registration.fragment_schema import BoardCameraFragmentV1

root = Path(__file__).resolve().parents[1]
for name, value in [
    ("openapi.json", app.openapi()),
    ("fr06-fragment.schema.json", BoardCameraFragmentV1.model_json_schema()),
]:
    (root / name).write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
