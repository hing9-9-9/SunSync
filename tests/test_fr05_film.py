"""Unit tests for VirtualFilmDevice, FilmDeviceProtocol, and ArUco marker display."""

import pytest
from app.schemas.film import CellCommand
from app.services.film import FilmDeviceProtocol, VirtualFilmDevice, generate_aruco_4x4_pattern


def test_virtual_film_device_defaults() -> None:
    device = VirtualFilmDevice()
    assert isinstance(device, FilmDeviceProtocol)
    assert device.rows == 10
    assert device.cols == 10
    assert device.film_type == "PDLC"
    assert not device.marker_mode_active

    grid = device.get_grid()
    assert len(grid) == 10
    assert all(len(row) == 10 for row in grid)
    assert all(grid[r][c] == 0.0 for r in range(10) for c in range(10))


def test_virtual_film_device_spd_custom() -> None:
    custom_cells = [[0.2 * r for _ in range(4)] for r in range(3)]
    spd_device = VirtualFilmDevice(rows=3, cols=4, film_type="SPD", initial_cells=custom_cells)
    assert isinstance(spd_device, FilmDeviceProtocol)
    assert spd_device.rows == 3
    assert spd_device.cols == 4
    assert spd_device.film_type == "SPD"
    assert spd_device.get_opacity(0, 0) == 0.0
    assert spd_device.get_opacity(1, 2) == 0.2
    assert spd_device.get_opacity(2, 3) == 0.4


def test_virtual_film_device_bounds_and_validations() -> None:
    with pytest.raises(ValueError, match="positive integers"):
        VirtualFilmDevice(rows=0, cols=10)

    with pytest.raises(ValueError, match="positive integers"):
        VirtualFilmDevice(rows=10, cols=-1)

    with pytest.raises(ValueError, match="non-empty string"):
        VirtualFilmDevice(film_type="")

    device = VirtualFilmDevice(rows=5, cols=5)
    with pytest.raises(ValueError, match="out of bounds"):
        device.get_opacity(5, 0)

    with pytest.raises(ValueError, match="out of bounds"):
        device.get_opacity(-1, 2)


def test_apply_commands_single_and_batch() -> None:
    device = VirtualFilmDevice(rows=4, cols=4)

    # 1. Tuple commands
    transitions = device.apply_commands([(0, 0, 1.0), (1, 1, 0.4)])
    assert transitions == [(0, 0, 0.0, 1.0), (1, 1, 0.0, 0.4)]
    assert device.get_opacity(0, 0) == 1.0
    assert device.get_opacity(1, 1) == 0.4

    # 2. CellCommand schemas
    cmd1 = CellCommand(row=2, col=2, state="opaque")
    cmd2 = CellCommand(row=3, col=3, state="transparent")
    cmd3 = CellCommand(row=0, col=0, opacity=0.0)
    transitions2 = device.apply_commands([cmd1, cmd2, cmd3])
    assert transitions2 == [
        (2, 2, 0.0, 1.0),
        (3, 3, 0.0, 0.0),
        (0, 0, 1.0, 0.0),
    ]
    assert device.get_opacity(2, 2) == 1.0
    assert device.get_opacity(0, 0) == 0.0


def test_apply_commands_rejections_and_atomicity() -> None:
    device = VirtualFilmDevice(rows=4, cols=4)
    device.apply_commands([(0, 0, 0.5)])

    # Out of bounds coordinate
    with pytest.raises(ValueError, match="out of bounds"):
        device.apply_commands([(0, 0, 0.0), (4, 4, 1.0)])
    # Atomic: (0, 0) should remain 0.5
    assert device.get_opacity(0, 0) == 0.5

    # Out of bounds opacity
    with pytest.raises(ValueError, match="Opacity must be in range"):
        device.apply_commands([(0, 0, 1.5)])
    assert device.get_opacity(0, 0) == 0.5

    with pytest.raises(ValueError, match="Opacity must be in range"):
        device.apply_commands([(0, 0, -0.1)])
    assert device.get_opacity(0, 0) == 0.5

    # NaN / Inf
    with pytest.raises(ValueError, match="Opacity must be in range"):
        device.apply_commands([(0, 0, float("nan"))])
    with pytest.raises(ValueError, match="Opacity must be in range"):
        device.apply_commands([(0, 0, float("inf"))])

    # Boolean
    with pytest.raises(TypeError, match="Invalid opacity value"):
        device.apply_commands([(0, 0, True)])  # type: ignore[arg-type]


def test_aruco_pattern_generation() -> None:
    pattern = generate_aruco_4x4_pattern(0)
    assert len(pattern) == 6
    assert all(len(row) == 6 for row in pattern)

    # Outer 1-cell border must be 1.0
    for r in range(6):
        assert pattern[r][0] == 1.0
        assert pattern[r][5] == 1.0
    for c in range(6):
        assert pattern[0][c] == 1.0
        assert pattern[5][c] == 1.0

    # Inner 4x4 values must be 0.0 or 1.0
    for r in range(1, 5):
        for c in range(1, 5):
            assert pattern[r][c] in (0.0, 1.0)


def test_marker_mode_display_and_restore() -> None:
    device = VirtualFilmDevice(rows=10, cols=10)
    # Set pre-marker cells
    device.apply_commands([(0, 0, 0.7), (2, 2, 0.3), (3, 4, 0.9)])
    pre_grid = device.get_grid()

    # Enable marker mode
    device.enable_marker_mode(code_id=5, start_row=2, start_col=2)
    assert device.marker_mode_active
    assert device.marker_code_id == 5
    assert device.marker_start_row == 2
    assert device.marker_start_col == 2

    # Verify border of marker
    for r in range(6):
        assert device.get_opacity(2 + r, 2) == 1.0
        assert device.get_opacity(2 + r, 7) == 1.0
    for c in range(6):
        assert device.get_opacity(2, 2 + c) == 1.0
        assert device.get_opacity(7, 2 + c) == 1.0

    # Unmodified region outside marker
    assert device.get_opacity(0, 0) == 0.7

    # Disable marker mode restores exact pre-marker grid
    device.disable_marker_mode()
    assert not device.marker_mode_active
    assert device.marker_code_id is None
    assert device.get_grid() == pre_grid


def test_marker_mode_rejections() -> None:
    device = VirtualFilmDevice(rows=8, cols=8)
    with pytest.raises(ValueError, match="code_id must be in 0..49"):
        device.enable_marker_mode(code_id=50)

    with pytest.raises(ValueError, match="start_row=3 exceeds film rows 8"):
        device.enable_marker_mode(code_id=0, start_row=3, start_col=2)

    with pytest.raises(ValueError, match="start_col=3 exceeds film cols 8"):
        device.enable_marker_mode(code_id=0, start_row=2, start_col=3)


def test_aruco_pattern_detected_by_opencv() -> None:
    """Verify that OpenCV's real DICT_4X4_50 detector recognizes generated 6x6 film markers."""
    import cv2
    import numpy as np

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    detector = cv2.aruco.ArucoDetector(dictionary)

    for test_code in (0, 15, 42, 49):
        pattern = generate_aruco_4x4_pattern(test_code)
        # Convert 6x6 opacities to grayscale image: 1.0 (black, 0), 0.0 (white, 255)
        grid = np.array(pattern, dtype=np.float32)
        img_small = np.where(grid > 0.5, 0, 255).astype(np.uint8)
        img_bordered = cv2.copyMakeBorder(img_small, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=255)
        img_large = cv2.resize(img_bordered, (200, 200), interpolation=cv2.INTER_NEAREST)

        _corners, ids, _ = detector.detectMarkers(img_large)
        assert ids is not None, f"Failed to detect ArUco marker for code_id {test_code}"
        assert ids.flatten()[0] == test_code


def test_strict_type_validations() -> None:
    # Boolean cannot be used as grid dimensions
    with pytest.raises(TypeError, match="must be integers"):
        VirtualFilmDevice(rows=True, cols=10)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be integers"):
        VirtualFilmDevice(rows=10, cols=False)  # type: ignore[arg-type]

    device = VirtualFilmDevice(rows=5, cols=5)
    # Boolean cannot be used as row or col in get_opacity
    with pytest.raises(TypeError, match="must be integers"):
        device.get_opacity(True, 0)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be integers"):
        device.get_opacity(0, False)  # type: ignore[arg-type]

    # Boolean or wrong length in apply_commands
    with pytest.raises(TypeError, match="must be integers"):
        device.apply_commands([(True, 0, 0.5)])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Command must be CellCommand or 3-tuple"):
        device.apply_commands([(0, 0)])  # type: ignore[arg-type]

    # CellCommand dimmed state validation
    valid_dimmed = CellCommand(row=1, col=1, state="dimmed", opacity=0.35)
    assert valid_dimmed.opacity == 0.35
    with pytest.raises(ValueError, match="state 'dimmed' requires explicit opacity"):
        CellCommand(row=1, col=1, state="dimmed")


def test_marker_mode_reposition_and_manual_command_during_marker() -> None:
    device = VirtualFilmDevice(rows=10, cols=10)
    # 1. Enable marker at (2, 2)
    device.enable_marker_mode(code_id=0, start_row=2, start_col=2)
    assert device.get_opacity(2, 2) == 1.0

    # 2. Re-enable/reposition marker at (4, 4)
    device.enable_marker_mode(code_id=1, start_row=4, start_col=4)
    # Pre-marker cells at (2, 2) restored to 0.0, new marker at (4, 4) active
    assert device.get_opacity(2, 2) == 0.0
    assert device.get_opacity(4, 4) == 1.0

    # 3. Apply manual command outside marker while marker is active
    device.apply_commands([(0, 0, 0.88)])
    assert device.get_opacity(0, 0) == 0.88

    # 4. Disable marker mode: manual command at (0, 0) is preserved!
    device.disable_marker_mode()
    assert not device.marker_mode_active
    assert device.get_opacity(0, 0) == 0.88
    assert device.get_opacity(4, 4) == 0.0
