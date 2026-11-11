"""Fixed held-out synthetic suite; keep failure counts beside accepted-case errors."""

import argparse
import importlib.util
import json
from pathlib import Path

from compare_measurement_modes import compare

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "noisy_fixture", root / "examples/build_noisy_fixture.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def run():
    results = []
    for kind in ("rectangle", "circle", "ellipse", "polygon"):
        for outlier in (False, True):
            cases = []
            truth = {}
            for seed in range(800, 820):
                value, _ = module.noisy_fixture(seed=seed, noise=0.3, outlier=outlier, kind=kind)
                name = f"{kind}-{outlier}-{seed}"
                cases.append((name, value))
                truth[name] = [
                    0.6,
                    0.4 if kind in ("ellipse", "polygon") else 0.6 if kind == "circle" else 0.45,
                    0.75,
                ]
            results.append({"shape": kind, "outlier": outlier, **compare(cases, truth)})
    return {
        "scope": "synthetic_only",
        "pixel_noise_sigma_px": 0.3,
        "placement_jitter_sigma_m": 0.002,
        "outlier_translation_m": 0.09,
        "frames_per_point": 8,
        "seeds": [800, 819],
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(), indent=2, allow_nan=False) + "\n", encoding="utf-8")
