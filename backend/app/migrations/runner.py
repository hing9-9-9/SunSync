"""Versioned SQLite schema upgrades.

SQLModel still creates the legacy tables.  Migrations own the durable nowcast
schema and record their progress in SQLite's built-in ``user_version``.
"""

from sqlalchemy import Engine

LATEST_VERSION = 5


def _upgrade_1(connection) -> None:  # type: ignore[no-untyped-def]
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS weather_snapshot (
          snapshot_id TEXT PRIMARY KEY,
          provider TEXT NOT NULL,
          product TEXT NOT NULL,
          model TEXT,
          observed_at TEXT,
          issued_at TEXT,
          fetched_at TEXT NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_path TEXT,
          quality_status TEXT NOT NULL
            CHECK (quality_status IN ('valid', 'degraded', 'invalid'))
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS weather_point (
          id INTEGER PRIMARY KEY,
          snapshot_id TEXT NOT NULL REFERENCES weather_snapshot(snapshot_id) ON DELETE CASCADE,
          valid_time TEXT NOT NULL,
          latitude_deg REAL NOT NULL,
          longitude_deg REAL NOT NULL,
          grid_resolution_km REAL,
          dni_W_m2 REAL,
          ghi_W_m2 REAL,
          direct_horizontal_W_m2 REAL,
          cloud_cover_percent REAL,
          temporal_meaning TEXT NOT NULL CHECK (temporal_meaning IN ('instant', 'mean')),
          interval_start TEXT,
          interval_end TEXT,
          UNIQUE(snapshot_id, valid_time, latitude_deg, longitude_deg)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS nowcast_run (
          run_id TEXT PRIMARY KEY,
          site_key TEXT NOT NULL,
          created_at TEXT NOT NULL,
          input_snapshot_ids_json TEXT NOT NULL,
          algorithm_version TEXT NOT NULL,
          status TEXT NOT NULL,
          result_hash TEXT,
          expires_at TEXT NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS nowcast_point (
          id INTEGER PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES nowcast_run(run_id) ON DELETE CASCADE,
          valid_time TEXT NOT NULL,
          clear_sky_dni_W_m2 REAL NOT NULL,
          clear_sky_index REAL,
          final_dni_W_m2 REAL NOT NULL,
          prediction_method TEXT NOT NULL,
          contributors_json TEXT NOT NULL,
          fallback_reason TEXT,
          UNIQUE(run_id, valid_time)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS worker_lease (
          lease_name TEXT PRIMARY KEY,
          owner_id TEXT NOT NULL,
          expires_at TEXT NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_weather_snapshot_fetched_at ON weather_snapshot(fetched_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_nowcast_run_expires_at ON nowcast_run(expires_at)"
    )


MIGRATIONS = {1: _upgrade_1}


def _upgrade_2(connection) -> None:  # type: ignore[no-untyped-def]
    connection.exec_driver_sql("ALTER TABLE weather_snapshot ADD COLUMN provenance_json TEXT")


MIGRATIONS[2] = _upgrade_2


def _upgrade_3(connection) -> None:  # type: ignore[no-untyped-def]
    connection.exec_driver_sql("ALTER TABLE nowcast_point ADD COLUMN dni_p10_W_m2 REAL")
    connection.exec_driver_sql("ALTER TABLE nowcast_point ADD COLUMN dni_p50_W_m2 REAL")
    connection.exec_driver_sql("ALTER TABLE nowcast_point ADD COLUMN dni_p90_W_m2 REAL")
    connection.exec_driver_sql("ALTER TABLE nowcast_point ADD COLUMN kma_ai_dni_W_m2 REAL")
    connection.exec_driver_sql("ALTER TABLE nowcast_point ADD COLUMN open_meteo_dni_W_m2 REAL")


MIGRATIONS[3] = _upgrade_3


def _upgrade_4(connection) -> None:  # type: ignore[no-untyped-def]
    """Add site-scoped source snapshots and production AI nowcast tables."""

    connection.exec_driver_sql("ALTER TABLE weather_snapshot ADD COLUMN site_key TEXT")
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_weather_snapshot_site_provider "
        "ON weather_snapshot(site_key, provider, fetched_at)"
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS ai_nowcast_run (
          run_id TEXT PRIMARY KEY,
          scene_id TEXT NOT NULL,
          site_key TEXT NOT NULL,
          prediction_origin TEXT NOT NULL,
          created_at TEXT NOT NULL,
          model_version TEXT NOT NULL,
          model_manifest_sha256 TEXT NOT NULL,
          input_snapshot_ids_json TEXT NOT NULL,
          rollout_mode TEXT NOT NULL CHECK (rollout_mode IN ('shadow', 'active')),
          status TEXT NOT NULL CHECK (status IN ('ready', 'degraded')),
          result_hash TEXT NOT NULL,
          expires_at TEXT NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS ai_nowcast_point (
          id INTEGER PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES ai_nowcast_run(run_id) ON DELETE CASCADE,
          horizon_minutes INTEGER NOT NULL CHECK (horizon_minutes IN (10, 30, 60)),
          valid_time TEXT NOT NULL,
          prediction_source TEXT NOT NULL CHECK (
            prediction_source IN (
              'ai_model', 'open_meteo_default', 'open_meteo_fallback', 'unavailable'
            )
          ),
          dni_W_m2 REAL,
          dni_p10_W_m2 REAL,
          dni_p50_W_m2 REAL,
          dni_p90_W_m2 REAL,
          open_meteo_dni_W_m2 REAL,
          shadow_dni_p10_W_m2 REAL,
          shadow_dni_p50_W_m2 REAL,
          shadow_dni_p90_W_m2 REAL,
          kma_snapshot_id TEXT,
          kma_observed_at TEXT,
          open_meteo_snapshot_id TEXT,
          open_meteo_fetched_at TEXT,
          fallback_used INTEGER NOT NULL CHECK (fallback_used IN (0, 1)),
          fallback_reason TEXT,
          quality_flags_json TEXT NOT NULL,
          UNIQUE(run_id, horizon_minutes)
        )
        """
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_ai_nowcast_run_scene_created "
        "ON ai_nowcast_run(scene_id, created_at)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_ai_nowcast_run_site_origin "
        "ON ai_nowcast_run(site_key, prediction_origin)"
    )


MIGRATIONS[4] = _upgrade_4


def _upgrade_5(connection) -> None:  # type: ignore[no-untyped-def]
    """Add the owner of a space; existing spaces stay without an owner."""

    columns = [row[1] for row in connection.exec_driver_sql("PRAGMA table_info(scenes)")]
    if "owner_id" not in columns:  # a fresh database already got it from the model
        connection.exec_driver_sql("ALTER TABLE scenes ADD COLUMN owner_id TEXT")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_scenes_owner_id ON scenes(owner_id)")


MIGRATIONS[5] = _upgrade_5


def upgrade_database(engine: Engine) -> None:
    """Apply pending upgrades atomically; an interrupted migration is retried."""

    with engine.begin() as connection:
        version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
        for target in range(version + 1, LATEST_VERSION + 1):
            MIGRATIONS[target](connection)
            connection.exec_driver_sql(f"PRAGMA user_version = {target}")
