"""Validate an FR06 document or regenerate its structural JSON Schema."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from app.schemas.scene import Scene

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "fr06_scene_schema.json"


def schema_document() -> dict:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **Scene.model_json_schema()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Check structure, geometry and references")
    validate.add_argument("file", type=Path)
    schema = commands.add_parser("schema", help="Write docs/fr06_scene_schema.json")
    schema.add_argument(
        "--check", action="store_true", help="Fail if the checked-in schema differs"
    )
    args = parser.parse_args()
    if args.command == "schema":
        rendered = json.dumps(schema_document(), ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not SCHEMA_PATH.exists() or SCHEMA_PATH.read_text(encoding="utf-8") != rendered:
                parser.exit(1, "Schema is stale; run python -m app.scene_cli schema\n")
            print("Schema matches the Pydantic contract.")
        else:
            SCHEMA_PATH.write_text(rendered, encoding="utf-8")
            print(SCHEMA_PATH)
        return 0
    try:
        scene = Scene.model_validate_json(args.file.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        parser.exit(1, f"Invalid scene: {exc}\n")
    print(f"Valid FR06 {scene.schema_version}: {scene.scene_id}, revision {scene.revision}")
    print("Storage validation only; measured accuracy and calculation readiness are not certified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
