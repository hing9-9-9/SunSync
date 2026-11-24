#!/usr/bin/env python3
"""Generate publication-grade vector SVG figures for the SunSync-AI tech blog.

All plotted numbers are read directly from repository experiment artifacts:
- data/fr01/model/final_test_20261102_20261105/final_test_metrics.json
- data/fr01/model/final_test_20261102_20261105/horizon_10_predictions.csv
- data/fr01/model/stage5/horizon_comparison.json
- tools/ai_experiments/run_fr03_vision_3d_experiment.py (0%, 2%, 5% noise)
- docs/reports/fr04_adaptive_control_experiment_results.json
- docs/reports/fr04_minute_replay.json
"""

from __future__ import annotations

import html
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIG_DIR = Path(__file__).resolve().parent / "figures"

# Colorblind-safe, print-ready IEEE palette
NAVY = "#1B365D"
TEAL = "#0F766E"
ORANGE = "#C2520F"
SLATE = "#64748B"
INK = "#111827"
MUTED = "#4B5563"
GRID = "#E5E7EB"
BAND_FILL = "#93C5FD"
BG = "#FFFFFF"
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-label="{esc(title)}">'
        ),
        f"<title>{esc(title)}</title>",
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{BG}" rx="4"/>',
    ]


def build_fig1_fr01_horizon_error() -> str:
    plot_blob = json.loads((FIG_DIR / "fr01_plot_data.json").read_text(encoding="utf-8"))
    final_data = {
        "horizons": {k: {"metrics": v} for k, v in plot_blob["final_test_horizons"].items()}
    }
    val_data = {"native_validation": plot_blob["native_validation"]}

    horizons = ["10", "30", "60"]
    labels = ["10 min", "30 min", "60 min"]

    w, h = 860, 368
    out = svg_header(w, h, "FR-01 Solar Nowcast Frozen Final Test Error and Coverage by Horizon")

    # Main title
    out.append(
        f'<text x="{w / 2}" y="24" text-anchor="middle" font-family="{FONT}" '
        f'font-size="14" font-weight="700" fill="{INK}">'
        "FR-01 Frozen Final Test (Nov 2-5, 2026): Horizon Error vs. Open-Meteo &amp; Interval Coverage"
        "</text>"
    )

    # ---- Left Panel: Final Test MAE & RMSE ----
    lx, ly, lw, lh = 64, 58, 365, 224
    max_y = 260.0
    out.append(
        f'<text x="{lx + lw / 2}" y="46" text-anchor="middle" font-family="{FONT}" '
        f'font-size="11.5" font-weight="600" fill="{INK}">(a) Point Forecast Error (W/m², Lower is Better)</text>'
    )

    for tick in [0, 50, 100, 150, 200, 250]:
        y = ly + lh - (tick / max_y) * lh
        out.append(
            f'<line x1="{lx}" y1="{y:.1f}" x2="{lx + lw}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{lx - 8}" y="{y + 3.5:.1f}" text-anchor="end" font-family="{FONT}" '
            f'font-size="10" fill="{MUTED}">{tick}</text>'
        )

    out.append(
        f'<line x1="{lx}" y1="{ly + lh}" x2="{lx + lw}" y2="{ly + lh}" stroke="{INK}" stroke-width="1.2"/>'
    )
    out.append(
        f'<line x1="{lx}" y1="{ly}" x2="{lx}" y2="{ly + lh}" stroke="{INK}" stroke-width="1.2"/>'
    )
    out.append(
        f'<text x="18" y="{ly + lh / 2}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="10.5" fill="{INK}" transform="rotate(-90 18 {ly + lh / 2})">Error (W/m²)</text>'
    )

    group_w = lw / len(horizons)
    bar_w = 21
    series = [
        ("p50_mae", "KMA V1 P50 MAE", NAVY, "1.0"),
        ("om_mae", "Open-Meteo MAE", ORANGE, "1.0"),
        ("p50_rmse", "KMA V1 P50 RMSE", "#60A5FA", "0.95"),
        ("om_rmse", "Open-Meteo RMSE", "#FDBA74", "0.95"),
    ]
    for idx, hz in enumerate(horizons):
        m = final_data["horizons"][hz]["metrics"]
        vals = [
            m["p50"]["mae_W_m2"],
            m["open_meteo"]["mae_W_m2"],
            m["p50"]["rmse_W_m2"],
            m["open_meteo"]["rmse_W_m2"],
        ]
        gx = lx + idx * group_w + (group_w - (len(vals) * bar_w + 3 * 4)) / 2
        for b_i, val in enumerate(vals):
            bx = gx + b_i * (bar_w + 4)
            bh = (val / max_y) * lh
            by = ly + lh - bh
            _, _, color, opacity = series[b_i]
            out.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w}" height="{bh:.1f}" '
                f'fill="{color}" fill-opacity="{opacity}" rx="2"/>'
            )
            out.append(
                f'<text x="{bx + bar_w / 2:.1f}" y="{by - 4:.1f}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="8.5" font-weight="600" fill="{INK}">{val:.0f}</text>'
            )
        delta_pct = (
            (m["p50"]["mae_W_m2"] - m["open_meteo"]["mae_W_m2"]) / m["open_meteo"]["mae_W_m2"] * 100
        )
        cx = lx + idx * group_w + group_w / 2
        out.append(
            f'<text x="{cx:.1f}" y="{ly + lh + 16}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="10.5" font-weight="600" fill="{INK}">{labels[idx]}</text>'
        )
        badge_color = TEAL if delta_pct < 0 else ORANGE
        sign = "+" if delta_pct > 0 else ""
        out.append(
            f'<text x="{cx:.1f}" y="{ly + lh + 30}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="9.2" font-weight="600" fill="{badge_color}">MAE {sign}{delta_pct:.1f}%</text>'
        )

    # Left Legend
    leg_x = lx + 4
    leg_y = ly + lh + 48
    for i, (_, name, color, op) in enumerate(series):
        col = i % 2
        row = i // 2
        x0 = leg_x + col * 185
        y0 = leg_y + row * 15
        out.append(
            f'<rect x="{x0}" y="{y0 - 8}" width="10" height="10" fill="{color}" fill-opacity="{op}" rx="1.5"/>'
        )
        out.append(
            f'<text x="{x0 + 15}" y="{y0 + 0.5}" font-family="{FONT}" font-size="9.5" fill="{INK}">{name}</text>'
        )

    # ---- Right Panel: PICP (%) vs Nominal 80% + Interval Width ----
    rx, ry, rw, rh = 500, 58, 330, 224
    out.append(
        f'<text x="{rx + rw / 2}" y="46" text-anchor="middle" font-family="{FONT}" '
        f'font-size="11.5" font-weight="600" fill="{INK}">(b) P10-P90 Coverage (PICP) &amp; Width</text>'
    )
    for tick in [0, 20, 40, 60, 80, 100]:
        y = ry + rh - (tick / 100.0) * rh
        out.append(
            f'<line x1="{rx}" y1="{y:.1f}" x2="{rx + rw}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{rx - 8}" y="{y + 3.5:.1f}" text-anchor="end" font-family="{FONT}" '
            f'font-size="10" fill="{MUTED}">{tick}%</text>'
        )
    # 80% nominal target line
    y80 = ry + rh - 0.80 * rh
    out.append(
        f'<line x1="{rx}" y1="{y80:.1f}" x2="{rx + rw}" y2="{y80:.1f}" '
        f'stroke="{ORANGE}" stroke-width="1.4" stroke-dasharray="5,3"/>'
    )
    out.append(
        f'<line x1="{rx}" y1="{ry + rh}" x2="{rx + rw}" y2="{ry + rh}" stroke="{INK}" stroke-width="1.2"/>'
    )
    out.append(
        f'<line x1="{rx}" y1="{ry}" x2="{rx}" y2="{ry + rh}" stroke="{INK}" stroke-width="1.2"/>'
    )
    out.append(
        f'<text x="{rx - 40}" y="{ry + rh / 2}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="10.5" fill="{INK}" transform="rotate(-90 {rx - 40} {ry + rh / 2})">PICP (%)</text>'
    )

    r_group_w = rw / len(horizons)
    r_bar_w = 32
    for idx, hz in enumerate(horizons):
        val_picp = val_data["native_validation"][hz]["picp_p10_p90"] * 100.0
        ft_picp = final_data["horizons"][hz]["metrics"]["picp_p10_p90"] * 100.0
        ft_width = final_data["horizons"][hz]["metrics"]["mean_interval_width_W_m2"]
        gx = rx + idx * r_group_w + (r_group_w - (2 * r_bar_w + 8)) / 2

        for b_i, (val_pct, color) in enumerate([(val_picp, SLATE), (ft_picp, TEAL)]):
            bx = gx + b_i * (r_bar_w + 8)
            bh = (val_pct / 100.0) * rh
            by = ry + rh - bh
            out.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{r_bar_w}" height="{bh:.1f}" fill="{color}" rx="2"/>'
            )
            out.append(
                f'<text x="{bx + r_bar_w / 2:.1f}" y="{by - 4:.1f}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="9" font-weight="600" fill="{INK}">{val_pct:.1f}%</text>'
            )

        cx = rx + idx * r_group_w + r_group_w / 2
        out.append(
            f'<text x="{cx:.1f}" y="{ry + rh + 16}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="10.5" font-weight="600" fill="{INK}">{labels[idx]}</text>'
        )
        out.append(
            f'<text x="{cx:.1f}" y="{ry + rh + 30}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="9" fill="{MUTED}">Width {ft_width:.0f} W/m²</text>'
        )

    # Right Legend
    r_leg_y = ry + rh + 46
    for i, (name, color) in enumerate(
        [("Validation PICP (Oct 29-30)", SLATE), ("Frozen Final Test PICP (Nov 2-5)", TEAL)]
    ):
        y0 = r_leg_y + i * 14
        out.append(
            f'<rect x="{rx + 8}" y="{y0 - 8}" width="10" height="10" fill="{color}" rx="1.5"/>'
        )
        out.append(
            f'<text x="{rx + 23}" y="{y0 + 0.5}" font-family="{FONT}" font-size="9.5" fill="{INK}">{name}</text>'
        )
    y_nom = r_leg_y + 28
    out.append(
        f'<line x1="{rx + 8}" y1="{y_nom - 3}" x2="{rx + 18}" y2="{y_nom - 3}" stroke="{ORANGE}" stroke-width="1.8" stroke-dasharray="4,2"/>'
    )
    out.append(
        f'<text x="{rx + 23}" y="{y_nom + 0.5}" font-family="{FONT}" font-size="9.5" fill="{ORANGE}">Nominal 80% coverage target</text>'
    )

    out.append("</svg>")
    return "\n".join(out)


def build_fig2_fr01_timeseries() -> str:
    plot_blob = json.loads((FIG_DIR / "fr01_plot_data.json").read_text(encoding="utf-8"))
    rows = []
    for ts, ref, om, p10, p50, p90 in plot_blob["holdout_2026_11_03"]:
        dt_utc = datetime.fromisoformat(ts)
        dt_kst = dt_utc.astimezone(timezone(timedelta(hours=9)))
        rows.append(
            {
                "kst_hour": dt_kst.hour + dt_kst.minute / 60.0,
                "ref": float(ref),
                "om": float(om),
                "p10": float(p10),
                "p50": float(p50),
                "p90": float(p90),
            }
        )
    rows.sort(key=lambda r: r["kst_hour"])

    w, h = 860, 320
    px, py, pw, ph = 66, 52, 764, 200
    min_hr, max_hr = 8.0, 17.5
    max_y = 950.0

    def sx(hr: float) -> float:
        return px + ((hr - min_hr) / (max_hr - min_hr)) * pw

    def sy(val: float) -> float:
        return py + ph - (val / max_y) * ph

    out = svg_header(
        w, h, "FR-01 10-Minute Probabilistic Nowcast Time Series on Final Test Day 2026-11-03"
    )
    out.append(
        f'<text x="{w / 2}" y="24" text-anchor="middle" font-family="{FONT}" '
        f'font-size="13.5" font-weight="700" fill="{INK}">'
        "FR-01 10-Minute Quantile Nowcast vs. Open-Meteo on Holdout Day 2026-11-03 (Seoul, KST)"
        "</text>"
    )
    out.append(
        f'<text x="{w / 2}" y="41" text-anchor="middle" font-family="{FONT}" '
        f'font-size="10" fill="{MUTED}">'
        "n = 49 valid daylight points | KMA V1 P50 MAE: 77.7 W/m² vs. Open-Meteo MAE: 169.4 W/m²"
        "</text>"
    )

    for tick in [0, 200, 400, 600, 800]:
        y = sy(tick)
        out.append(
            f'<line x1="{px}" y1="{y:.1f}" x2="{px + pw}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{px - 8}" y="{y + 3.5:.1f}" text-anchor="end" font-family="{FONT}" font-size="10" fill="{MUTED}">{tick}</text>'
        )

    for hr in range(8, 18):
        x = sx(float(hr))
        out.append(
            f'<line x1="{x:.1f}" y1="{py}" x2="{x:.1f}" y2="{py + ph}" stroke="#F3F4F6" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{x:.1f}" y="{py + ph + 16}" text-anchor="middle" font-family="{FONT}" font-size="10" fill="{MUTED}">{hr:02d}:00</text>'
        )

    out.append(
        f'<line x1="{px}" y1="{py + ph}" x2="{px + pw}" y2="{py + ph}" stroke="{INK}" stroke-width="1.2"/>'
    )
    out.append(
        f'<line x1="{px}" y1="{py}" x2="{px}" y2="{py + ph}" stroke="{INK}" stroke-width="1.2"/>'
    )
    out.append(
        f'<text x="18" y="{py + ph / 2}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="10.5" fill="{INK}" transform="rotate(-90 18 {py + ph / 2})">DNI (W/m²)</text>'
    )
    out.append(
        f'<text x="{px + pw / 2}" y="{py + ph + 33}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="10" fill="{INK}">Valid Time (KST, 2026-11-03)</text>'
    )

    # Shaded P10-P90 polygon
    upper = " ".join(f"{sx(r['kst_hour']):.1f},{sy(r['p90']):.1f}" for r in rows)
    lower = " ".join(f"{sx(r['kst_hour']):.1f},{sy(r['p10']):.1f}" for r in reversed(rows))
    out.append(
        f'<polygon points="{upper} {lower}" fill="{BAND_FILL}" fill-opacity="0.38" stroke="none"/>'
    )

    # Open-Meteo baseline
    om_pts = " ".join(f"{sx(r['kst_hour']):.1f},{sy(r['om']):.1f}" for r in rows)
    out.append(
        f'<polyline points="{om_pts}" fill="none" stroke="{ORANGE}" stroke-width="2" stroke-dasharray="6,4"/>'
    )

    # P50 prediction
    p50_pts = " ".join(f"{sx(r['kst_hour']):.1f},{sy(r['p50']):.1f}" for r in rows)
    out.append(f'<polyline points="{p50_pts}" fill="none" stroke="{NAVY}" stroke-width="2.2"/>')

    # Reference DSR-proxy DNI
    ref_pts = " ".join(f"{sx(r['kst_hour']):.1f},{sy(r['ref']):.1f}" for r in rows)
    out.append(f'<polyline points="{ref_pts}" fill="none" stroke="{INK}" stroke-width="1.8"/>')
    for r in rows:
        out.append(
            f'<circle cx="{sx(r["kst_hour"]):.1f}" cy="{sy(r["ref"]):.1f}" r="2.4" fill="{INK}"/>'
        )

    # Legend bar at bottom
    ly = h - 16
    items = [
        ("Reference DSR-proxy DNI", "line_dot", INK),
        ("KMA V1 P50 Nowcast", "line", NAVY),
        ("KMA V1 P10-P90 Interval", "box", BAND_FILL),
        ("Open-Meteo ECMWF IFS025", "dash", ORANGE),
    ]
    lx = 95
    for label, kind, color in items:
        if kind == "box":
            out.append(
                f'<rect x="{lx}" y="{ly - 8}" width="16" height="10" fill="{color}" fill-opacity="0.55" rx="1.5"/>'
            )
        elif kind == "dash":
            out.append(
                f'<line x1="{lx}" y1="{ly - 3}" x2="{lx + 18}" y2="{ly - 3}" stroke="{color}" stroke-width="2.2" stroke-dasharray="5,3"/>'
            )
        elif kind == "line_dot":
            out.append(
                f'<line x1="{lx}" y1="{ly - 3}" x2="{lx + 18}" y2="{ly - 3}" stroke="{color}" stroke-width="1.8"/>'
            )
            out.append(f'<circle cx="{lx + 9}" cy="{ly - 3}" r="2.6" fill="{color}"/>')
        else:
            out.append(
                f'<line x1="{lx}" y1="{ly - 3}" x2="{lx + 18}" y2="{ly - 3}" stroke="{color}" stroke-width="2.2"/>'
            )
        out.append(
            f'<text x="{lx + 24}" y="{ly + 0.5}" font-family="{FONT}" font-size="9.5" fill="{INK}">{label}</text>'
        )
        lx += 178

    out.append("</svg>")
    return "\n".join(out)


def run_fr03_sweep() -> dict[str, dict]:
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for noise in ["0.0", "2.0", "5.0"]:
            out_json = Path(tmp) / f"fr03_{noise}.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "server/tools/ai_experiments/run_fr03_vision_3d_experiment.py"),
                    "--noise-pct",
                    noise,
                    "--json-out",
                    str(out_json),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            results[noise] = json.loads(out_json.read_text(encoding="utf-8"))
    return results


def build_fig3_fr03_vision_error(sweep: dict[str, dict]) -> str:
    w, h = 860, 342
    out = svg_header(
        w, h, "FR-03 Vision AI 3D Furniture Estimation Error Across Simulated Noise Levels"
    )
    out.append(
        f'<text x="{w / 2}" y="24" text-anchor="middle" font-family="{FONT}" '
        f'font-size="13.5" font-weight="700" fill="{INK}">'
        "FR-03 Single-Photo Vision AI 3D Estimation Error vs. Simulated Noise (ArUco Pose Error = 0.0 cm)"
        "</text>"
    )

    items = ["Library Bookshelf", "Oak Study Desk", "Ergonomic Chair", "Mean (3 Items)"]
    noises = [("0.0", "0% Noise", SLATE), ("2.0", "2% Noise", NAVY), ("5.0", "5% Noise", TEAL)]

    def draw_panel(px: int, py: int, pw: int, ph: int, title: str, metric: str) -> None:
        max_y = 50.0
        out.append(
            f'<text x="{px + pw / 2}" y="{py - 12}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="11.5" font-weight="600" fill="{INK}">{title}</text>'
        )
        for tick in [0, 10, 20, 30, 40, 50]:
            y = py + ph - (tick / max_y) * ph
            out.append(
                f'<line x1="{px}" y1="{y:.1f}" x2="{px + pw}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>'
            )
            out.append(
                f'<text x="{px - 8}" y="{y + 3.5:.1f}" text-anchor="end" font-family="{FONT}" font-size="10" fill="{MUTED}">{tick}</text>'
            )
        out.append(
            f'<line x1="{px}" y1="{py + ph}" x2="{px + pw}" y2="{py + ph}" stroke="{INK}" stroke-width="1.2"/>'
        )
        out.append(
            f'<line x1="{px}" y1="{py}" x2="{px}" y2="{py + ph}" stroke="{INK}" stroke-width="1.2"/>'
        )
        out.append(
            f'<text x="{px - 36}" y="{py + ph / 2}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="10" fill="{INK}" transform="rotate(-90 {px - 36} {py + ph / 2})">Error (cm)</text>'
        )

        gw = pw / len(items)
        bw = 21
        for i, item_name in enumerate(items):
            gx = px + i * gw + (gw - (3 * bw + 8)) / 2
            for j, (n_key, _, color) in enumerate(noises):
                rep = sweep[n_key]
                if item_name.startswith("Mean"):
                    val = (
                        rep["mean_center_error_cm"]
                        if metric == "center"
                        else rep["mean_dim_error_cm"]
                    )
                else:
                    match = next(x for x in rep["items"] if x["name"] == item_name)
                    val = match["center_err_cm"] if metric == "center" else match["dim_err_cm"]
                bx = gx + j * (bw + 4)
                bh = (val / max_y) * ph
                by = py + ph - bh
                out.append(
                    f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw}" height="{bh:.1f}" fill="{color}" rx="2"/>'
                )
                out.append(
                    f'<text x="{bx + bw / 2:.1f}" y="{by - 3.5:.1f}" text-anchor="middle" '
                    f'font-family="{FONT}" font-size="8.2" font-weight="600" fill="{INK}">{val:.1f}</text>'
                )
            cx = px + i * gw + gw / 2
            short_label = (
                item_name.replace("Library ", "")
                .replace("Oak Study ", "")
                .replace("Ergonomic ", "")
            )
            out.append(
                f'<text x="{cx:.1f}" y="{py + ph + 16}" text-anchor="middle" font-family="{FONT}" '
                f'font-size="9.5" font-weight="600" fill="{INK}">{short_label}</text>'
            )

        # Draw 15 cm fine-boundary reference line cleanly over bars
        y_ref = py + ph - (15.0 / max_y) * ph
        out.append(
            f'<line x1="{px}" y1="{y_ref:.1f}" x2="{px + pw}" y2="{y_ref:.1f}" '
            f'stroke="{ORANGE}" stroke-width="1.5" stroke-dasharray="5,3"/>'
        )

    draw_panel(62, 60, 355, 200, "(a) 3D Center Localization Error (cm)", "center")
    draw_panel(482, 60, 355, 200, "(b) 3D Bounding-Box Dimension Error (cm)", "dim")

    # Shared Legend (centered row 1) + concise note (centered row 2)
    ly1 = h - 38
    lx = 165
    for _, name, color in noises:
        out.append(f'<rect x="{lx}" y="{ly1 - 8}" width="11" height="11" fill="{color}" rx="1.5"/>')
        out.append(
            f'<text x="{lx + 16}" y="{ly1 + 1}" font-family="{FONT}" font-size="9.5" fill="{INK}">{name}</text>'
        )
        lx += 110
    out.append(
        f'<line x1="{lx}" y1="{ly1 - 2.5}" x2="{lx + 16}" y2="{ly1 - 2.5}" stroke="{ORANGE}" stroke-width="1.8" stroke-dasharray="4,2"/>'
    )
    out.append(
        f'<text x="{lx + 22}" y="{ly1 + 1}" font-family="{FONT}" font-size="9.5" fill="{ORANGE}">Fine-protection boundary target (15 cm)</text>'
    )

    out.append(
        f'<text x="{w / 2}" y="{h - 15}" text-anchor="middle" font-family="{FONT}" font-size="9.3" fill="{MUTED}">'
        "Single-photo depth error (20-40 cm) motivates Stage-2 Guided ArUco P1-P4 refinement when precision is needed."
        "</text>"
    )

    out.append("</svg>")
    return "\n".join(out)


def build_fig4_fr04_control_tradeoff() -> str:
    synth_path = ROOT / "docs/experiments/fr04_adaptive_control_experiment_results.json"
    min_path = ROOT / "docs/experiments/fr04_minute_replay.json"
    synth = json.loads(synth_path.read_text(encoding="utf-8"))
    minute = json.loads(min_path.read_text(encoding="utf-8"))

    w, h = 860, 368
    out = svg_header(
        w, h, "FR-04 Adaptive Shading Control Flicker Reduction and Real 1-Minute Replay"
    )
    out.append(
        f'<text x="{w / 2}" y="24" text-anchor="middle" font-family="{FONT}" '
        f'font-size="13.5" font-weight="700" fill="{INK}">'
        "FR-04 Adaptive Control: Synthetic Benchmark &amp; Real Measured 1-Min Irradiance Replay"
        "</text>"
    )

    # ---- Left Panel: Synthetic Benchmark (Flicker Reduction % on Partly Cloudy & Squall) ----
    lx, ly, lw, lh = 62, 58, 365, 208
    out.append(
        f'<text x="{lx + lw / 2}" y="46" text-anchor="middle" font-family="{FONT}" '
        f'font-size="11.2" font-weight="600" fill="{INK}">(a) Synthetic Benchmark: Flicker Reduction (%)</text>'
    )
    max_y = 90.0
    for tick in [0, 20, 40, 60, 80]:
        y = ly + lh - (tick / max_y) * lh
        out.append(
            f'<line x1="{lx}" y1="{y:.1f}" x2="{lx + lw}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{lx - 8}" y="{y + 3.5:.1f}" text-anchor="end" font-family="{FONT}" font-size="10" fill="{MUTED}">{tick}%</text>'
        )
    out.append(
        f'<line x1="{lx}" y1="{ly + lh}" x2="{lx + lw}" y2="{ly + lh}" stroke="{INK}" stroke-width="1.2"/>'
    )
    out.append(
        f'<line x1="{lx}" y1="{ly}" x2="{lx}" y2="{ly + lh}" stroke="{INK}" stroke-width="1.2"/>'
    )
    out.append(
        f'<text x="18" y="{ly + lh / 2}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="10" fill="{INK}" transform="rotate(-90 18 {ly + lh / 2})">Opacity-Change Reduction (%)</text>'
    )

    scen_names = ["Sunny", "Partly Cloudy Rapid-Flicker", "High Uncertainty Squall"]
    short_scens = ["Sunny", "Partly Cloudy", "Uncertain Squall"]
    modes = [
        ("smooth", "FR-04 smooth_fr02 (default)", SLATE),
        ("pdlc", "FR-04 replace (PDLC)", NAVY),
        ("lg_sgf", "FR-04 replace (LG SGF 5-lvl)", TEAL),
    ]
    gw = lw / len(scen_names)
    bw = 23
    for i, sname in enumerate(scen_names):
        srec = next(s for s in synth["scenarios"] if s["scenario"] == sname)
        gx = lx + i * gw + (gw - (3 * bw + 18)) / 2
        for j, (mkey, _, color) in enumerate(modes):
            val = srec["signed_metrics"][mkey]["all_cells"]["flicker_reduction_pct"]
            bx = gx + j * (bw + 9)
            bh = max(0.0, (val / max_y) * lh)
            by = ly + lh - bh
            out.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw}" height="{bh:.1f}" fill="{color}" rx="2"/>'
            )
            out.append(
                f'<text x="{bx + bw / 2:.1f}" y="{by - 4:.1f}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="7.4" font-weight="600" fill="{INK}">+{val:.1f}%</text>'
            )
        sgf_sun = srec["signed_metrics"]["lg_sgf"]["grade_b_cells"]["sun_weighted_gain_pct"]
        cx = lx + i * gw + gw / 2
        out.append(
            f'<text x="{cx:.1f}" y="{ly + lh + 16}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="9.5" font-weight="600" fill="{INK}">{short_scens[i]}</text>'
        )
        out.append(
            f'<text x="{cx:.1f}" y="{ly + lh + 29}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="8.5" font-weight="600" fill="{TEAL}">SGF daylight +{sgf_sun:.1f}%</text>'
        )

    # Left Legend
    for i, (_, label, color) in enumerate(modes):
        y0 = ly + lh + 47 + i * 14
        out.append(
            f'<rect x="{lx + 6}" y="{y0 - 8}" width="10" height="10" fill="{color}" rx="1.5"/>'
        )
        out.append(
            f'<text x="{lx + 21}" y="{y0 + 0.5}" font-family="{FONT}" font-size="9.2" fill="{INK}">{label}</text>'
        )

    # ---- Right Panel: Real 1-Minute Measured Irradiance Replay (Switches) ----
    rx, ry, rw, rh = 496, 58, 336, 208
    out.append(
        f'<text x="{rx + rw / 2}" y="46" text-anchor="middle" font-family="{FONT}" '
        f'font-size="11.2" font-weight="600" fill="{INK}">(b) Real 1-Min SURFRAD Replay: Cell Switches</text>'
    )
    r_max = 650.0
    for tick in [0, 150, 300, 450, 600]:
        y = ry + rh - (tick / r_max) * rh
        out.append(
            f'<line x1="{rx}" y1="{y:.1f}" x2="{rx + rw}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{rx - 8}" y="{y + 3.5:.1f}" text-anchor="end" font-family="{FONT}" font-size="10" fill="{MUTED}">{tick}</text>'
        )
    out.append(
        f'<line x1="{rx}" y1="{ry + rh}" x2="{rx + rw}" y2="{ry + rh}" stroke="{INK}" stroke-width="1.2"/>'
    )
    out.append(
        f'<line x1="{rx}" y1="{ry}" x2="{rx}" y2="{ry + rh}" stroke="{INK}" stroke-width="1.2"/>'
    )
    out.append(
        f'<text x="{rx - 40}" y="{ry + rh / 2}" text-anchor="middle" font-family="{FONT}" '
        f'font-size="10" fill="{INK}" transform="rotate(-90 {rx - 40} {ry + rh / 2})">Switch Count (1-min step)</text>'
    )

    days = [
        ("2023-10-11", "Clear\n(Oct 11)"),
        ("2023-10-02", "Intermittent\n(Oct 02)"),
        ("2023-10-22", "Restless\n(Oct 22)"),
    ]
    rgw = rw / len(days)
    rbw = 34
    for i, (dstr, dlabel) in enumerate(days):
        drec = next(r for r in minute["results"] if r["station_date"] == dstr)
        c1 = next(c for c in drec["cases"] if c["interval_seconds"] == 60)
        fr02_sw = c1["fr02_switches"]
        fr04_sw = c1["fr04_switches"]
        chg = c1["switch_change_pct"]

        gx = rx + i * rgw + (rgw - (2 * rbw + 8)) / 2
        for j, (val, color) in enumerate([(fr02_sw, ORANGE), (fr04_sw, NAVY)]):
            bx = gx + j * (rbw + 8)
            bh = (val / r_max) * rh
            by = ry + rh - bh
            out.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{rbw}" height="{bh:.1f}" fill="{color}" rx="2"/>'
            )
            out.append(
                f'<text x="{bx + rbw / 2:.1f}" y="{by - 4:.1f}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="9" font-weight="600" fill="{INK}">{val}</text>'
            )

        cx = rx + i * rgw + rgw / 2
        line1, line2 = dlabel.split("\n")
        out.append(
            f'<text x="{cx:.1f}" y="{ry + rh + 15}" text-anchor="middle" font-family="{FONT}" font-size="9.5" font-weight="600" fill="{INK}">{line1}</text>'
        )
        out.append(
            f'<text x="{cx:.1f}" y="{ry + rh + 27}" text-anchor="middle" font-family="{FONT}" font-size="8.5" fill="{MUTED}">{line2}</text>'
        )
        if chg > 0:
            out.append(
                f'<text x="{cx:.1f}" y="{ry + rh + 39}" text-anchor="middle" font-family="{FONT}" '
                f'font-size="8.8" font-weight="700" fill="{TEAL}">-{chg:.1f}% switches</text>'
            )

    # Right Legend + daylight callout
    for i, (label, color) in enumerate(
        [("FR-02 Rule Baseline (1-min)", ORANGE), ("FR-04 smooth_fr02 (1-min)", NAVY)]
    ):
        y0 = ry + rh + 58 + i * 14
        out.append(
            f'<rect x="{rx + 8}" y="{y0 - 8}" width="10" height="10" fill="{color}" rx="1.5"/>'
        )
        out.append(
            f'<text x="{rx + 23}" y="{y0 + 0.5}" font-family="{FONT}" font-size="9.2" fill="{INK}">{label}</text>'
        )

    out.append("</svg>")
    return "\n".join(out)


def build_fig0_system_architecture() -> str:
    w, h = 860, 358
    out = svg_header(w, h, "SunSync-AI End-to-End 3-Track AIML System Architecture")
    out.append(
        "<defs>"
        '<marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M 0 1 L 10 5 L 0 9 z" fill="{NAVY}"/>'
        "</marker>"
        "</defs>"
    )
    out.append(
        f'<text x="{w / 2}" y="22" text-anchor="middle" font-family="{FONT}" '
        f'font-size="13.5" font-weight="700" fill="{INK}">'
        "SunSync-AI End-to-End Architecture: Vision 3D Sensing, Probabilistic Nowcasting, and Adaptive Control"
        "</text>"
    )

    def lane(x: int, y: int, lw: int, lh: int, accent: str, fill: str, title: str) -> None:
        out.append(
            f'<rect x="{x}" y="{y}" width="{lw}" height="{lh}" fill="{fill}" stroke="{accent}" stroke-width="1.3" rx="6"/>'
        )
        out.append(f'<rect x="{x}" y="{y}" width="{lw}" height="22" fill="{accent}" rx="5"/>')
        out.append(f'<rect x="{x}" y="{y + 16}" width="{lw}" height="6" fill="{accent}"/>')
        out.append(
            f'<text x="{x + 10}" y="{y + 14.5}" font-family="{FONT}" font-size="9.8" font-weight="700" fill="#FFFFFF">'
            f"{esc(title)}</text>"
        )

    def box(
        x: int, y: int, bw: int, bh: int, title: str, sub: str, stroke: str = "#CBD5E1"
    ) -> None:
        out.append(
            f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" fill="#FFFFFF" stroke="{stroke}" stroke-width="1.1" rx="4"/>'
        )
        out.append(
            f'<text x="{x + bw / 2}" y="{y + 18}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="9.2" font-weight="700" fill="{INK}">{esc(title)}</text>'
        )
        out.append(
            f'<text x="{x + bw / 2}" y="{y + 33}" text-anchor="middle" font-family="{FONT}" '
            f'font-size="8.0" fill="{MUTED}">{esc(sub)}</text>'
        )

    def arrow(x1: float, y1: float, x2: float, y2: float) -> None:
        out.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{NAVY}" stroke-width="1.4" marker-end="url(#arr)"/>'
        )

    # Lane 1 (Top): Track 2 Vision AI 3D Sensing
    lane(
        16,
        36,
        822,
        92,
        NAVY,
        "#F8FAFC",
        "Track 2: Hybrid Vision AI 3D Sensing & Guided ArUco Fallback (FR-03 & FR-06)",
    )
    box(28, 68, 178, 48, "Indoor Photo + 6x6 ArUco", "s = 0.6 m, solvePnP IPPE_SQUARE")
    box(228, 68, 196, 48, "YOLOv8m + Depth Anything V2", "SUNSYNC_VISION_AI lock + contour FB")
    box(446, 68, 184, 48, "3D Cuboids & VisionPreview", "Quality score + 2D room floorplan")
    box(652, 68, 174, 48, "Stage-2 Guided ArUco", "P1-P4 corner capture -> Scene DB", stroke=ORANGE)
    arrow(206, 92, 226, 92)
    arrow(424, 92, 444, 92)
    arrow(630, 92, 650, 92)

    # Lane 2 (Middle): Track 1 Solar Nowcasting
    lane(
        16,
        148,
        822,
        92,
        TEAL,
        "#F0FDFA",
        "Track 1: Sub-Hourly Probabilistic Solar Nowcasting (FR-01.4)",
    )
    box(28, 180, 178, 48, "KMA GK2A + Open-Meteo", "10-min DSR/CA + IFS025 (+7h delay)")
    box(228, 180, 196, 48, "Erbs Prior & Clear-Sky Norm", "k = DNI / DNI_clear (5 fixed features)")
    box(446, 180, 184, 48, "Frozen Linear Quantile V1", "L-BFGS-B P10 / P50 / P90 (10/30/60m)")
    box(
        652,
        180,
        174,
        48,
        "Operational Policy & UI",
        "10/30m V1, 60m OM, Provenance bar",
        stroke=TEAL,
    )
    arrow(206, 204, 226, 204)
    arrow(424, 204, 444, 204)
    arrow(630, 204, 650, 204)

    # Lane 3 (Bottom): Track 3 Adaptive Control & Rolling Precomputation
    lane(
        16,
        260,
        822,
        86,
        "#334155",
        "#F8FAFC",
        "Track 3: Rolling Time-Bucket Precomputation & Adaptive Smart Film Control (FR-02 / FR-04)",
    )
    box(28, 290, 192, 46, "Geometry Cache + Fingerprint", "_GEOMETRY_CACHE + SHA-256 weather")
    box(
        242,
        290,
        196,
        46,
        "10-min Rolling & Event Sync",
        "SQLite scenario_snapshots + single-flight",
    )
    box(460, 290, 184, 46, "FR-04 Stabilization / DP", "smooth_fr02 (default) / SGF 5-lvl DP")
    box(
        666,
        290,
        160,
        46,
        "Read-First UI & ThinQ ON",
        "GET /scenario & LG_THINQ_ON_SGF_V1",
        stroke=NAVY,
    )
    arrow(220, 313, 240, 313)
    arrow(438, 313, 458, 313)
    arrow(644, 313, 664, 313)

    # Right bus routing Track 2 (Scene Geometry) directly into Track 3 without passing through Track 1
    out.append(
        f'<polyline points="838,92 850,92 850,305 840,305" fill="none" stroke="{NAVY}" '
        'stroke-width="1.5" marker-end="url(#arr)"/>'
    )
    # Track 1 output feeds directly into Track 3
    arrow(124, 240, 124, 258)
    out.append(
        f'<text x="430" y="141" text-anchor="middle" font-family="{FONT}" font-size="8.1" font-weight="600" fill="{NAVY}">'
        "Track 2 registers 3D ENU Occluders &amp; Protected Regions (right bus -> Track 3 Geometry Cache &amp; Invalidation)"
        "</text>"
    )
    out.append(
        f'<text x="470" y="253" text-anchor="middle" font-family="{FONT}" font-size="8.1" font-weight="600" fill="{TEAL}">'
        "Track 1 Quantile DNI (P10 / P50 / P90) &amp; weather_fingerprint feed Track 3 Rolling Precomputation"
        "</text>"
    )

    out.append("</svg>")
    return "\n".join(out)


def export_svg_to_pdf(svg_path: Path, pdf_path: Path, width: int, height: int) -> None:
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome.exists():
        return
    with tempfile.TemporaryDirectory() as tmp:
        wrapper = Path(tmp) / "wrap.html"
        wrapper.write_text(
            f"<!DOCTYPE html><html><head><style>@page{{size:{width}px {height}px;margin:0;}}"
            f"html,body{{margin:0;padding:0;width:{width}px;height:{height}px;overflow:hidden;}}"
            f"img{{display:block;width:{width}px;height:{height}px;}}</style></head>"
            f'<body><img src="{svg_path.resolve().as_uri()}"/></body></html>',
            encoding="utf-8",
        )
        subprocess.run(
            [
                str(chrome),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf_path.resolve()}",
                wrapper.resolve().as_uri(),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    sweep = run_fr03_sweep()
    outputs: list[tuple[str, str, int, int]] = [
        ("fig0_system_architecture", build_fig0_system_architecture(), 860, 358),
        ("fig1_fr01_horizon_error", build_fig1_fr01_horizon_error(), 860, 368),
        ("fig2_fr01_timeseries_10min", build_fig2_fr01_timeseries(), 860, 320),
        ("fig3_fr03_vision_3d_error", build_fig3_fr03_vision_error(sweep), 860, 342),
        ("fig4_fr04_control_tradeoff", build_fig4_fr04_control_tradeoff(), 860, 368),
    ]
    for stem, content, w, h in outputs:
        svg_path = FIG_DIR / f"{stem}.svg"
        pdf_path = FIG_DIR / f"{stem}.pdf"
        svg_path.write_text(content, encoding="utf-8")
        export_svg_to_pdf(svg_path, pdf_path, w, h)
        print(f"Wrote {svg_path.name} ({len(content)} B) + {pdf_path.name}")


if __name__ == "__main__":
    main()
