"""Bounded, robust point fusion and raw-pixel/shape joint refinement."""

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

from .geometry import fit_surface, validate_polygon


def fuse(samples, required, spread_limit, limit, placement_sigma):
    """Majority-consensus rejection; correlated views cannot manufacture confidence."""
    p = np.array([s["point"] for s in samples])
    if not len(p):
        return None, [], {"reason": "insufficient_samples"}
    centre = np.median(p, axis=0)
    distances = np.linalg.norm(p - centre, axis=1)
    median = np.median(distances)
    threshold = min(
        spread_limit, max(0.005, median + 3 * 1.4826 * np.median(abs(distances - median)))
    )
    inliers = np.flatnonzero(distances <= threshold)
    if len(inliers) < required or len(inliers) <= len(samples) / 2:
        return None, [], {"reason": "inconsistent_samples"}
    # Prefer low covariance; retain differing views before repeated pixel configurations.
    ranked = sorted(inliers, key=lambda i: float(np.trace(samples[i]["covariance"])))
    groups = []
    for i in ranked:
        match = next(
            (
                g
                for g in groups
                if samples[i]["signature"].shape == samples[g[0]]["signature"].shape
                and np.sqrt(np.mean((samples[i]["signature"] - samples[g[0]]["signature"]) ** 2))
                < 1
            ),
            None,
        )
        if match is None:
            groups.append([i])
        else:
            match.append(i)
    ordered = [
        i for layer in range(max(map(len, groups))) for g in groups for i in g[layer : layer + 1]
    ][:limit]
    if len(ordered) < required:
        return None, [], {"reason": "insufficient_samples"}
    selected = [samples[i] for i in ordered]
    weights = []
    for i, sample in zip(ordered, selected):
        group = next(g for g in groups if i in g)
        count = sum(j in ordered for j in group)
        sample["correlation_weight"] = 1 / count
        weights.append(1 / (count * max(np.trace(sample["covariance"]), 3 * placement_sigma**2)))
    weights = np.array(weights)
    weights /= weights.sum()
    centre = sum(w * s["point"] for w, s in zip(weights, selected))
    spread = float(max(np.linalg.norm(s["point"] - centre) for s in selected))
    if spread > spread_limit:
        return None, [], {"reason": "inconsistent_samples"}
    # Keep the full per-view covariance floor rather than divide by burst length.
    covariance = (
        sum(
            w * (s["covariance"] + np.outer(s["point"] - centre, s["point"] - centre))
            for w, s in zip(weights, selected)
        )
        + np.eye(3) * placement_sigma**2
    )
    return (
        centre,
        selected,
        {
            "covariance_board_m2": covariance.tolist(),
            "uncertainty_m": float(np.sqrt(np.linalg.eigvalsh(covariance)[-1])),
            "repeat_spread_m": spread,
            "independent_view_groups": len(groups),
            "selected_capture_ids": [s["capture"].capture_id for s in selected],
            "weights": weights.tolist(),
            "reason": "ok",
        },
    )


def shape_parameters(kind, points, up):
    """Use topology only; never use sketch dimensions as measured constraints."""
    n = len(points)
    x = points[1] - points[0]
    x -= up * np.dot(x, up)
    x /= np.linalg.norm(x)
    y = np.cross(up, x)
    basis = np.column_stack([x, y, up])
    q = points @ basis
    centre = q.mean(axis=0)
    if kind == "rectangle":
        fit = fit_surface(kind, points, 1e6)
        winding = 1 if np.dot(points[3] - points[0], y) > 0 else -1
        initial = np.r_[centre, 0.0, np.log([fit["width"], fit["depth"]])]

        def decode(v):
            c, s = np.cos(v[3]), np.sin(v[3])
            w, d = np.exp(v[4:6])
            xy = np.array(
                [
                    [-w / 2, -winding * d / 2],
                    [w / 2, -winding * d / 2],
                    [w / 2, winding * d / 2],
                    [-w / 2, winding * d / 2],
                ]
            )
            xy = xy @ np.array([[c, s], [-s, c]]) + v[:2]
            return np.column_stack([xy, np.full(n, v[2])]) @ basis.T
    elif kind in ("circle", "ellipse"):
        fit = fit_surface(kind, points, 1e6)
        centre = fit["centre"] @ basis
        theta = float(np.arctan2(np.dot(fit["axis"], y), np.dot(fit["axis"], x)))
        rot = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
        local = (q[:, :2] - centre[:2]) @ rot.T
        radii = np.array([fit["width"], fit["depth"]]) / 2
        angles = np.arctan2(local[:, 1] / radii[1], local[:, 0] / radii[0])
        initial = np.r_[centre, theta, np.log(radii), angles]

        def decode(v):
            a, b = np.exp(v[4:6])
            b = a if kind == "circle" else b
            xy = np.column_stack([a * np.cos(v[6:]), b * np.sin(v[6:])])
            c, s = np.cos(v[3]), np.sin(v[3])
            xy = xy @ np.array([[c, s], [-s, c]]) + v[:2]
            return np.column_stack([xy, np.full(n, v[2])]) @ basis.T

        # Circle angle and radius redundancy is removed by freezing unused variables.
        if kind == "circle":
            initial = np.r_[
                centre, np.log(radii[0]), np.arctan2(q[:, 1] - centre[1], q[:, 0] - centre[0])
            ]

            def decode(v):
                radius = np.exp(v[3])
                xy = np.column_stack([radius * np.cos(v[4:]), radius * np.sin(v[4:])]) + v[:2]
                return np.column_stack([xy, np.full(n, v[2])]) @ basis.T
    else:
        validate_polygon(q[:, :2])
        initial = np.r_[centre[2], q[:, :2].ravel()]

        def decode(v):
            return np.column_stack([v[1:].reshape(n, 2), np.full(n, v[0])]) @ basis.T

    return initial, decode


def refine(kind, points, samples, board, up, placement_sigma, tolerance, max_rmse):
    """Jointly fit constrained shape, per-frame pose and lens-to-point offsets.

    Pixel noise is estimated from each original PnP; hand placement is a finite
    nuisance residual, never an assumption that every lens was exactly on a corner.
    """
    initial, decode = shape_parameters(kind, points, up)
    size = len(initial)
    objects = board.objects()
    frames = []
    for point_index, group in enumerate(samples):
        for sample in group:
            capture = sample["capture"]
            transform = sample["transform"]
            rotation = transform[:3, :3].T
            r = cv2.Rodrigues(rotation)[0].ravel()
            t = -rotation @ transform[:3, 3]
            ids = sorted(d.marker_id for d in capture.detections if d.marker_id in objects)
            detections = {d.marker_id: np.array(d.corners_px) for d in capture.detections}
            frames.append(
                (
                    point_index,
                    sample,
                    np.r_[r, t],
                    np.concatenate([objects[i] for i in ids]),
                    np.concatenate([detections[i] for i in ids]),
                    capture.camera.matrices(),
                )
            )
    start = np.r_[initial, *[f[2] for f in frames]]
    lower = np.full(len(start), -np.inf)
    upper = -lower
    # Prevent overflow and physically nonsensical radius/side excursions.
    logs = [4, 5] if kind in ("rectangle", "ellipse") else [3] if kind == "circle" else []
    for i in logs:
        lower[i], upper[i] = np.log(0.001), np.log(50)

    def residual(v):
        shaped = decode(v[:size])
        res = []
        for i, (index, sample, _, obj, pixels, (k, d)) in enumerate(frames):
            pose = v[size + 6 * i : size + 6 * i + 6]
            r, t = pose[:3], pose[3:]
            projection = cv2.projectPoints(obj, r, t, k, d)[0].reshape(-1, 2)
            weight = np.sqrt(sample.get("correlation_weight", 1))
            res.extend(((projection - pixels) / sample["pixel_sigma"] * weight).ravel())
            rotation = cv2.Rodrigues(r)[0]
            contact = rotation.T @ (np.asarray(sample["capture"].camera_to_point_m) - t)
            res.extend((contact - shaped[index]) / placement_sigma * weight)
        return np.asarray(res)

    sparsity = lil_matrix((sum(2 * len(f[3]) + 3 for f in frames), len(start)), dtype=int)
    row = 0
    for i, frame in enumerate(frames):
        pixel_count = 2 * len(frame[3])
        sparsity[row : row + pixel_count + 3, size + i * 6 : size + (i + 1) * 6] = 1
        sparsity[row + pixel_count : row + pixel_count + 3, :size] = 1
        row += pixel_count + 3
    solution = least_squares(
        residual,
        start,
        bounds=(lower, upper),
        loss="soft_l1",
        f_scale=1,
        x_scale="jac",
        jac_sparsity=sparsity.tocsr(),
        max_nfev=120,
        ftol=1e-7,
        xtol=1e-7,
        gtol=1e-7,
    )
    shaped = decode(solution.x[:size])
    movement = np.linalg.norm(shaped - points, axis=1)
    if not solution.success or not np.isfinite(shaped).all():
        raise ValueError("joint_optimizer_did_not_converge")
    if movement.max() > tolerance:
        raise ValueError("joint_shape_correction_exceeds_tolerance")
    rmses = []
    for i, (_, sample, _, obj, pixels, (k, d)) in enumerate(frames):
        pose = solution.x[size + 6 * i : size + 6 * i + 6]
        rot = cv2.Rodrigues(pose[:3])[0]
        projected = cv2.projectPoints(obj, pose[:3], pose[3:], k, d)[0].reshape(-1, 2)
        rmse = float(np.sqrt(np.mean(np.sum((projected - pixels) ** 2, axis=1))))
        if (obj @ rot.T + pose[3:])[:, 2].min() <= 0 or rmse > max_rmse:
            raise ValueError("joint_reprojection_error")
        rmses.append(rmse)
    fit_surface(kind, shaped, tolerance)
    return shaped, {
        "status": "converged",
        "iterations": solution.nfev,
        "initial_cost": float(np.sum(2 * (np.sqrt(1 + residual(start) ** 2) - 1)) / 2),
        "final_cost": float(solution.cost),
        "max_point_correction_m": float(movement.max()),
        "max_reprojection_rmse_px": max(rmses),
        "selected_frames": len(frames),
        "uncertainty_scope": "local_pixel_noise_and_repeat_spread; excludes calibration/scale/placement bias",
    }
