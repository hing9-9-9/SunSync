"""KMA GK2A 10-minute DSR/cloud adapter and fixed-width response parser."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from app.nowcast.adapters import RawPoint, RawSnapshot
from engine.fr01.models import Site

KMA_GK2A_URL = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph_sun_sat_txt"
KMA_DSR_UNIT_SOURCE_URL = (
    "https://api.nmsc.kma.go.kr/homepage/html/base/cmm/selectPage.do"
    "?page=static.convergence.utilizationProducts"
)
KMA_INTERVAL_MINUTES = 10
KMA_MAX_POINTS_PER_REQUEST = 24
MISSING_TOKENS = {
    "",
    "nan",
    "-9",
    "-9.0",
    "-99",
    "-99.0",
    "-999",
    "-999.0",
    "-9999",
    "6553.5",
}


class KmaGk2aError(ValueError):
    """Sanitized provider or response error that never contains the authentication key."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class KmaSeries:
    """One KMA variable returned for one point across an ordered UTC timeline."""

    variable: str
    base_time: datetime
    longitude_deg: float
    latitude_deg: float
    values: tuple[tuple[datetime, float | None], ...]


@dataclass(frozen=True)
class KmaCollection:
    """Paired DSR and cloud responses plus their normalized durable snapshot."""

    site: Site
    start_time: datetime
    end_time: datetime
    fetched_at: datetime
    dsr_payloads: tuple[bytes, ...]
    cloud_payloads: tuple[bytes, ...]
    dsr: KmaSeries
    cloud: KmaSeries
    snapshot: RawSnapshot

    @property
    def dsr_payload(self) -> bytes:
        """Return the sole raw response for short requests; combine chunks for compatibility."""

        return b"\n".join(self.dsr_payloads)

    @property
    def cloud_payload(self) -> bytes:
        """Return the sole raw response for short requests; combine chunks for compatibility."""

        return b"\n".join(self.cloud_payloads)


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_provider_time(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%d%H%M").replace(tzinfo=UTC)
    except ValueError as error:
        raise KmaGk2aError("kma_invalid_timestamp") from error


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [part.strip() for part in stripped[1:-1].split("|")]


def parse_kma_wide_payload(
    payload: bytes, expected_variable: str, *, interval_minutes: int = KMA_INTERVAL_MINUTES
) -> KmaSeries:
    """Parse KMA's wide fixed-width table into an ordered UTC value series."""

    try:
        document = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise KmaGk2aError("kma_invalid_encoding") from error

    rows = [_table_cells(line) for line in document.splitlines()]
    rows = [row for row in rows if row]
    header = next(
        (row for row in rows if len(row) >= 5 and row[:4] == ["TMFC", "VAR", "LON", "LAT"]),
        None,
    )
    if header is None:
        raise KmaGk2aError("kma_invalid_header")

    variable = expected_variable.upper()
    data = next((row for row in rows if len(row) >= 5 and row[1].upper() == variable), None)
    if data is None or len(data) != len(header):
        raise KmaGk2aError("kma_invalid_data_row")

    try:
        base_time = _parse_provider_time(data[0])
        longitude = float(data[2])
        latitude = float(data[3])
    except (TypeError, ValueError) as error:
        raise KmaGk2aError("kma_invalid_coordinates") from error
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise KmaGk2aError("kma_invalid_coordinates")

    timestamps = tuple(_parse_provider_time(value) for value in header[4:])
    if not timestamps:
        raise KmaGk2aError("kma_empty_response")
    expected_step = timedelta(minutes=interval_minutes)
    if any(current - previous != expected_step for previous, current in pairwise(timestamps)):
        raise KmaGk2aError("kma_unexpected_interval")

    parsed: list[tuple[datetime, float | None]] = []
    for timestamp, token in zip(timestamps, data[4:], strict=True):
        if token.lower() in MISSING_TOKENS:
            parsed.append((timestamp, None))
            continue
        try:
            value = float(token)
        except ValueError as error:
            raise KmaGk2aError("kma_invalid_value") from error
        if not math.isfinite(value):
            raise KmaGk2aError("kma_invalid_value")
        if variable == "DSR" and value < 0:
            raise KmaGk2aError("kma_invalid_dsr")
        parsed.append((timestamp, value))

    return KmaSeries(
        variable=variable,
        base_time=base_time,
        longitude_deg=longitude,
        latitude_deg=latitude,
        values=tuple(parsed),
    )


def build_kma_snapshot(
    site: Site,
    start_time: datetime,
    end_time: datetime,
    fetched_at: datetime,
    dsr_payload: bytes,
    cloud_payload: bytes,
    dsr: KmaSeries,
    cloud: KmaSeries,
) -> RawSnapshot:
    """Combine paired provider series into the existing immutable snapshot contract."""

    if (dsr.longitude_deg, dsr.latitude_deg) != (cloud.longitude_deg, cloud.latitude_deg):
        raise KmaGk2aError("kma_grid_mismatch")
    dsr_values = dict(dsr.values)
    cloud_values = dict(cloud.values)
    timestamps = sorted(set(dsr_values) | set(cloud_values))
    if not timestamps:
        raise KmaGk2aError("kma_empty_response")

    points: list[RawPoint] = []
    degraded = False
    for timestamp in timestamps:
        dsr_value = dsr_values.get(timestamp)
        cloud_raw = cloud_values.get(timestamp)
        cloud_fraction = cloud_raw if cloud_raw is not None and 0.0 <= cloud_raw <= 1.0 else None
        if dsr_value is None or cloud_fraction is None:
            degraded = True
        points.append(
            RawPoint(
                valid_time=timestamp,
                latitude_deg=dsr.latitude_deg,
                longitude_deg=dsr.longitude_deg,
                ghi_W_m2=dsr_value,
                cloud_cover_percent=cloud_fraction * 100.0 if cloud_fraction is not None else None,
                temporal_meaning="instant",
            )
        )

    combined_payload = b"--DSR--\n" + dsr_payload + b"\n--CA--\n" + cloud_payload
    return RawSnapshot(
        provider="kma_gk2a",
        product="10min_dsr_cloud_point",
        payload=combined_payload,
        points=tuple(points),
        model="gk2a",
        observed_at=max(timestamps),
        quality_status="degraded" if degraded else "valid",
        provenance={
            "endpoint": KMA_GK2A_URL,
            "request_latitude_deg": site.latitude_deg,
            "request_longitude_deg": site.longitude_deg,
            "grid_latitude_deg": dsr.latitude_deg,
            "grid_longitude_deg": dsr.longitude_deg,
            "requested_start_utc": start_time.isoformat(),
            "requested_end_utc": end_time.isoformat(),
            "fetched_at_utc": fetched_at.isoformat(),
            "interval_minutes": KMA_INTERVAL_MINUTES,
            "variables": ["DSR", "CA"],
            "dsr_unit": "W/m2",
            "unit_confirmed": True,
            "dsr_unit_source": KMA_DSR_UNIT_SOURCE_URL,
            "cloud_source_scale": "fraction_0_1",
        },
    )


@dataclass(frozen=True)
class KmaGk2aAdapter:
    """Fetch paired 10-minute DSR and cloud point products from KMA API Hub."""

    site: Site
    api_key: str = field(repr=False)
    timeout_seconds: float = 90.0
    max_attempts: int = 3
    retry_base_seconds: float = 1.0
    max_concurrency: int = 6
    opener: Callable[..., Any] = urlopen
    sleeper: Callable[[float], None] = time.sleep

    def _fetch_variable(self, start: datetime, end: datetime, variable: str) -> bytes:
        query = urlencode(
            {
                "tm1": start.strftime("%Y%m%d%H%M"),
                "tm2": end.strftime("%Y%m%d%H%M"),
                "int": str(KMA_INTERVAL_MINUTES),
                "varn": variable,
                "lat": f"{self.site.latitude_deg:.6f}",
                "lon": f"{self.site.longitude_deg:.6f}",
                "authKey": self.api_key,
            }
        )
        url = f"{KMA_GK2A_URL}?{query}"
        for attempt in range(1, self.max_attempts + 1):
            try:
                with self.opener(url, timeout=self.timeout_seconds) as response:  # nosec B310
                    if getattr(response, "status", 200) != 200:
                        raise KmaGk2aError("kma_transport_error")
                    return response.read()
            except HTTPError as error:
                if error.code < 500 or attempt == self.max_attempts:
                    raise KmaGk2aError("kma_transport_error") from None
            except (TimeoutError, URLError, OSError):
                if attempt == self.max_attempts:
                    raise KmaGk2aError("kma_transport_error") from None
            self.sleeper(self.retry_base_seconds * (2 ** (attempt - 1)))
        raise KmaGk2aError("kma_transport_error")

    def fetch_chunk(
        self, start_time: datetime, end_time: datetime, variable: str
    ) -> tuple[bytes, KmaSeries]:
        """Fetch and validate one provider-sized chunk for a durable work queue."""

        if not self.api_key.strip():
            raise KmaGk2aError("kma_api_key_missing")
        start = _utc(start_time, "start_time")
        end = _utc(end_time, "end_time")
        normalized_variable = variable.upper()
        if normalized_variable not in {"DSR", "CA"}:
            raise ValueError("variable must be DSR or CA")
        if start > end:
            raise ValueError("start_time must not be after end_time")
        if any(
            value.second or value.microsecond or value.minute % KMA_INTERVAL_MINUTES
            for value in (start, end)
        ):
            raise ValueError("chunk boundaries must align to 10-minute UTC boundaries")
        point_count = int((end - start) / timedelta(minutes=KMA_INTERVAL_MINUTES)) + 1
        if point_count > KMA_MAX_POINTS_PER_REQUEST:
            raise ValueError("chunk exceeds provider 24-point limit")

        payload = self._fetch_variable(start, end, normalized_variable)
        series = parse_kma_wide_payload(payload, normalized_variable)
        if not series.values or series.values[0][0] != start or series.values[-1][0] != end:
            raise KmaGk2aError("kma_unexpected_range")
        return payload, series

    def _fetch_series(
        self, start: datetime, end: datetime, variable: str
    ) -> tuple[tuple[bytes, ...], KmaSeries]:
        ranges: list[tuple[datetime, datetime]] = []
        current = start
        maximum_span = timedelta(minutes=KMA_INTERVAL_MINUTES * (KMA_MAX_POINTS_PER_REQUEST - 1))
        while current <= end:
            chunk_end = min(end, current + maximum_span)
            ranges.append((current, chunk_end))
            current = chunk_end + timedelta(minutes=KMA_INTERVAL_MINUTES)

        def fetch_chunk(time_range: tuple[datetime, datetime]) -> tuple[bytes, KmaSeries]:
            chunk_start, chunk_end = time_range
            return self.fetch_chunk(chunk_start, chunk_end, variable)

        workers = min(max(1, self.max_concurrency), len(ranges))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            fetched_chunks = list(executor.map(fetch_chunk, ranges))

        payloads: list[bytes] = []
        chunks: list[KmaSeries] = []
        for (chunk_start, chunk_end), (payload, series) in zip(ranges, fetched_chunks, strict=True):
            if (
                not series.values
                or series.values[0][0] != chunk_start
                or series.values[-1][0] != chunk_end
            ):
                raise KmaGk2aError("kma_unexpected_range")
            payloads.append(payload)
            chunks.append(series)

        if not chunks:
            raise KmaGk2aError("kma_empty_response")
        first = chunks[0]
        if any(
            (chunk.longitude_deg, chunk.latitude_deg) != (first.longitude_deg, first.latitude_deg)
            for chunk in chunks[1:]
        ):
            raise KmaGk2aError("kma_grid_mismatch")
        values = tuple(item for chunk in chunks for item in chunk.values)
        expected_step = timedelta(minutes=KMA_INTERVAL_MINUTES)
        if any(
            current_time - previous != expected_step
            for previous, current_time in pairwise(t for t, _ in values)
        ):
            raise KmaGk2aError("kma_unexpected_interval")
        return (
            tuple(payloads),
            KmaSeries(
                variable=variable,
                base_time=first.base_time,
                longitude_deg=first.longitude_deg,
                latitude_deg=first.latitude_deg,
                values=values,
            ),
        )

    def fetch_range(
        self, start_time: datetime, end_time: datetime, *, fetched_at: datetime | None = None
    ) -> KmaCollection:
        """Fetch an inclusive, 10-minute-aligned UTC range for DSR and cloud fraction."""

        if not self.api_key.strip():
            raise KmaGk2aError("kma_api_key_missing")
        start = _utc(start_time, "start_time")
        end = _utc(end_time, "end_time")
        fetched = _utc(fetched_at or datetime.now(UTC), "fetched_at")
        if start >= end:
            raise ValueError("start_time must be before end_time")
        if any(
            value.second or value.microsecond or value.minute % KMA_INTERVAL_MINUTES
            for value in (start, end)
        ):
            raise ValueError("start_time and end_time must align to 10-minute UTC boundaries")

        with ThreadPoolExecutor(max_workers=2) as executor:
            dsr_future = executor.submit(self._fetch_series, start, end, "DSR")
            cloud_future = executor.submit(self._fetch_series, start, end, "CA")
            dsr_payloads, dsr = dsr_future.result()
            cloud_payloads, cloud = cloud_future.result()
        dsr_payload = b"\n--KMA-CHUNK--\n".join(dsr_payloads)
        cloud_payload = b"\n--KMA-CHUNK--\n".join(cloud_payloads)
        snapshot = build_kma_snapshot(
            self.site, start, end, fetched, dsr_payload, cloud_payload, dsr, cloud
        )
        return KmaCollection(
            site=self.site,
            start_time=start,
            end_time=end,
            fetched_at=fetched,
            dsr_payloads=dsr_payloads,
            cloud_payloads=cloud_payloads,
            dsr=dsr,
            cloud=cloud,
            snapshot=snapshot,
        )

    def fetch_recent(
        self,
        now: datetime,
        *,
        lookback_minutes: int = 60,
        provider_lag_minutes: int = 20,
    ) -> KmaCollection:
        """Fetch the latest conservative range for a scheduled worker run."""

        current = _utc(now, "now")
        available = current - timedelta(minutes=provider_lag_minutes)
        end = available.replace(
            minute=(available.minute // KMA_INTERVAL_MINUTES) * KMA_INTERVAL_MINUTES,
            second=0,
            microsecond=0,
        )
        start = end - timedelta(minutes=lookback_minutes)
        return self.fetch_range(start, end, fetched_at=current)
