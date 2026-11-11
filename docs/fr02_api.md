# FR-02 API

Result format: [fr02_schedule.md](fr02_schedule.md). Decision rules: [fr02_design.md](fr02_design.md).

## Shading plan of a Scene

`POST /api/scenes/{scene_id}/shading-plans` returns the `ShadingPlan` of a stored Scene.

| Request field | Meaning |
|---|---|
| `start_time` | ISO 8601 time with seconds and a time zone offset, for example `2026-10-14T04:30:00Z` |
| `duration_minutes` | Length of the plan, 1 to 1440, default 120 |
| `interval_seconds` | Time step, 1 to 3600, default 300 |
| `expected_scene_revision` | Optional; a different stored revision gives 409 |

With the Google login on, a Scene of another account answers 404, like a missing one ([auth.md](auth.md)).

All protected areas are planned; there is no area filter, because a plan for some areas would leave the others
unprotected. Errors: 404 (no such Scene), 409 (revision differs), 422 (invalid input, or a Scene without
protected areas). The endpoint runs the FR-01 prediction with the same limits and applies the FR-02 rules to
it, using the period and the time step that the FR-01 result reports. Only the DNI selection record that FR-01
already keeps is stored.

```bash
curl -sS -X POST http://localhost:8000/api/scenes/scene_demo_001/shading-plans \
  -H 'Content-Type: application/json' \
  -d '{"start_time": "2026-10-14T04:30:00Z", "duration_minutes": 30, "interval_seconds": 60}'
```
