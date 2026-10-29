"""Shared persisted-scene site discovery for all source-specific workers."""

from sqlalchemy import Engine, text

from app.schemas.scene import Scene
from engine.fr01.models import Site


def _site_key(site: Site) -> tuple[float, float]:
    return (site.latitude_deg, site.longitude_deg)


def load_unique_sites(engine: Engine) -> tuple[Site, ...]:
    """Read valid persisted scenes and deduplicate their physical sites."""

    with engine.connect() as connection:
        payloads = connection.execute(text("SELECT scene_json FROM scenes ORDER BY scene_id")).scalars()
        sites: dict[tuple[float, float], Site] = {}
        for payload in payloads:
            try:
                scene = Scene.model_validate_json(payload)
            except ValueError:
                continue
            site = Site(scene.site.latitude_deg, scene.site.longitude_deg, scene.site.timezone)
            sites.setdefault(_site_key(site), site)
    return tuple(sites.values())
