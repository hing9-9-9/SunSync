"""Compare all input cases without choosing the best matching frames or hiding failures."""

import argparse
import json
from pathlib import Path

import numpy as np
from sunsync_registration.core import measure
from sunsync_registration.models import MeasureRequest


def compare(cases, truth=None):
    rows = []
    for name, value in cases:
        request = MeasureRequest.model_validate(value)
        for mode in ("legacy", "weighted", "robust_joint"):
            row = {"case": name, "mode": mode}
            try:
                result = measure(request.model_copy(update={"estimation_mode": mode}))
                row.update(
                    status=result["status"],
                    required_actions=result["required_actions"],
                    optimization=result.get("optimization"),
                    raw_count=len(request.captures),
                    selected_count=sum(
                        d.get("selected", False) for d in result["capture_diagnostics"]
                    ),
                )
                if result["model"]:
                    dimensions = result["model"]["dimensions_m"]
                    row["dimensions_m"] = dimensions
                    # Ground truth is only used here, after the solver has returned.
                    if truth and name in truth:
                        expected = np.asarray(truth[name], float)
                        if (
                            expected.shape != (3,)
                            or not np.isfinite(expected).all()
                            or np.any(expected <= 0)
                        ):
                            raise ValueError("Truth requires three finite positive dimensions")
                        row["absolute_error_m"] = np.abs(np.asarray(dimensions) - expected).tolist()
                        row["relative_error_percent"] = (
                            np.abs(np.asarray(dimensions) - expected) / expected * 100
                        ).tolist()
            except ValueError as error:
                row.update(status="rejected", reason=str(error))
            rows.append(row)
    summary = {}
    for mode in ("legacy", "weighted", "robust_joint"):
        mode_rows = [r for r in rows if r["mode"] == mode]
        errors = [r["absolute_error_m"] for r in mode_rows if "absolute_error_m" in r]
        summary[mode] = {
            "total": len(mode_rows),
            "accepted": sum("dimensions_m" in r for r in mode_rows),
            "rejected": sum("dimensions_m" not in r for r in mode_rows),
            "mean_absolute_error_m": np.mean(errors, axis=0).tolist() if errors else None,
        }
    return {
        "scope": "Dataset comparison; not a guarantee of real-device accuracy",
        "summary": summary,
        "cases": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", type=Path, help="Directory of MeasureRequest JSON files")
    parser.add_argument(
        "--truth",
        type=Path,
        help="Optional JSON mapping filename to [width, depth, height] in metres",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [
        (p.name, json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(args.inputs.glob("*.json"))
    ]
    if not cases:
        parser.error("No input JSON files")
    truth = json.loads(args.truth.read_text(encoding="utf-8")) if args.truth else None
    args.output.write_text(
        json.dumps(compare(cases, truth), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
