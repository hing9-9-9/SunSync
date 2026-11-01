"""Render a local FR-01.4 Open-Meteo integration SQLite database as Markdown."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _cell(value: object) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|")


def _table(headers: list[str], rows: list[list[object]]) -> str:
    lines = [f"| {' | '.join(headers)} |", f"| {' | '.join('---' for _ in headers)} |"]
    lines.extend(f"| {' | '.join(_cell(value) for value in row)} |" for row in rows)
    return "\n".join(lines)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Path to a local integration.db file")
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_file():
        raise SystemExit(f"Database not found: {database}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    snapshots = connection.execute("""SELECT snapshot_id, provider, product, model, fetched_at,
        quality_status, raw_payload_hash, provenance_json FROM weather_snapshot ORDER BY fetched_at""").fetchall()
    points = connection.execute("""SELECT snapshot_id, valid_time, interval_start, interval_end,
        dni_W_m2, cloud_cover_percent, latitude_deg, longitude_deg
        FROM weather_point ORDER BY interval_start LIMIT 8""").fetchall()
    runs = connection.execute("""SELECT run_id, created_at, input_snapshot_ids_json, status
        FROM nowcast_run ORDER BY created_at""").fetchall()
    run_points = connection.execute("""SELECT run_id, valid_time, final_dni_W_m2,
        clear_sky_dni_W_m2, clear_sky_index, prediction_method, fallback_reason
        FROM nowcast_point ORDER BY valid_time LIMIT 12""").fetchall()
    print("# FR-01.4 Open-Meteo 로컬 검증 결과\n")
    print(f"- DB: `{database}`")
    print(f"- Snapshot: {len(snapshots)}개 · Weather point: {len(points)}개(앞 8개 표시) · Nowcast run: {len(runs)}개\n")
    print("## 수집 snapshot\n")
    print(_table(["ID", "Provider", "Model", "Fetched at", "상태", "Payload hash"], [[row["snapshot_id"], row["provider"], row["model"], row["fetched_at"], row["quality_status"], row["raw_payload_hash"]] for row in snapshots]))
    for row in snapshots:
        provenance = json.loads(row["provenance_json"] or "{}")
        print("\n### 요청 위치와 provider grid\n")
        print(_table(["항목", "값"], [
            ["요청 좌표", f'{provenance.get("request_latitude_deg")}, {provenance.get("request_longitude_deg")}'],
            ["Provider grid", f'{provenance.get("grid_latitude_deg")}, {provenance.get("grid_longitude_deg")}'],
            ["Grid elevation", provenance.get("grid_elevation_m")],
            ["Model selection", provenance.get("model_selection")],
            ["Resolved model", provenance.get("resolved_model")],
            ["Spatial provenance", provenance.get("spatial_provenance")],
        ]))
    print("\n## Hourly DNI point (앞 8개)\n")
    print(_table(["Interval", "DNI W/m²", "Cloud %", "Provider grid"], [[f'{row["interval_start"]} → {row["interval_end"]}', row["dni_W_m2"], row["cloud_cover_percent"], f'{row["latitude_deg"]}, {row["longitude_deg"]}'] for row in points]))
    print("\n## API 선택 기록\n")
    print(_table(["Run", "Created at", "상태", "Input snapshot"], [[row["run_id"], row["created_at"], row["status"], row["input_snapshot_ids_json"]] for row in runs]))
    print("\n## Timestamp별 최종 DNI (앞 12개)\n")
    print(_table(["Timestamp", "최종 DNI", "Clear-sky DNI", "Index", "방법", "Fallback"], [[row["valid_time"], row["final_dni_W_m2"], row["clear_sky_dni_W_m2"], row["clear_sky_index"], row["prediction_method"], row["fallback_reason"]] for row in run_points]))
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
