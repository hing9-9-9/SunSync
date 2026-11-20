"""Independent receiver-side surface fitting; parity checked against measurement exports."""

import cv2
import numpy as np


def validate_polygon(vertices):
    p = np.asarray(vertices, float)
    if p.ndim != 2 or p.shape[1] != 2 or len(p) < 3 or not np.isfinite(p).all():
        raise ValueError("Polygon needs 3..16 finite, ordered vertices; do not repeat first vertex")
    if len(np.unique(p, axis=0)) != len(p):
        raise ValueError("Duplicate polygon vertices")

    def cross(a, b, c):
        x, y = b - a, c - a
        return x[0] * y[1] - x[1] * y[0]

    def on(a, b, c):
        return (
            abs(cross(a, b, c)) < 1e-10
            and np.all(c >= np.minimum(a, b) - 1e-10)
            and np.all(c <= np.maximum(a, b) + 1e-10)
        )

    n = len(p)
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        for j in range(i + 1, n):
            if j == (i + 1) % n or (j + 1) % n == i:
                continue
            c, d = p[j], p[(j + 1) % n]
            if (cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0) or any(
                (on(a, b, c), on(a, b, d), on(c, d, a), on(c, d, b))
            ):
                raise ValueError("Self-intersecting polygon")
    area = 0.5 * abs(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - p[:, 1] * np.roll(p[:, 0], -1)))
    if area < 1e-10:
        raise ValueError("Degenerate polygon")


def fit_surface(kind, points, tolerance):
    p = np.asarray(points, float)
    centre = p.mean(axis=0)
    _, s, vt = np.linalg.svd(p - centre, full_matrices=False)
    if len(s) < 2 or s[1] < 1e-8:
        raise ValueError("Collinear surface points")
    basis = vt[:2].T
    q = (p - centre) @ basis
    plane_error = float(np.max(abs((p - centre) @ vt[-1])))
    residual = plane_error
    if kind == "rectangle":
        x = (p[1] - p[0] + p[2] - p[3]) / 2
        y = (p[3] - p[0] + p[2] - p[1]) / 2
        width = float(np.linalg.norm(x))
        if width < 1e-8:
            raise ValueError("Zero width")
        axis = x / width
        yy = y - axis * np.dot(y, axis)
        depth = float(np.linalg.norm(yy))
        if depth < 1e-8:
            raise ValueError("Zero depth")
        yy /= depth
        predicted = np.array(
            [
                centre + a * x / 2 + b * yy * depth / 2
                for a, b in [(-1, -1), (1, -1), (1, 1), (-1, 1)]
            ]
        )
        residual = max(residual, float(np.max(np.linalg.norm(p - predicted, axis=1))))
    elif kind == "circle":
        design = np.column_stack([2 * q, np.ones(len(q))])
        sol, _, rank, _ = np.linalg.lstsq(design, np.sum(q * q, axis=1), rcond=None)
        if rank < 3:
            raise ValueError("Circle fit is degenerate")
        radius2 = sol[2] + np.sum(sol[:2] ** 2)
        if radius2 <= 0:
            raise ValueError("Invalid circle radius")
        radius = np.sqrt(radius2)
        centre = centre + basis @ sol[:2]
        residual = max(residual, float(np.max(abs(np.linalg.norm(q - sol[:2], axis=1) - radius))))
        width = depth = float(2 * radius)
        axis = p[1] - centre
        axis /= np.linalg.norm(axis)
    elif kind == "ellipse":
        (cx, cy), (da, db), angle = cv2.fitEllipse(q.astype(np.float32))
        theta = np.deg2rad(angle)
        directions = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
        local = (q - [cx, cy]) @ directions
        radii = np.array([da, db]) / 2
        if np.min(radii) < 1e-8:
            raise ValueError("Degenerate ellipse")
        norm = np.sqrt(np.sum((local / radii) ** 2, axis=1))
        # Conservative algebraic radial residual, not a confidence interval.
        residual = max(residual, float(np.max(abs(norm - 1)) * np.max(radii)))
        major = int(np.argmax(radii))
        width, depth = float(np.max(radii) * 2), float(np.min(radii) * 2)
        axis = basis @ directions[:, major]
        centre = centre + basis @ np.array([cx, cy])
    else:
        validate_polygon(q)
        axis = p[1] - p[0]
        axis /= np.linalg.norm(axis)
        yy = np.cross(vt[-1], axis)
        yy /= np.linalg.norm(yy)
        local = (p - centre) @ np.column_stack([axis, yy])
        lo, hi = local.min(axis=0), local.max(axis=0)
        width, depth = map(float, hi - lo)
        centre = centre + np.column_stack([axis, yy]) @ ((lo + hi) / 2)
    if residual > tolerance:
        raise ValueError(f"Surface residual {residual:.4f} m exceeds tolerance")
    return {
        "centre": centre,
        "width": float(width),
        "depth": float(depth),
        "axis": axis,
        "normal": vt[-1],
        "max_residual_m": residual,
    }
