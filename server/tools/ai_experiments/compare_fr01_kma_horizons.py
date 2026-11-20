"""Compare FR-01 10/30/60-minute quantile models on native and common validation rows."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

HORIZONS = (10, 30, 60)
QUANTILES = ("p10", "p50", "p90")


def _load_predictions(path: Path, expected_horizon: int) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        for line_number, row in enumerate(reader, start=2):
            if int(row["horizon_minutes"]) != expected_horizon:
                raise ValueError(f"unexpected horizon in {path}:{line_number}")
            origin = row["prediction_origin_utc"]
            if origin in rows:
                raise ValueError(f"duplicate prediction origin in {path}:{line_number}")
            rows[origin] = row
    if not rows:
        raise ValueError(f"no prediction rows: {path}")
    return rows


def _mean(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot average empty values")
    return sum(values) / len(values)


def _metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    references = [float(row["reference_dni_W_m2"]) for row in rows]
    p50 = [float(row["p50_dni_W_m2"]) for row in rows]
    open_meteo = [float(row["open_meteo_dni_W_m2"]) for row in rows]
    model_errors = [prediction - reference for prediction, reference in zip(p50, references)]
    baseline_errors = [prediction - reference for prediction, reference in zip(open_meteo, references)]

    pinball: dict[str, float] = {}
    cdf: dict[str, float] = {}
    for label, quantile in zip(QUANTILES, (0.10, 0.50, 0.90), strict=True):
        predictions = [float(row[f"{label}_dni_W_m2"]) for row in rows]
        losses = []
        below = 0
        for reference, prediction in zip(references, predictions, strict=True):
            error = reference - prediction
            losses.append(max(quantile * error, (quantile - 1.0) * error))
            below += int(reference <= prediction)
        pinball[label] = _mean(losses)
        cdf[label] = below / len(rows)

    covered = [
        float(row["p10_dni_W_m2"])
        <= reference
        <= float(row["p90_dni_W_m2"])
        for row, reference in zip(rows, references, strict=True)
    ]
    widths = [float(row["interval_width_W_m2"]) for row in rows]
    return {
        "row_count": len(rows),
        "p50": {
            "mae_W_m2": _mean([abs(error) for error in model_errors]),
            "rmse_W_m2": math.sqrt(_mean([error * error for error in model_errors])),
            "bias_W_m2": _mean(model_errors),
        },
        "open_meteo": {
            "mae_W_m2": _mean([abs(error) for error in baseline_errors]),
            "rmse_W_m2": math.sqrt(_mean([error * error for error in baseline_errors])),
            "bias_W_m2": _mean(baseline_errors),
        },
        "pinball_loss_W_m2": pinball,
        "empirical_cdf_at_quantile": cdf,
        "picp_p10_p90": _mean([float(value) for value in covered]),
        "mean_interval_width_W_m2": _mean(widths),
        "p10_zero_rate": _mean(
            [float(float(row["p10_factor"]) == 0.0) for row in rows]
        ),
        "p90_cap_rate": _mean(
            [float(float(row["p90_factor"]) >= 1.05) for row in rows]
        ),
        "raw_crossing_count": sum(row["raw_quantile_crossing"] == "True" for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for horizon in HORIZONS:
        parser.add_argument(f"--horizon-{horizon}-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    directories = {
        horizon: getattr(args, f"horizon_{horizon}_dir") for horizon in HORIZONS
    }
    predictions = {
        horizon: _load_predictions(
            directory / "validation_quantile_predictions.csv", horizon
        )
        for horizon, directory in directories.items()
    }
    common_origins = set.intersection(*(set(rows) for rows in predictions.values()))
    if not common_origins:
        raise ValueError("no common validation prediction origins")

    document = {
        "horizons_minutes": list(HORIZONS),
        "selection_policy": "validation only; diagnostic report-only; final test unused",
        "native_validation": {
            str(horizon): _metrics(list(rows.values()))
            for horizon, rows in predictions.items()
        },
        "common_validation": {
            "common_origin_count": len(common_origins),
            "first_origin_utc": min(common_origins),
            "last_origin_utc": max(common_origins),
            "metrics": {
                str(horizon): _metrics(
                    [predictions[horizon][origin] for origin in sorted(common_origins)]
                )
                for horizon in HORIZONS
            },
        },
        "final_test_used": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"common_validation_origins={len(common_origins)}")
    for horizon in HORIZONS:
        native = document["native_validation"][str(horizon)]
        common = document["common_validation"]["metrics"][str(horizon)]
        print(
            f"horizon={horizon} native_rows={native['row_count']} "
            f"native_p50_mae={native['p50']['mae_W_m2']:.2f} "
            f"common_p50_mae={common['p50']['mae_W_m2']:.2f}"
        )
    print(f"output={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
