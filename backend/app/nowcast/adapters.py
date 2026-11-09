"""Provider adapter boundary for production Open-Meteo DNI snapshots."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from engine.fr01.models import Site

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_MODEL = "ecmwf_ifs025"


class OpenMeteoSnapshotError(ValueError):
    """A provider failure which must not create an invalid durable snapshot."""

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
    """Fetch one site's hourly DNI forecast as immutable normalized points."""

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
                "models": OPEN_METEO_MODEL,
                "timeformat": "unixtime",
            }
        )
        try:
            with urlopen(f"{OPEN_METEO_URL}?{query}", timeout=self.timeout_seconds) as response:  # nosec B310
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
            times, dnis, clouds = (
                hourly["time"],
                hourly["direct_normal_irradiance"],
                hourly["cloud_cover"],
            )
            if not all(isinstance(values, list) for values in (times, dnis, clouds)) or not times:
                raise ValueError
            if len(times) != len(dnis) or len(times) != len(clouds):
                raise ValueError
            grid_latitude, grid_longitude, grid_elevation = (
                document.get("latitude"),
                document.get("longitude"),
                document.get("elevation"),
            )
            has_grid_metadata = grid_latitude is not None and grid_longitude is not None
            if not has_grid_metadata:
                grid_latitude, grid_longitude = self.site.latitude_deg, self.site.longitude_deg
            if (
                isinstance(grid_latitude, bool)
                or not isinstance(grid_latitude, (int, float))
                or isinstance(grid_longitude, bool)
                or not isinstance(grid_longitude, (int, float))
                or not isfinite(grid_latitude)
                or not isfinite(grid_longitude)
                or not -90 <= grid_latitude <= 90
                or not -180 <= grid_longitude <= 180
            ):
                raise ValueError
            if grid_elevation is not None and (
                isinstance(grid_elevation, bool)
                or not isinstance(grid_elevation, (int, float))
                or not isfinite(grid_elevation)
            ):
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
                        latitude_deg=float(grid_latitude),
                        longitude_deg=float(grid_longitude),
                        temporal_meaning="mean",
                        dni_W_m2=float(dni) if dni is not None else None,
                        cloud_cover_percent=float(cloud),
                        interval_start=end - timedelta(hours=1),
                        interval_end=end,
                    )
                )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            OSError,
            OverflowError,
        ) as error:
            raise OpenMeteoSnapshotError("open_meteo_invalid_response") from error
        return RawSnapshot(
            provider="open_meteo",
            product="hourly_direct_normal_irradiance",
            payload=payload,
            points=tuple(points),
            model=OPEN_METEO_MODEL,
            provenance={
                "spatial_provenance": "provider_grid"
                if has_grid_metadata
                else "legacy_request_coordinate",
                "request_latitude_deg": self.site.latitude_deg,
                "request_longitude_deg": self.site.longitude_deg,
                "grid_latitude_deg": float(grid_latitude),
                "grid_longitude_deg": float(grid_longitude),
                "grid_elevation_m": float(grid_elevation) if grid_elevation is not None else None,
                "model_selection": "explicit",
                "resolved_model": OPEN_METEO_MODEL,
            },
        )


@dataclass
class MockSnapshotAdapter:
    snapshots: list[RawSnapshot] = field(default_factory=list)
    calls: list[datetime] = field(default_factory=list)

    def fetch(self, now: datetime) -> RawSnapshot:
        self.calls.append(now)
        if not self.snapshots:
            raise RuntimeError("mock adapter has no snapshot")
        return self.snapshots.pop(0)
