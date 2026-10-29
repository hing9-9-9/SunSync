"""Step C NetCDF adapter fixtures; no external KMA request is made here."""

from datetime import UTC, datetime
from io import BytesIO

import h5py
import numpy as np
import pytest
from app.nowcast.adapters import Gk2aDsrError
from app.nowcast.experimental.gk2a.swrad_dni_adapter import Gk2aSwradDniAdapter

from engine.fr01.models import Site

SITE = Site(37.5665, 126.9780, "Asia/Seoul")
TIME = datetime(2026, 10, 17, 3, tzinfo=UTC)


def fixture(*, dqf: int = 0, include_coordinates: bool = True) -> bytes:
    stream = BytesIO()
    with h5py.File(stream, "w") as file:
        dsr = file.create_dataset("DSR", data=np.array([[65535, 6000], [5000, 4000]], dtype=np.uint16))
        dsr.attrs["_FillValue"] = 65535
        dsr.attrs["scale_factor"] = 0.1
        file.create_dataset("DSR_DQF1", data=np.array([[0, dqf], [0, 0]], dtype=np.uint8))
        file.create_dataset("SW_DQF", data=np.zeros((2, 2), dtype=np.uint8))
        if include_coordinates:
            file.create_dataset("latitude", data=np.array([[37.0, 37.5665], [38.0, 38.0]]))
            file.create_dataset("longitude", data=np.array([[126.0, 126.9780], [127.0, 128.0]]))
    return stream.getvalue()


def test_swrad_netcdf_derives_dni_and_preserves_input_provenance():
    snapshot = Gk2aSwradDniAdapter(SITE, "secret")._normalize(fixture(), TIME)
    assert snapshot.points[0].ghi_W_m2 == 600.0
    assert snapshot.points[0].dni_W_m2 is not None
    assert snapshot.provenance["derived_from_ghi"] is True
    assert snapshot.provenance["dsr_dqf1"] == 0


def test_swrad_bad_dqf_is_stored_as_excluded_not_synthetic_dni():
    snapshot = Gk2aSwradDniAdapter(SITE, "secret")._normalize(fixture(dqf=2), TIME)
    assert snapshot.points[0].dni_W_m2 is None
    assert snapshot.provenance["derivation_reason"] == "dqf_invalid"


def test_swrad_requires_geographic_coordinates_for_site_mapping():
    with pytest.raises(Gk2aDsrError, match="gk2a_invalid_netcdf"):
        Gk2aSwradDniAdapter(SITE, "secret")._normalize(fixture(include_coordinates=False), TIME)
