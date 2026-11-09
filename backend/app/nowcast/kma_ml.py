"""FR-01.4 KMA AI × Open-Meteo Hybrid Quantile Nowcasting Module.

This module implements a 2-stage hybrid nowcasting engine that converts
KMA high-frequency surface observations (Downward Shortwave Radiation, DSR / GHI)
and GK2A satellite cloud patch data into monotonic Direct Normal Irradiance (DNI)
quantiles (p10, p50, p90), blended smoothly with Open-Meteo synoptic forecasts.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
from pvlib.irradiance import erbs
from scipy.optimize import minimize
from sqlalchemy import Engine, text

from app.nowcast.repository import utc_z
from app.schemas.nowcast_ai import (
    AiNowcastRequest,
    CloudPatchSummary,
    ComparisonMetrics,
    NowcastComparisonResponse,
    QuantileNowcastPoint,
)
from engine.fr01.models import Site

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "kma_openmeteo_quantile_nowcast_v1"
SOLAR_CONSTANT_W_M2 = 1361.0


def calculate_solar_geometry(site: Site, timestamps: list[datetime]) -> list[dict[str, float]]:
    """Compute solar elevation, zenith, and clear-sky GHI/DNI/DHI via pvlib with analytical fallback."""
    results: list[dict[str, float]] = []
    try:
        import pandas as pd
        from pvlib.location import Location

        loc = Location(site.latitude_deg, site.longitude_deg, tz="UTC")
        dt_idx = pd.DatetimeIndex([t.astimezone(UTC) for t in timestamps])
        cs = loc.get_clearsky(dt_idx)
        sp = loc.get_solarposition(dt_idx)

        for i in range(len(timestamps)):
            elevation = float(sp["apparent_elevation"].iloc[i])
            zenith = float(sp["apparent_zenith"].iloc[i])
            dni = float(cs["dni"].iloc[i])
            ghi = float(cs["ghi"].iloc[i])
            dhi = float(cs["dhi"].iloc[i])
            results.append(
                {
                    "solar_elevation_deg": max(0.0, elevation) if elevation > 0 else elevation,
                    "zenith_deg": max(0.0, min(180.0, zenith)),
                    "clear_sky_dni": max(0.0, dni) if elevation > 0 else 0.0,
                    "clear_sky_ghi": max(0.0, ghi) if elevation > 0 else 0.0,
                    "clear_sky_dhi": max(0.0, dhi) if elevation > 0 else 0.0,
                }
            )
        return results
    except (ImportError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.debug("Falling back to analytical solar geometry: %s", exc)
        # Analytical fast fallback
        lat_rad = math.radians(site.latitude_deg)
        for t in timestamps:
            t_utc = t.astimezone(UTC)
            day_of_year = t_utc.timetuple().tm_yday
            hour_dec = t_utc.hour + t_utc.minute / 60.0 + t_utc.second / 3600.0

            # Solar declination
            declination = 0.40928 * math.sin(2 * math.pi * (284 + day_of_year) / 365.25)
            # Solar hour angle
            lstm = 15.0 * round(site.longitude_deg / 15.0)
            b = 2 * math.pi * (day_of_year - 81) / 364.0
            eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
            tc = 4.0 * (site.longitude_deg - lstm) + eot
            lst = hour_dec + tc / 60.0
            hra = math.radians(15.0 * (lst - 12.0))

            sin_elev = math.sin(lat_rad) * math.sin(declination) + math.cos(lat_rad) * math.cos(
                declination
            ) * math.cos(hra)
            elev_deg = math.degrees(math.asin(max(-1.0, min(1.0, sin_elev))))
            zenith_deg = max(0.0, 90.0 - elev_deg)

            if elev_deg <= 0.0:
                results.append(
                    {
                        "solar_elevation_deg": elev_deg,
                        "zenith_deg": zenith_deg,
                        "clear_sky_dni": 0.0,
                        "clear_sky_ghi": 0.0,
                        "clear_sky_dhi": 0.0,
                    }
                )
            else:
                air_mass = 1.0 / max(0.05, math.sin(math.radians(elev_deg)))
                cs_dni = SOLAR_CONSTANT_W_M2 * (0.7 ** (air_mass**0.678))
                cs_ghi = cs_dni * math.sin(math.radians(elev_deg)) + 0.1 * SOLAR_CONSTANT_W_M2
                results.append(
                    {
                        "solar_elevation_deg": elev_deg,
                        "zenith_deg": zenith_deg,
                        "clear_sky_dni": cs_dni,
                        "clear_sky_ghi": cs_ghi,
                        "clear_sky_dhi": max(
                            0.0, cs_ghi - cs_dni * math.sin(math.radians(elev_deg))
                        ),
                    }
                )
        return results


def dsr_to_dni_physics(
    dsr_W_m2: float,
    clear_sky_dni: float,
    solar_zenith_deg: float,
    timestamp: datetime,
) -> tuple[float, float, float]:
    """Convert DSR/GHI to DNI with pvlib's official Erbs implementation.

    Returns: (kt, kd, decomposed_dni)
    """
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    if solar_zenith_deg >= 89.5 or clear_sky_dni <= 1.0 or dsr_W_m2 <= 0.0:
        return 0.0, 1.0, 0.0

    decomposition = erbs(dsr_W_m2, solar_zenith_deg, timestamp)
    kt = float(np.asarray(decomposition["kt"]).item())
    dhi = float(np.asarray(decomposition["dhi"]).item())
    dni_decomp = float(np.asarray(decomposition["dni"]).item())
    kd = float(np.clip(dhi / dsr_W_m2, 0.0, 1.0))
    dni_bounded = float(np.clip(dni_decomp, 0.0, clear_sky_dni * 1.05))
    return kt, kd, dni_bounded


class QuantileGradientEnsemble:
    """Deterministic, pre-calibrated quantile ensemble with pinball loss .fit(X, y) support."""

    def __init__(self) -> None:
        # Default pre-calibrated basis weights for [p10, p50, p90]
        # Features X: [1.0 (intercept), k_direct_prior, k_om_prior, cloud_transmittance, advection_attenuation]
        self.weights: dict[float, np.ndarray] = {
            0.10: np.array([-0.05, 0.70, 0.15, 0.10, -0.20], dtype=np.float64),
            0.50: np.array([0.00, 0.75, 0.20, 0.05, -0.15], dtype=np.float64),
            0.90: np.array([0.08, 0.80, 0.15, 0.05, -0.10], dtype=np.float64),
        }
        self.fitted = False

    def fit(self, x: np.ndarray, y: np.ndarray) -> None:
        """Fit quantile weights on empirical dataset (X: N x 5, y: N, values in [0.0, 1.1])."""
        if x.shape[0] < 5 or x.shape[1] != 5:
            return

        def pinball_loss(w: np.ndarray, alpha: float) -> float:
            pred = x @ w
            diff = y - pred
            return float(np.mean(np.maximum(alpha * diff, (alpha - 1.0) * diff)))

        for alpha in [0.10, 0.50, 0.90]:
            w0 = self.weights[alpha].copy()
            res = minimize(
                pinball_loss,
                w0,
                args=(alpha,),
                method="L-BFGS-B",
                bounds=[(-0.5, 0.5), (0.0, 1.5), (0.0, 1.5), (-0.5, 0.5), (-0.5, 0.5)],
            )
            if res.success:
                self.weights[alpha] = res.x
        self.fitted = True

    def predict_factors(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Predict (k_p10, k_p50, k_p90) transmission factors, strictly monotonic."""
        p10 = x @ self.weights[0.10]
        p50 = x @ self.weights[0.50]
        p90 = x @ self.weights[0.90]

        # Enforce non-negativity and bounds
        p10 = np.clip(p10, 0.0, 1.05)
        p50 = np.clip(p50, 0.0, 1.05)
        p90 = np.clip(p90, 0.0, 1.05)

        # Enforce strict monotonicity p10 <= p50 <= p90
        p50 = np.maximum(p10, p50)
        p90 = np.maximum(p50, p90)

        return p10, p50, p90


class KmaQuantileNowcaster:
    """2-Stage Hybrid Quantile Nowcaster combining KMA observations, GK2A advection, and Open-Meteo."""

    def __init__(
        self, engine: Engine | None = None, model_version: str = ALGORITHM_VERSION
    ) -> None:
        self.engine = engine
        self.model_version = model_version
        self.ensemble = QuantileGradientEnsemble()

    def fit(self, x: np.ndarray, y: np.ndarray) -> None:
        """Expose training interface for synthetic or historical calibration."""
        self.ensemble.fit(x, y)

    def _query_open_meteo_points(
        self, site: Site, start_dt: datetime, end_dt: datetime
    ) -> dict[datetime, float]:
        """Query fresh Open-Meteo hourly means from durable database tables."""
        if not self.engine:
            return {}

        results: dict[datetime, float] = {}
        try:
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text("""
                    SELECT p.valid_time, p.interval_start, p.interval_end, p.dni_W_m2
                    FROM weather_snapshot s
                    JOIN weather_point p ON p.snapshot_id = s.snapshot_id
                    WHERE s.provider = 'open_meteo' AND p.dni_W_m2 IS NOT NULL
                      AND s.quality_status = 'valid'
                      AND p.interval_start <= :end_dt AND :start_dt < p.interval_end
                    ORDER BY s.fetched_at DESC
                    """),
                        {"start_dt": utc_z(start_dt), "end_dt": utc_z(end_dt)},
                    )
                    .mappings()
                    .all()
                )

                for row in rows:
                    try:
                        vtime = datetime.fromisoformat(row["valid_time"]).astimezone(UTC)
                        istart = datetime.fromisoformat(row["interval_start"]).astimezone(UTC)
                        iend = datetime.fromisoformat(row["interval_end"]).astimezone(UTC)
                        val = float(row["dni_W_m2"])
                        if vtime not in results:
                            results[vtime] = val
                        # Also index interval bounds
                        results[istart] = val
                        results[iend] = val
                    except (ValueError, TypeError, KeyError) as exc:
                        logger.debug("Skipping unparseable row: %s", exc)
                        continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("Open-Meteo database query failed: %s", exc)
            return {}
        return results

    def _get_interpolated_open_meteo(
        self,
        dt: datetime,
        stored_om: dict[datetime, float],
        clear_sky_dni: float,
        solar_elevation: float,
    ) -> float | None:
        """Find matching or interpolated Open-Meteo DNI value."""
        if solar_elevation <= 0.0 or clear_sky_dni <= 0.0:
            return 0.0

        if not stored_om:
            return None

        # Direct exact match
        if dt in stored_om:
            return float(np.clip(stored_om[dt], 0.0, clear_sky_dni * 1.05))

        # Nearest neighbor within 1 hour
        closest_dt = min(stored_om.keys(), key=lambda t: abs((t - dt).total_seconds()))
        diff_sec = abs((closest_dt - dt).total_seconds())
        if diff_sec <= 3600:
            return float(np.clip(stored_om[closest_dt], 0.0, clear_sky_dni * 1.05))

        return None

    def predict(
        self,
        site: Site,
        request: AiNowcastRequest,
        scene_id: str = "transient",
        now: datetime | None = None,
    ) -> NowcastComparisonResponse:
        """Run the 2-Stage Hybrid Quantile Nowcast."""
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        start_dt = datetime.fromisoformat(request.start_time).astimezone(UTC)
        end_dt = start_dt + timedelta(minutes=request.duration_minutes)

        # Build discrete timeline
        timestamps: list[datetime] = []
        cur = start_dt
        while cur <= end_dt:
            timestamps.append(cur)
            cur += timedelta(seconds=request.interval_seconds)

        solar_geoms = calculate_solar_geometry(site, timestamps)
        stored_om = self._query_open_meteo_points(site, start_dt, end_dt)

        # Process KMA observations
        kma_obs = sorted(request.kma_observations, key=lambda o: o.timestamp)
        has_kma = len(kma_obs) > 0

        # Calculate cloud volatility index from observations
        if has_kma and len(kma_obs) >= 2:
            cloud_vals = [o.cloud_cover_pct for o in kma_obs]
            std_cloud = float(np.std(cloud_vals)) / 100.0
            volatility_index = float(np.clip(std_cloud * 1.5, 0.05, 0.95))
        elif has_kma:
            volatility_index = 0.20
        else:
            volatility_index = 0.15

        # Process cloud patch advection
        cloud_patch = request.cloud_patch or CloudPatchSummary()
        wind_speed_m_s = math.sqrt(cloud_patch.motion_u_m_s**2 + cloud_patch.motion_v_m_s**2)
        wind_speed_km_h = wind_speed_m_s * 3.6
        advection_scale = cloud_patch.upwind_cloud_gradient * (
            wind_speed_km_h / max(1.0, cloud_patch.patch_size_km)
        )

        # Recent observation reference for extrapolation
        latest_obs = kma_obs[-1] if has_kma else None
        latest_obs_dt = (
            datetime.fromisoformat(latest_obs.timestamp).astimezone(UTC) if latest_obs else start_dt
        )
        latest_obs_geometry = (
            calculate_solar_geometry(site, [latest_obs_dt])[0] if latest_obs is not None else None
        )

        quantile_points: list[QuantileNowcastPoint] = []
        recorded_kma_p50: list[float] = []
        recorded_om: list[float] = []

        for i, dt in enumerate(timestamps):
            geom = solar_geoms[i]
            el = geom["solar_elevation_deg"]
            cs_dni = geom["clear_sky_dni"]

            # Night condition
            if el <= 0.0 or cs_dni <= 0.0:
                p = QuantileNowcastPoint(
                    timestamp=utc_z(dt),
                    solar_elevation_deg=round(el, 2),
                    clear_sky_dni_W_m2=0.0,
                    open_meteo_dni_W_m2=0.0,
                    kma_ai_dni_W_m2=0.0,
                    dni_p10_W_m2=0.0,
                    dni_p50_W_m2=0.0,
                    dni_p90_W_m2=0.0,
                    clear_sky_index_p50=0.0,
                    uncertainty_spread_W_m2=0.0,
                    selected_dni_W_m2=0.0,
                    selected_source="pvlib_clear_sky",
                    fallback_used=False,
                )
                quantile_points.append(p)
                continue

            # Lead time in hours from latest observation
            lead_time_hrs = max(0.0, (dt - latest_obs_dt).total_seconds() / 3600.0)
            obs_weight = math.exp(-lead_time_hrs / 1.5)  # KMA observation weight decays over 1.5h

            # Open-Meteo prior
            om_dni = self._get_interpolated_open_meteo(dt, stored_om, cs_dni, el)
            if om_dni is None:
                # Synthetic realistic Open-Meteo profile when database is empty
                cloud_baseline = (
                    latest_obs.cloud_cover_pct / 100.0
                    if latest_obs
                    else cloud_patch.mean_cloud_fraction
                )
                om_dni = float(np.clip(cs_dni * (1.0 - 0.85 * cloud_baseline), 0.0, cs_dni))
                om_fallback = True
            else:
                om_fallback = False

            k_om = om_dni / cs_dni if cs_dni > 0 else 0.0

            # KMA Direct decomposition prior
            if latest_obs is not None:
                assert latest_obs_geometry is not None
                obs_clear_sky_dni = latest_obs_geometry["clear_sky_dni"]
                _kt, _kd, dni_erbs = dsr_to_dni_physics(
                    latest_obs.dsr_W_m2,
                    obs_clear_sky_dni,
                    latest_obs_geometry["zenith_deg"],
                    latest_obs_dt,
                )
                k_erbs = dni_erbs / obs_clear_sky_dni if obs_clear_sky_dni > 0 else 0.0
                cloud_transmittance = 1.0 - (latest_obs.cloud_cover_pct / 100.0)
            else:
                # No KMA observations supplied, rely on cloud patch or clear-sky
                cloud_transmittance = 1.0 - cloud_patch.mean_cloud_fraction
                k_erbs = cloud_transmittance
                dni_erbs = k_erbs * cs_dni

            # Feature vector for Stage 2 Quantile Ensemble
            # Features: [1.0, k_direct_prior, k_om_prior, cloud_transmittance, advection_attenuation]
            advection_impact = float(np.clip(advection_scale * lead_time_hrs, -0.5, 0.5))
            features = np.array(
                [
                    1.0,
                    k_erbs * obs_weight + k_om * (1.0 - obs_weight),
                    k_om,
                    cloud_transmittance,
                    advection_impact,
                ],
                dtype=np.float64,
            ).reshape(1, 5)

            k10_arr, k50_arr, k90_arr = self.ensemble.predict_factors(features)
            k10 = float(k10_arr[0])
            k50 = float(k50_arr[0])
            k90 = float(k90_arr[0])

            # Adjust spread based on cloud volatility and lead time
            spread_expansion = volatility_index * 0.55 + (1.0 - obs_weight) * 0.20
            k10 = max(0.0, k10 - spread_expansion * 0.75)
            k90 = min(1.05, max(k50, k90 + spread_expansion * 0.75))

            # Re-enforce monotonicity
            k50 = max(k10, min(k90, k50))

            max_dni = round(cs_dni * 1.05, 1)
            raw_p10 = round(float(np.clip(k10 * cs_dni, 0.0, max_dni)), 1)
            raw_p50 = round(float(np.clip(k50 * cs_dni, raw_p10, max_dni)), 1)
            raw_p90 = round(float(np.clip(k90 * cs_dni, raw_p50, max_dni)), 1)

            dni_p10 = max(0.0, min(raw_p10, max_dni))
            dni_p50 = max(dni_p10, min(raw_p50, max_dni))
            dni_p90 = max(dni_p50, min(raw_p90, max_dni))
            uncertainty_spread = round(dni_p90 - dni_p10, 1)

            kma_ai_dni = dni_p50 if has_kma else None
            cs_index_p50 = round(dni_p50 / cs_dni, 3) if cs_dni > 0 else 0.0

            # Mode selection
            if request.mode == "kma_ai_ensemble":
                if has_kma:
                    selected_val = dni_p50
                    selected_src = "kma_ai_ensemble"
                    fallback_used = False
                elif not om_fallback:
                    selected_val = om_dni
                    selected_src = "open_meteo"
                    fallback_used = False
                else:
                    selected_val = cs_dni
                    selected_src = "pvlib_clear_sky"
                    fallback_used = True
            elif request.mode == "open_meteo_only":
                selected_val = om_dni
                selected_src = "open_meteo" if not om_fallback else "pvlib_clear_sky"
                fallback_used = om_fallback
            else:  # shadow_compare
                selected_val = dni_p50
                selected_src = "kma_ai_ensemble" if has_kma else "open_meteo"
                fallback_used = False

            point = QuantileNowcastPoint(
                timestamp=utc_z(dt),
                solar_elevation_deg=round(el, 2),
                clear_sky_dni_W_m2=round(cs_dni, 1),
                open_meteo_dni_W_m2=round(om_dni, 1),
                kma_ai_dni_W_m2=kma_ai_dni,
                dni_p10_W_m2=dni_p10,
                dni_p50_W_m2=dni_p50,
                dni_p90_W_m2=dni_p90,
                clear_sky_index_p50=cs_index_p50,
                uncertainty_spread_W_m2=uncertainty_spread,
                selected_dni_W_m2=selected_val,
                selected_source=selected_src,
                fallback_used=fallback_used,
            )
            quantile_points.append(point)

            if point.open_meteo_dni_W_m2 is not None:
                recorded_om.append(point.open_meteo_dni_W_m2)
            recorded_kma_p50.append(point.dni_p50_W_m2)

        # Compute Comparison Metrics
        spreads = [p.uncertainty_spread_W_m2 for p in quantile_points if p.solar_elevation_deg > 0]
        mean_spread = float(np.mean(spreads)) if spreads else 0.0

        if recorded_om and recorded_kma_p50:
            kma_vs_om_mae = float(
                np.mean(np.abs(np.array(recorded_kma_p50) - np.array(recorded_om)))
            )
        else:
            kma_vs_om_mae = None

        # Reference actual DNI comparison (if ground truth supplied)
        kma_rmse: float | None = None
        om_rmse: float | None = None
        pinball_loss: float | None = None

        if request.reference_actual_dni:
            actual_pairs: list[tuple[float, float, float, float, float]] = []
            for p in quantile_points:
                if p.timestamp in request.reference_actual_dni:
                    act = request.reference_actual_dni[p.timestamp]
                    actual_pairs.append(
                        (
                            act,
                            p.dni_p50_W_m2,
                            p.open_meteo_dni_W_m2 or 0.0,
                            p.dni_p10_W_m2,
                            p.dni_p90_W_m2,
                        )
                    )
            if actual_pairs:
                acts = np.array([pair[0] for pair in actual_pairs])
                p50s = np.array([pair[1] for pair in actual_pairs])
                oms = np.array([pair[2] for pair in actual_pairs])
                p10s = np.array([pair[3] for pair in actual_pairs])
                p90s = np.array([pair[4] for pair in actual_pairs])

                kma_rmse = float(np.sqrt(np.mean((acts - p50s) ** 2)))
                om_rmse = float(np.sqrt(np.mean((acts - oms) ** 2)))

                # 3-Quantile Pinball loss
                l10 = np.mean(np.maximum(0.10 * (acts - p10s), (0.10 - 1.0) * (acts - p10s)))
                l50 = np.mean(np.maximum(0.50 * (acts - p50s), (0.50 - 1.0) * (acts - p50s)))
                l90 = np.mean(np.maximum(0.90 * (acts - p90s), (0.90 - 1.0) * (acts - p90s)))
                pinball_loss = float((l10 + l50 + l90) / 3.0)

        metrics = ComparisonMetrics(
            sample_count=len(quantile_points),
            mean_uncertainty_spread_W_m2=round(mean_spread, 1),
            cloud_volatility_index=round(volatility_index, 3),
            kma_vs_open_meteo_mae_W_m2=round(kma_vs_om_mae, 1)
            if kma_vs_om_mae is not None
            else None,
            kma_ai_rmse_W_m2=round(kma_rmse, 1) if kma_rmse is not None else None,
            open_meteo_rmse_W_m2=round(om_rmse, 1) if om_rmse is not None else None,
            kma_ai_pinball_loss=round(pinball_loss, 2) if pinball_loss is not None else None,
        )

        run_id = str(uuid4())

        # Persist run and points in DB if engine provided
        if self.engine:
            self._persist_run(run_id, site, current_time, quantile_points)

        return NowcastComparisonResponse(
            run_id=run_id,
            scene_id=scene_id,
            mode=request.mode,
            model_version=self.model_version,
            start_time=utc_z(start_dt),
            end_time=utc_z(end_dt),
            interval_seconds=request.interval_seconds,
            points=quantile_points,
            metrics=metrics,
        )

    def _persist_run(
        self,
        run_id: str,
        site: Site,
        now: datetime,
        points: list[QuantileNowcastPoint],
    ) -> None:
        """Persist nowcast run and quantile points with _upgrade_3 columns."""
        if not self.engine:
            return

        expires = now + timedelta(minutes=60)
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text("""
                    INSERT INTO nowcast_run(run_id, site_key, created_at, input_snapshot_ids_json, algorithm_version, status, expires_at)
                    VALUES (:id, :site, :created, :snapshots, :algorithm, 'ready', :expires)
                    """),
                    {
                        "id": run_id,
                        "site": f"{site.latitude_deg:.6f},{site.longitude_deg:.6f}",
                        "created": utc_z(now),
                        "snapshots": json.dumps([]),
                        "algorithm": self.model_version,
                        "expires": utc_z(expires),
                    },
                )
                for pt in points:
                    conn.execute(
                        text("""
                        INSERT INTO nowcast_point(
                            run_id, valid_time, clear_sky_dni_W_m2, clear_sky_index,
                            final_dni_W_m2, prediction_method, contributors_json, fallback_reason,
                            dni_p10_W_m2, dni_p50_W_m2, dni_p90_W_m2, kma_ai_dni_W_m2, open_meteo_dni_W_m2
                        )
                        VALUES (
                            :run, :time, :clear, :index,
                            :final, :method, :contributors, :fallback_reason,
                            :p10, :p50, :p90, :kma_dni, :om_dni
                        )
                        """),
                        {
                            "run": run_id,
                            "time": pt.timestamp,
                            "clear": pt.clear_sky_dni_W_m2,
                            "index": pt.clear_sky_index_p50,
                            "final": pt.selected_dni_W_m2,
                            "method": pt.selected_source,
                            "contributors": json.dumps([{"source": pt.selected_source}]),
                            "fallback_reason": "clear_sky_fallback" if pt.fallback_used else None,
                            "p10": pt.dni_p10_W_m2,
                            "p50": pt.dni_p50_W_m2,
                            "p90": pt.dni_p90_W_m2,
                            "kma_dni": pt.kma_ai_dni_W_m2,
                            "om_dni": pt.open_meteo_dni_W_m2,
                        },
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist nowcast run %s: %s", run_id, exc)
