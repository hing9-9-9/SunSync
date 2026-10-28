"""FR-01.4 Implementation Step C — DSR-to-DNI quality gates and DISC provenance."""

from datetime import UTC, datetime

from app.nowcast.derivation import derive_dni_from_gk2a_dsr

from engine.fr01.models import Site

SITE = Site(37.5665, 126.9780, "Asia/Seoul")
DAY = datetime(2026, 10, 17, 3, tzinfo=UTC)
NIGHT = datetime(2026, 10, 16, 18, tzinfo=UTC)


def test_disc_derives_finite_clipped_dni_only_from_verified_dqf():
    derived = derive_dni_from_gk2a_dsr(site=SITE, valid_time=DAY, dsr_W_m2=600, dqf_ok=True)
    assert derived.reason is None
    assert 0 <= derived.dni_W_m2 <= derived.clear_sky_dni_W_m2
    assert 0 <= derived.zenith_deg <= 70


def test_missing_dqf_is_an_exclusion_not_a_synthetic_quality_pass():
    derived = derive_dni_from_gk2a_dsr(site=SITE, valid_time=DAY, dsr_W_m2=600, dqf_ok=None)
    assert (derived.dni_W_m2, derived.reason) == (None, "dqf_unavailable")


def test_night_is_zero_but_missing_and_out_of_range_dsr_are_unknown():
    night = derive_dni_from_gk2a_dsr(site=SITE, valid_time=NIGHT, dsr_W_m2=0, dqf_ok=True)
    missing = derive_dni_from_gk2a_dsr(site=SITE, valid_time=DAY, dsr_W_m2=None, dqf_ok=True)
    invalid = derive_dni_from_gk2a_dsr(site=SITE, valid_time=DAY, dsr_W_m2=1501, dqf_ok=True)
    assert (night.dni_W_m2, night.reason) == (0.0, "night")
    assert (missing.dni_W_m2, missing.reason) == (None, "dsr_missing")
    assert (invalid.dni_W_m2, invalid.reason) == (None, "dsr_out_of_range")
