"""Leakage-safe dataset builder for the first FR-01 KMA P50 experiment."""

from __future__ import annotations

import csv
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.nowcast.kma_ml import calculate_solar_geometry, dsr_to_dni_physics
from engine.fr01.models import Site

FEATURE_NAMES = (
    "intercept",
    "direct_prior_factor",
    "open_meteo_factor",
    "cloud_transmittance",
    "advection_impact",
)
HORIZON_MINUTES = 10
ALLOWED_HORIZON_MINUTES = (10, 30, 60)
MIN_SOLAR_ELEVATION_DEG = 5.0
FINAL_TEST_START = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
FINAL_TEST_END = datetime(2026, 11, 5, 23, 50, tzinfo=UTC)
DEVELOPMENT_START = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
VALIDATION_START = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
DIAGNOSTIC_START = datetime(2026, 10, 31, 0, 0, tzinfo=UTC)
MIN_OPEN_METEO_AVAILABILITY_DELAY = timedelta(hours=7)


def parse_utc(value: str, *, field: str) -> datetime:
    """Parse an explicitly timezone-aware timestamp and normalize it to UTC."""

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"invalid {field}: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone: {value}")
    return parsed.astimezone(UTC)


def utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class KmaInputPoint:
    timestamp: datetime
    dsr_W_m2: float | None
    cloud_cover_pct: float | None


@dataclass(frozen=True)
class OpenMeteoPoint:
    snapshot_available_at: datetime
    issued_at: datetime | None
    valid_time: datetime
    dni_W_m2: float
    snapshot_id: str


@dataclass(frozen=True)
class ModelDatasetRow:
    prediction_origin_utc: str
    input_kma_timestamp_utc: str
    open_meteo_snapshot_available_at_utc: str
    open_meteo_issued_at_utc: str
    open_meteo_valid_time_utc: str
    target_dsr_timestamp_utc: str
    valid_time_utc: str
    horizon_minutes: int
    dsr_input_W_m2: float
    cloud_cover_input_pct: float
    open_meteo_dni_W_m2: float
    solar_elevation_origin_deg: float
    solar_elevation_target_deg: float
    clear_sky_dni_origin_W_m2: float
    clear_sky_dni_target_W_m2: float
    kma_dni_prior_W_m2: float
    intercept: float
    direct_prior_factor: float
    open_meteo_factor: float
    cloud_transmittance: float
    advection_impact: float
    reference_dni_W_m2: float
    reference_dni_factor: float
    reference_type: str
    cloud_patch_used: bool

    def features(self) -> tuple[float, float, float, float, float]:
        return (
            self.intercept,
            self.direct_prior_factor,
            self.open_meteo_factor,
            self.cloud_transmittance,
            self.advection_impact,
        )


@dataclass(frozen=True)
class DatasetBuildResult:
    rows: tuple[ModelDatasetRow, ...]
    source_kma_rows: int
    excluded: dict[str, int]
    dsr_unit_assumed: bool


@dataclass(frozen=True)
class DevelopmentSplits:
    train: tuple[ModelDatasetRow, ...]
    validation: tuple[ModelDatasetRow, ...]
    diagnostic: tuple[ModelDatasetRow, ...]
    boundaries: dict[str, str]


def _optional_float(value: str | None, *, field: str, line_number: int) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        number = float(value)
    except ValueError as error:
        raise ValueError(f"invalid {field} at line {line_number}") from error
    if not math.isfinite(number):
        raise ValueError(f"non-finite {field} at line {line_number}")
    return number


def load_kma_points(path: Path, *, assume_dsr_w_m2: bool = False) -> list[KmaInputPoint]:
    """Load normalized KMA data without silently accepting an unknown DSR unit."""

    required = {"timestamp_utc", "dsr_raw", "cloud_cover_pct", "dsr_unit", "unit_confirmed"}
    points: list[KmaInputPoint] = []
    seen: set[datetime] = set()
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(f"KMA CSV missing columns: {', '.join(missing)}")
        for line_number, row in enumerate(reader, start=2):
            timestamp = parse_utc(row["timestamp_utc"], field="timestamp_utc")
            if timestamp in seen:
                raise ValueError(f"duplicate KMA timestamp at line {line_number}")
            seen.add(timestamp)
            unit_confirmed = row["unit_confirmed"].strip().lower() == "true"
            unit = row["dsr_unit"].strip().lower().replace("²", "2")
            if not assume_dsr_w_m2 and not (unit_confirmed and unit in {"w/m2", "w_m2"}):
                raise ValueError(
                    "KMA DSR unit is not confirmed as W/m2; pass assume_dsr_w_m2=True "
                    "only after recording that experiment assumption"
                )
            dsr = _optional_float(row["dsr_raw"], field="dsr_raw", line_number=line_number)
            cloud = _optional_float(
                row["cloud_cover_pct"], field="cloud_cover_pct", line_number=line_number
            )
            if dsr is not None and dsr < 0:
                raise ValueError(f"negative dsr_raw at line {line_number}")
            if cloud is not None and not 0.0 <= cloud <= 100.0:
                cloud = None
            points.append(KmaInputPoint(timestamp, dsr, cloud))
    if not points:
        raise ValueError("KMA CSV contains no rows")
    return sorted(points, key=lambda point: point.timestamp)


def load_open_meteo_points(path: Path) -> list[OpenMeteoPoint]:
    """Load immutable forecast snapshots; available_at is the information cutoff."""

    required = {
        "snapshot_id",
        "snapshot_available_at_utc",
        "issued_at_utc",
        "valid_time_utc",
        "open_meteo_dni_W_m2",
    }
    points: list[OpenMeteoPoint] = []
    seen: set[tuple[str, datetime]] = set()
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(f"Open-Meteo CSV missing columns: {', '.join(missing)}")
        for line_number, row in enumerate(reader, start=2):
            available = parse_utc(
                row["snapshot_available_at_utc"], field="snapshot_available_at_utc"
            )
            issued_raw = row["issued_at_utc"].strip()
            if not issued_raw:
                raise ValueError(f"empty issued_at_utc at line {line_number}")
            issued = parse_utc(issued_raw, field="issued_at_utc")
            available = max(available, issued + MIN_OPEN_METEO_AVAILABILITY_DELAY)
            valid = parse_utc(row["valid_time_utc"], field="valid_time_utc")
            dni = _optional_float(
                row["open_meteo_dni_W_m2"],
                field="open_meteo_dni_W_m2",
                line_number=line_number,
            )
            snapshot_id = row["snapshot_id"].strip()
            if not snapshot_id:
                raise ValueError(f"empty snapshot_id at line {line_number}")
            if dni is None or dni < 0:
                raise ValueError(f"invalid Open-Meteo DNI at line {line_number}")
            key = (snapshot_id, valid)
            if key in seen:
                raise ValueError(f"duplicate Open-Meteo snapshot point at line {line_number}")
            seen.add(key)
            points.append(OpenMeteoPoint(available, issued, valid, dni, snapshot_id))
    if not points:
        raise ValueError("Open-Meteo CSV contains no rows")
    return sorted(points, key=lambda point: (point.snapshot_available_at, point.valid_time))


def assert_training_data_excludes_final_test(kma_points: list[KmaInputPoint]) -> None:
    """Prevent the frozen 2026-11-02~05 holdout from entering a training run."""

    overlaps = [
        point.timestamp
        for point in kma_points
        if FINAL_TEST_START <= point.timestamp <= FINAL_TEST_END
    ]
    if overlaps:
        raise ValueError(
            "training data overlaps frozen final test set "
            f"({utc_z(FINAL_TEST_START)} through {utc_z(FINAL_TEST_END)}); "
            "use this period only in the final evaluation command"
        )


def _open_meteo_at(
    points: list[OpenMeteoPoint], prediction_origin: datetime, valid_time: datetime
) -> OpenMeteoPoint | None:
    """Map a target into the containing preceding-hour mean of an available snapshot."""

    eligible = [point for point in points if point.snapshot_available_at <= prediction_origin]
    if not eligible:
        return None
    latest_available = max(point.snapshot_available_at for point in eligible)
    latest = [point for point in eligible if point.snapshot_available_at == latest_available]
    by_snapshot: dict[str, list[OpenMeteoPoint]] = defaultdict(list)
    for point in latest:
        by_snapshot[point.snapshot_id].append(point)
    snapshot_id = max(by_snapshot)
    values = sorted(by_snapshot[snapshot_id], key=lambda point: point.valid_time)
    exact = next((point for point in values if point.valid_time == valid_time), None)
    if exact is not None:
        return exact
    covering = next((point for point in values if point.valid_time > valid_time), None)
    if covering is None:
        return None
    distance = (covering.valid_time - valid_time).total_seconds()
    if distance <= 0 or distance > 3600:
        return None
    return OpenMeteoPoint(
        snapshot_available_at=covering.snapshot_available_at,
        issued_at=covering.issued_at,
        valid_time=valid_time,
        dni_W_m2=covering.dni_W_m2,
        snapshot_id=covering.snapshot_id,
    )


def build_model_dataset(
    kma_points: list[KmaInputPoint],
    open_meteo_points: list[OpenMeteoPoint],
    site: Site,
    *,
    horizon_minutes: int = HORIZON_MINUTES,
    dsr_unit_assumed: bool = False,
    min_solar_elevation_deg: float = MIN_SOLAR_ELEVATION_DEG,
) -> DatasetBuildResult:
    """Build leakage-safe rows for an allowed future DSR-derived DNI horizon."""

    if horizon_minutes not in ALLOWED_HORIZON_MINUTES:
        raise ValueError(
            f"horizon_minutes must be one of {ALLOWED_HORIZON_MINUTES}, got {horizon_minutes}"
        )

    kma_by_time = {point.timestamp: point for point in kma_points}
    geometries = calculate_solar_geometry(site, [point.timestamp for point in kma_points])
    geometry_by_time = dict(zip((point.timestamp for point in kma_points), geometries, strict=True))
    excluded: Counter[str] = Counter()
    rows: list[ModelDatasetRow] = []
    horizon = timedelta(minutes=horizon_minutes)

    for origin_point in kma_points:
        origin = origin_point.timestamp
        valid_time = origin + horizon
        target_point = kma_by_time.get(valid_time)
        if target_point is None:
            excluded["missing_target_timestamp"] += 1
            continue
        if origin_point.dsr_W_m2 is None:
            excluded["missing_input_dsr"] += 1
            continue
        if origin_point.cloud_cover_pct is None:
            excluded["missing_input_cloud"] += 1
            continue
        if target_point.dsr_W_m2 is None:
            excluded["missing_target_dsr"] += 1
            continue

        origin_geometry = geometry_by_time[origin]
        target_geometry = geometry_by_time[valid_time]
        if (
            origin_geometry["solar_elevation_deg"] < min_solar_elevation_deg
            or target_geometry["solar_elevation_deg"] < min_solar_elevation_deg
            or origin_geometry["clear_sky_dni"] <= 0
            or target_geometry["clear_sky_dni"] <= 0
        ):
            excluded["low_solar_elevation"] += 1
            continue

        open_meteo = _open_meteo_at(open_meteo_points, origin, valid_time)
        if open_meteo is None:
            excluded["missing_eligible_open_meteo"] += 1
            continue

        _, _, kma_prior = dsr_to_dni_physics(
            origin_point.dsr_W_m2,
            origin_geometry["clear_sky_dni"],
            origin_geometry["zenith_deg"],
            origin,
        )
        _, _, reference_dni = dsr_to_dni_physics(
            target_point.dsr_W_m2,
            target_geometry["clear_sky_dni"],
            target_geometry["zenith_deg"],
            valid_time,
        )
        k_erbs = kma_prior / origin_geometry["clear_sky_dni"]
        k_om = open_meteo.dni_W_m2 / target_geometry["clear_sky_dni"]
        obs_weight = math.exp(-(horizon_minutes / 60.0) / 1.5)
        direct_prior = k_erbs * obs_weight + k_om * (1.0 - obs_weight)
        target_factor = min(1.1, max(0.0, reference_dni / target_geometry["clear_sky_dni"]))

        rows.append(
            ModelDatasetRow(
                prediction_origin_utc=utc_z(origin),
                input_kma_timestamp_utc=utc_z(origin),
                open_meteo_snapshot_available_at_utc=utc_z(open_meteo.snapshot_available_at),
                open_meteo_issued_at_utc=utc_z(open_meteo.issued_at)
                if open_meteo.issued_at
                else "",
                open_meteo_valid_time_utc=utc_z(open_meteo.valid_time),
                target_dsr_timestamp_utc=utc_z(target_point.timestamp),
                valid_time_utc=utc_z(valid_time),
                horizon_minutes=horizon_minutes,
                dsr_input_W_m2=origin_point.dsr_W_m2,
                cloud_cover_input_pct=origin_point.cloud_cover_pct,
                open_meteo_dni_W_m2=open_meteo.dni_W_m2,
                solar_elevation_origin_deg=origin_geometry["solar_elevation_deg"],
                solar_elevation_target_deg=target_geometry["solar_elevation_deg"],
                clear_sky_dni_origin_W_m2=origin_geometry["clear_sky_dni"],
                clear_sky_dni_target_W_m2=target_geometry["clear_sky_dni"],
                kma_dni_prior_W_m2=kma_prior,
                intercept=1.0,
                direct_prior_factor=direct_prior,
                open_meteo_factor=k_om,
                cloud_transmittance=1.0 - origin_point.cloud_cover_pct / 100.0,
                advection_impact=0.0,
                reference_dni_W_m2=reference_dni,
                reference_dni_factor=target_factor,
                reference_type="dsr_proxy",
                cloud_patch_used=False,
            )
        )

    return DatasetBuildResult(
        tuple(rows), len(kma_points), dict(sorted(excluded.items())), dsr_unit_assumed
    )


def build_10min_dataset(
    kma_points: list[KmaInputPoint],
    open_meteo_points: list[OpenMeteoPoint],
    site: Site,
    *,
    dsr_unit_assumed: bool = False,
    min_solar_elevation_deg: float = MIN_SOLAR_ELEVATION_DEG,
) -> DatasetBuildResult:
    """Compatibility wrapper for the frozen 10-minute dataset contract."""

    return build_model_dataset(
        kma_points,
        open_meteo_points,
        site,
        horizon_minutes=HORIZON_MINUTES,
        dsr_unit_assumed=dsr_unit_assumed,
        min_solar_elevation_deg=min_solar_elevation_deg,
    )


def validate_no_leakage(rows: tuple[ModelDatasetRow, ...] | list[ModelDatasetRow]) -> None:
    """Fail closed when a row contains information unavailable at prediction time."""

    for index, row in enumerate(rows, start=1):
        origin = parse_utc(row.prediction_origin_utc, field="prediction_origin_utc")
        valid = parse_utc(row.valid_time_utc, field="valid_time_utc")
        input_kma = parse_utc(row.input_kma_timestamp_utc, field="input_kma_timestamp_utc")
        available = parse_utc(
            row.open_meteo_snapshot_available_at_utc,
            field="open_meteo_snapshot_available_at_utc",
        )
        target = parse_utc(row.target_dsr_timestamp_utc, field="target_dsr_timestamp_utc")
        om_valid = parse_utc(row.open_meteo_valid_time_utc, field="open_meteo_valid_time_utc")
        if valid - origin != timedelta(minutes=row.horizon_minutes):
            raise ValueError(f"row {index}: invalid horizon")
        if input_kma > origin:
            raise ValueError(f"row {index}: future KMA input")
        if available > origin:
            raise ValueError(f"row {index}: future Open-Meteo snapshot")
        if target != valid:
            raise ValueError(f"row {index}: target DSR does not match valid time")
        if om_valid != valid:
            raise ValueError(f"row {index}: Open-Meteo value does not match valid time")


def split_development_rows(rows: tuple[ModelDatasetRow, ...]) -> DevelopmentSplits:
    """Apply fixed UTC development splits with a target-time embargo at every boundary."""

    def timestamps(row: ModelDatasetRow) -> tuple[datetime, datetime]:
        return (
            parse_utc(row.prediction_origin_utc, field="prediction_origin_utc"),
            parse_utc(row.valid_time_utc, field="valid_time_utc"),
        )

    train = tuple(
        row
        for row in rows
        if DEVELOPMENT_START <= (values := timestamps(row))[0] < VALIDATION_START
        and values[1] < VALIDATION_START
    )
    validation = tuple(
        row
        for row in rows
        if VALIDATION_START <= (values := timestamps(row))[0] < DIAGNOSTIC_START
        and values[1] < DIAGNOSTIC_START
    )
    diagnostic = tuple(
        row
        for row in rows
        if DIAGNOSTIC_START <= (values := timestamps(row))[0] < FINAL_TEST_START
        and values[1] < FINAL_TEST_START
    )
    if min(len(train), len(validation), len(diagnostic)) == 0:
        raise ValueError("one or more fixed development splits contain no valid rows")
    if max(timestamps(row)[1] for row in train) >= VALIDATION_START:
        raise AssertionError("train target crosses validation boundary")
    if max(timestamps(row)[1] for row in validation) >= DIAGNOSTIC_START:
        raise AssertionError("validation target crosses diagnostic boundary")
    if max(timestamps(row)[1] for row in diagnostic) >= FINAL_TEST_START:
        raise AssertionError("diagnostic target crosses final-test boundary")

    return DevelopmentSplits(
        train=train,
        validation=validation,
        diagnostic=diagnostic,
        boundaries={
            "train_start": utc_z(DEVELOPMENT_START),
            "train_target_before": utc_z(VALIDATION_START),
            "validation_start": utc_z(VALIDATION_START),
            "validation_target_before": utc_z(DIAGNOSTIC_START),
            "diagnostic_start": utc_z(DIAGNOSTIC_START),
            "diagnostic_target_before": utc_z(FINAL_TEST_START),
            "split_embargo_rule": "valid_time < next_split_start",
        },
    )


def write_dataset_csv(rows: tuple[ModelDatasetRow, ...], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("cannot write an empty model dataset")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
