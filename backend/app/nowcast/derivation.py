"""FR-01.4 Implementation Step C — conservative conversion of satellite DSR (GHI-like) to DNI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite

import pandas as pd
from pvlib import irradiance
from pvlib.location import Location

from engine.fr01.models import Site
from engine.fr01.solar import calculate_solar_positions

ALGORITHM = "pvlib_disc_v1"
MAX_GHI_W_M2 = 1500.0
MAX_ZENITH_DEG = 70.0


@dataclass(frozen=True)
class DniDerivation:
    """A derivation or a reproducible exclusion, never an imputed value."""

    dni_W_m2: float | None
    clear_sky_dni_W_m2: float | None
    zenith_deg: float | None
    reason: str | None
    algorithm: str = ALGORITHM


def derive_dni_from_gk2a_dsr(
    *,
    site: Site,
    valid_time: datetime,
    dsr_W_m2: float | None,
    dqf_ok: bool | None,
) -> DniDerivation:
    """Estimate DNI with DISC only when the required input quality is known.

    GK2A DSR is treated as a GHI candidate, not as DNI.  A missing DQF does
    not silently become good quality: current point API responses therefore
    produce an exclusion until a verified quality signal is available.
    """

    if valid_time.tzinfo is None or valid_time.utcoffset() is None:
        raise ValueError("valid_time must be UTC-aware")
    timestamp = valid_time.astimezone(UTC)
    if dqf_ok is not True:
        return DniDerivation(None, None, None, "dqf_unavailable" if dqf_ok is None else "dqf_invalid")
    if dsr_W_m2 is None:
        return DniDerivation(None, None, None, "dsr_missing")
    if isinstance(dsr_W_m2, bool) or not isinstance(dsr_W_m2, (int, float)) or not isfinite(dsr_W_m2):
        return DniDerivation(None, None, None, "dsr_invalid")
    if not 0 <= dsr_W_m2 <= MAX_GHI_W_M2:
        return DniDerivation(None, None, None, "dsr_out_of_range")

    solar_position = calculate_solar_positions(site, (timestamp,))[0]
    zenith = 90.0 - solar_position.elevation_deg
    if solar_position.elevation_deg <= 0:
        return DniDerivation(0.0, 0.0, zenith, "night")
    if zenith > MAX_ZENITH_DEG:
        return DniDerivation(None, None, zenith, "solar_zenith_exceeds_limit")

    index = pd.DatetimeIndex([timestamp])
    clear_sky = float(Location(site.latitude_deg, site.longitude_deg, tz="UTC").get_clearsky(index)["dni"].iloc[0])
    if not isfinite(clear_sky) or clear_sky < 0:
        return DniDerivation(None, None, zenith, "clear_sky_invalid")
    disc_dni = float(irradiance.disc(float(dsr_W_m2), zenith, index)["dni"].iloc[0])
    if not isfinite(disc_dni) or disc_dni < 0:
        return DniDerivation(None, clear_sky, zenith, "disc_invalid")
    # Satellite GHI inversion can overshoot; preserve the applied physical clip.
    return DniDerivation(min(disc_dni, clear_sky), clear_sky, zenith, None)
