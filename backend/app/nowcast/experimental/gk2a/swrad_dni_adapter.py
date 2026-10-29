"""Step C adapter: turn one verified GK2A SWRAD NetCDF pixel into a DNI snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from math import isfinite
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import h5py
import numpy as np

from app.nowcast.adapters import Gk2aDsrError, RawPoint, RawSnapshot
from app.nowcast.experimental.gk2a.derivation import derive_dni_from_gk2a_dsr
from engine.fr01.models import Site

GK2A_SWRAD_URL = "https://apihub.kma.go.kr/api/typ01/url/sat_file_down2.php"


@dataclass(frozen=True)
class Gk2aSwradDniAdapter:
    """Download one SWRAD file and derive a site-nearest, DQF-gated DNI point.

    A coordinate grid is deliberately required in the payload.  Files without
    one are rejected instead of treating an array index as a geographic point.
    The production KO file currently needs its companion lat/lon file before
    this adapter can be scheduled; this strict failure is the Step-E gate.
    """

    site: Site
    api_key: str
    timeout_seconds: float = 60.0

    def fetch(self, now: datetime) -> RawSnapshot:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be UTC-aware")
        if not self.api_key or not self.api_key.strip():
            raise Gk2aDsrError("gk2a_auth_missing")
        observed = now.astimezone(UTC).replace(second=0, microsecond=0)
        query = urlencode({"typ": "bin", "lvl": "l2", "dat": "swrad", "are": "ko",
                           "tm": observed.strftime("%Y%m%d%H%M"), "authKey": self.api_key})
        request = Request(f"{GK2A_SWRAD_URL}?{query}", headers={"User-Agent": "SunSync-GK2A-SWRAD-worker/1.0"})
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
        return self._normalize(payload, observed)

    def _normalize(self, payload: bytes, observed_at: datetime) -> RawSnapshot:
        try:
            with h5py.File(BytesIO(payload), "r") as dataset:
                dsr_source = _dataset(dataset, "DSR")
                dqf1 = _dataset(dataset, "DSR_DQF1")
                sw_dqf = _dataset(dataset, "SW_DQF")
                latitude, longitude = _dataset(dataset, "latitude"), _dataset(dataset, "longitude")
                raw_dsr, raw_dqf1, raw_sw = np.asarray(dsr_source), np.asarray(dqf1), np.asarray(sw_dqf)
                latitudes, longitudes = np.asarray(latitude), np.asarray(longitude)
                if not (raw_dsr.shape == raw_dqf1.shape == raw_sw.shape == latitudes.shape == longitudes.shape):
                    raise ValueError("grid shapes differ")
                if raw_dsr.ndim != 2:
                    raise ValueError("DSR grid is not two-dimensional")
                distance = (latitudes - self.site.latitude_deg) ** 2 + (longitudes - self.site.longitude_deg) ** 2
                if not np.isfinite(distance).any():
                    raise ValueError("coordinate grid has no finite point")
                row, col = np.unravel_index(np.nanargmin(distance), distance.shape)
                fill = dsr_source.attrs.get("_FillValue")
                raw_value = raw_dsr[row, col]
                dsr = None if fill is not None and raw_value == fill else float(raw_value) * float(dsr_source.attrs.get("scale_factor", 1.0))
                if dsr is not None and not isfinite(dsr):
                    raise ValueError("non-finite DSR")
                # Product DQFs are categorical: zero is the only verified-good state.
                dqf_ok = bool(raw_dqf1[row, col] == 0 and raw_sw[row, col] == 0)
                representative_lat, representative_lon = float(latitudes[row, col]), float(longitudes[row, col])
        except (OSError, KeyError, TypeError, ValueError, IndexError) as error:
            raise Gk2aDsrError("gk2a_invalid_netcdf") from error
        derived = derive_dni_from_gk2a_dsr(site=self.site, valid_time=observed_at, dsr_W_m2=dsr, dqf_ok=dqf_ok)
        point = RawPoint(valid_time=observed_at, latitude_deg=representative_lat, longitude_deg=representative_lon,
                         temporal_meaning="instant", ghi_W_m2=dsr, dni_W_m2=derived.dni_W_m2)
        return RawSnapshot(provider="kma_gk2a", product="gk2a_swrad_dni", model="GK2A", payload=payload,
                           points=(point,), observed_at=observed_at,
                           provenance={"derived_from_ghi": True, "algorithm": derived.algorithm,
                                       "derivation_reason": derived.reason, "clear_sky_dni_W_m2": derived.clear_sky_dni_W_m2,
                                       "zenith_deg": derived.zenith_deg, "dsr_dqf1": int(raw_dqf1[row, col]),
                                       "sw_dqf": int(raw_sw[row, col]), "grid_row": int(row), "grid_column": int(col)})


def _dataset(file: h5py.File, name: str):  # type: ignore[no-untyped-def]
    if name in file:
        return file[name]
    matches: list[h5py.Dataset] = []
    file.visititems(lambda _key, item: matches.append(item) if isinstance(item, h5py.Dataset) and item.name.rsplit("/", 1)[-1] == name else None)
    if len(matches) != 1:
        raise KeyError(name)
    return matches[0]
