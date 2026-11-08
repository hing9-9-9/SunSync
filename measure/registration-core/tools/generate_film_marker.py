"""Generate the standard 1m film reference image and physical specification."""

import argparse
import json
from pathlib import Path
import cv2
import numpy as np
from sunsync_registration.models import Board


def generate(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    board = Board(board_id="window_board")
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    image = np.full((1000, 1000), 255, np.uint8)
    image[200:800, 200:800] = cv2.aruco.generateImageMarker(dictionary, 0, 600)
    _, ids, _ = cv2.aruco.ArucoDetector(dictionary).detectMarkers(image)
    if ids is None or ids.ravel().tolist() != [0]:
        raise RuntimeError("Generated standard marker failed detection")
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG encoding failed")
    (destination / "film-marker-id0.png").write_bytes(encoded.tobytes())
    (destination / "film-marker-id0.json").write_text(
        json.dumps(
            {
                "board": board.model_dump(),
                "film": {
                    "width_m": 1,
                    "height_m": 1,
                    "rows": 10,
                    "cols": 10,
                    "cell_side_m": 0.1,
                    "start_row": 2,
                    "start_col": 2,
                },
                "note": "PNG pixels do not enforce print/display physical size. Verify the black outer side is 0.6m.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    generate(parser.parse_args().output)
