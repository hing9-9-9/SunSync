# UI data integration (2026-11-01)

Working copy: `/Users/ldi/Documents/Codex/SunSync-integration`, branch `feature/ui-integration`.
The original `/Users/ldi/Documents/GitHub/SunSync` checkout is unchanged.
This branch combines the local UI branch with main at `741617d`; newer remote commits have not been fetched.

- UI POST/PUT persists a shared Scene atomically with its UI projection.
- The projection uses current FR06 geometry (no removed sample_points_m), furniture types, numeric floor levels, and explicitly positioned windows with 10 cm film grids.
- Legacy windows without wall/offset/sill remain editable but are not invented as calculation geometry. Review location, north alignment and window position, then save again before prediction.
- Saved and north-confirmed spaces request FR01 predictions, pinned to the server Scene revision. The date is selectable in the space timezone; the current UI requests a full day at 30 minute intervals to limit tile workload. Large scenes can still exceed server limits and show an error.
- The summary distinguishes Open-Meteo, clear-sky and missing irradiance; 0 is a physical value, null is unknown. Results report sampled first exposure and peak sampled surface irradiance.
- The plan's decorative sunlight polygon is hidden during real prediction. FR01 does not provide a whole-floor polygon; the actual results appear in the region summary. Film mapping remains an explicitly labeled FR02 simulation.
- Existing stored scenes using the older sample_points_m schema are not migrated by this change. Use a fresh development database or migrate old data separately; do not delete a real database.
- External/native Scenes without a matching UI projection remain listed as unavailable; this implementation does not import arbitrary polygonal rooms into the rectangular UI.
- Weather collection is a separate worker (`PYTHONPATH=backend python -m app.nowcast.open_meteo_worker`); the prediction request never invokes it. Without a saved forecast, the UI identifies the clear-sky fallback.

Run backend from this directory:

```sh
/Users/ldi/Documents/GitHub/SunSync/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8011
```

Run frontend:

```sh
cd frontend
SUNSYNC_API_TARGET=http://127.0.0.1:8011 npm run dev -- --host 127.0.0.1 --port 5181
```

No GitHub push or PR is performed.
