"""Source adapter boundary plus deterministic and Open-Meteo adapters."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from engine.fr01.models import Site

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
GK2A_DSR_URL = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph_sun_sat_txt"
GK2A_ERROR = re.compile(
    r"<\s*error\b|error|fail|denied|invalid|unauthori|option\s+required|"
    r"\[옵션\s*필요\]|인증|오류|자료\s*없",
    re.IGNORECASE,
)


class OpenMeteoSnapshotError(ValueError):
    """A provider failure which must not create an invalid durable snapshot."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class Gk2aDsrError(ValueError):
    """A classified GK2A provider failure; never persist it as a snapshot."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class RawPoint:
    valid_time: datetime
    latitude_deg: float
    longitude_deg: float
    temporal_meaning: str = "instant"
    grid_resolution_km: float | None = None
    dni_W_m2: float | None = None
    ghi_W_m2: float | None = None
    direct_horizontal_W_m2: float | None = None
    cloud_cover_percent: float | None = None
    interval_start: datetime | None = None
    interval_end: datetime | None = None


@dataclass(frozen=True)
class RawSnapshot:
    provider: str
    product: str
    payload: bytes
    points: tuple[RawPoint, ...]
    model: str | None = None
    observed_at: datetime | None = None
    issued_at: datetime | None = None
    quality_status: str = "valid"
    provenance: dict[str, object] = field(default_factory=dict)


class SnapshotAdapter(Protocol):
    def fetch(self, now: datetime) -> RawSnapshot: ...


@dataclass(frozen=True)
class OpenMeteoDniAdapter:
    """Fetch one site's hourly DNI forecast as immutable normalized points.

    Open-Meteo's hourly values are means for the preceding hour.  Each point
    therefore uses its returned timestamp as ``interval_end`` and preserves
    the half-open interval ``[end - 1 hour, end)``.
    """

    site: Site
    timeout_seconds: float = 5.0

    def fetch(self, now: datetime) -> RawSnapshot:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be UTC-aware")
        query = urlencode(
            {
                "latitude": self.site.latitude_deg,
                "longitude": self.site.longitude_deg,
                "hourly": "direct_normal_irradiance,cloud_cover",
                "timeformat": "unixtime",
            }
        )
        try:
            with urlopen(f"{OPEN_METEO_URL}?{query}", timeout=self.timeout_seconds) as response:  # nosec B310: fixed HTTPS URL
                if response.status != 200:
                    raise OpenMeteoSnapshotError("open_meteo_transport_error")
                payload = response.read()
        except TimeoutError as error:
            raise OpenMeteoSnapshotError("open_meteo_timeout") from error
        except (HTTPError, URLError, OSError) as error:
            raise OpenMeteoSnapshotError("open_meteo_transport_error") from error
        return self._normalize(payload, now.astimezone(UTC))

    def _normalize(self, payload: bytes, fetched_at: datetime) -> RawSnapshot:
        import json

        try:
            document = json.loads(payload.decode("utf-8"))
            hourly, units = document["hourly"], document["hourly_units"]
            if units["direct_normal_irradiance"] != "W/m²":
                raise ValueError
            times = hourly["time"]
            dnis = hourly["direct_normal_irradiance"]
            clouds = hourly["cloud_cover"]
            if not all(isinstance(values, list) for values in (times, dnis, clouds)):
                raise ValueError
            if not times or len(times) != len(dnis) or len(times) != len(clouds):
                raise ValueError
            points: list[RawPoint] = []
            previous: datetime | None = None
            for epoch, dni, cloud in zip(times, dnis, clouds, strict=True):
                if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
                    raise TypeError
                end = datetime.fromtimestamp(epoch, UTC)
                if previous is not None and end <= previous:
                    raise ValueError
                previous = end
                if dni is not None and (
                    isinstance(dni, bool)
                    or not isinstance(dni, (int, float))
                    or not isfinite(dni)
                    or dni < 0
                ):
                    raise ValueError
                if (
                    isinstance(cloud, bool)
                    or not isinstance(cloud, (int, float))
                    or not isfinite(cloud)
                    or not 0 <= cloud <= 100
                ):
                    raise ValueError
                points.append(
                    RawPoint(
                        valid_time=end,
                        latitude_deg=self.site.latitude_deg,
                        longitude_deg=self.site.longitude_deg,
                        temporal_meaning="mean",
                        dni_W_m2=float(dni) if dni is not None else None,
                        cloud_cover_percent=float(cloud),
                        interval_start=end - timedelta(hours=1),
                        interval_end=end,
                    )
                )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, OSError, OverflowError) as error:
            raise OpenMeteoSnapshotError("open_meteo_invalid_response") from error
        return RawSnapshot(
            provider="open_meteo",
            product="hourly_direct_normal_irradiance",
            model=None,
            payload=payload,
            points=tuple(points),
            # This is a forecast product, not an observation.  ``issued_at``
            # is deliberately absent until the provider exposes a reliable one.
            observed_at=None,
            issued_at=None,
        )


def _table_tokens(line: str) -> list[str]:
    return [item for item in re.split(r"[\s,|]+", line.strip().lstrip("#").strip()) if item]


@dataclass(frozen=True)
class Gk2aDsrAdapter:
    """Authenticated GK2A point DSR adapter.

    The point endpoint is known to return a nearest-grid value, but its table
    does not currently provide the representative coordinate or grid identity.
    That limitation is retained in snapshot provenance rather than pretending
    the request coordinate is an exact observation location.
    """

    site: Site
    api_key: str
    timeout_seconds: float = 30.0

    def fetch(self, now: datetime) -> RawSnapshot:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be UTC-aware")
        if not self.api_key or not self.api_key.strip():
            raise Gk2aDsrError("gk2a_auth_missing")
        end = now.astimezone(UTC).replace(second=0, microsecond=0)
        # The endpoint caps a response at 24 timestamp columns.  A 3h50m
        # inclusive window yields exactly 24 ten-minute values.
        start = end - timedelta(hours=3, minutes=50)
        query = urlencode(
            {
                "varn": "DSR",
                "tm1": start.strftime("%Y%m%d%H%M"),
                "tm2": end.strftime("%Y%m%d%H%M"),
                "int": "10",
                "lat": f"{self.site.latitude_deg:.6f}",
                "lon": f"{self.site.longitude_deg:.6f}",
                "authKey": self.api_key,
            }
        )
        request = Request(
            f"{GK2A_DSR_URL}?{query}",
            headers={"User-Agent": "SunSync-GK2A-DSR-worker/1.0"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise Gk2aDsrError("gk2a_http_error")
                payload = response.read()
        except TimeoutError as error:
            raise Gk2aDsrError("gk2a_timeout") from error
        except HTTPError as error:
            raise Gk2aDsrError("gk2a_auth_error" if error.code in {401, 403} else "gk2a_http_error") from error
        except (URLError, OSError) as error:
            raise Gk2aDsrError("gk2a_transport_error") from error
        return self._normalize(payload)

    def _normalize(self, payload: bytes) -> RawSnapshot:
        try:
            body = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise Gk2aDsrError("gk2a_invalid_response") from error
        if GK2A_ERROR.search(body):
            if re.search(r"denied|unauthori|인증", body, re.IGNORECASE):
                reason = "gk2a_auth_error"
            elif re.search(r"자료\s*없|no\s*data", body, re.IGNORECASE):
                reason = "gk2a_no_data"
            else:
                reason = "gk2a_provider_error"
            raise Gk2aDsrError(reason)
        columns: list[str] | None = None
        for line in body.splitlines():
            candidate = [item.upper() for item in _table_tokens(line)]
            if "VAR" in candidate and ("TMFC" in candidate or "TIME" in candidate):
                columns = candidate
                break
        if columns is None:
            raise Gk2aDsrError("gk2a_invalid_response")
        try:
            variable_index = columns.index("VAR")
            longitude_index = columns.index("LON")
            latitude_index = columns.index("LAT")
            time_columns = [
                (index, datetime.strptime(column, "%Y%m%d%H%M").replace(tzinfo=UTC))
                for index, column in enumerate(columns)
                if re.fullmatch(r"20\d{10}", column)
            ]
        except ValueError as error:
            raise Gk2aDsrError("gk2a_invalid_response") from error
        if not time_columns:
            raise Gk2aDsrError("gk2a_no_data")
        if any(later <= earlier for (_, earlier), (_, later) in pairwise(time_columns)):
            raise Gk2aDsrError("gk2a_invalid_response")
        points: list[RawPoint] = []
        representative_location: tuple[float, float] | None = None
        for line in body.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            fields = _table_tokens(line)
            if len(fields) <= max(variable_index, longitude_index, latitude_index) or fields[variable_index].upper() != "DSR":
                continue
            try:
                longitude, latitude = float(fields[longitude_index]), float(fields[latitude_index])
            except ValueError as error:
                raise Gk2aDsrError("gk2a_invalid_response") from error
            if not isfinite(latitude) or not isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise Gk2aDsrError("gk2a_invalid_response")
            if representative_location is not None and representative_location != (latitude, longitude):
                raise Gk2aDsrError("gk2a_invalid_response")
            representative_location = (latitude, longitude)
            for value_index, observed_at in time_columns:
                if value_index >= len(fields):
                    raise Gk2aDsrError("gk2a_invalid_response")
                dsr = _parse_dsr(fields[value_index])
                points.append(
                    RawPoint(
                        valid_time=observed_at,
                        latitude_deg=latitude,
                        longitude_deg=longitude,
                        temporal_meaning="instant",
                        ghi_W_m2=dsr,
                    )
                )
        if not points:
            raise Gk2aDsrError("gk2a_no_data")
        assert representative_location is not None
        latitude, longitude = representative_location
        return RawSnapshot(
            provider="kma_gk2a",
            product="gk2a_dsr_point",
            model="GK2A",
            payload=payload,
            points=tuple(points),
            observed_at=max(point.valid_time for point in points),
            provenance={
                "unit": "W/m2",
                "variable": "DSR",
                "temporal_meaning": "instant_assumed_from_timestamp_columns",
                "spatial_representation": "representative_coordinate_from_response",
                "representative_latitude_deg": latitude,
                "representative_longitude_deg": longitude,
                "request_to_representative_distance_km": _great_circle_distance_km(
                    self.site.latitude_deg, self.site.longitude_deg, latitude, longitude
                ),
                "grid_resolution_km": None,
            },
        )


def _parse_dsr(value: str) -> float | None:
    if value.upper() in {"", "-", "NA", "N/A", "NULL", "MISSING"}:
        return None
    try:
        result = float(value)
    except ValueError as error:
        raise Gk2aDsrError("gk2a_invalid_response") from error
    if not isfinite(result) or result < 0:
        raise Gk2aDsrError("gk2a_invalid_response")
    return result


def _great_circle_distance_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    """Great-circle distance for the request and API-representative locations."""

    latitude_delta = radians(latitude_b - latitude_a)
    longitude_delta = radians(longitude_b - longitude_a)
    haversine = sin(latitude_delta / 2) ** 2 + cos(radians(latitude_a)) * cos(radians(latitude_b)) * sin(longitude_delta / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(haversine))


@dataclass
class MockSnapshotAdapter:
    """Returns supplied fixture snapshots without network or clock side effects."""

    snapshots: list[RawSnapshot] = field(default_factory=list)
    calls: list[datetime] = field(default_factory=list)

    def fetch(self, now: datetime) -> RawSnapshot:
        self.calls.append(now)
        if not self.snapshots:
            raise RuntimeError("mock adapter has no snapshot")
        return self.snapshots.pop(0)
