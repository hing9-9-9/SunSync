# FR-01 MVP — Implementation Guide (3 Stages)

> **Purpose:** Implementation checklist for FR-01, one stage at a time. It is not a design-history report.
>
> **Design reference:** `docs/fr01_design.md`

## Target behavior

For a requested time range, calculate solar elevation/azimuth and determine whether direct sunlight can reach each selected FR-06 `ProtectedRegion.sample_points_m` through a `Window`, without earlier intersection with opaque `Occluder` or `Room` boundaries. Return sample-level paths, region-level time series, and the first **sampled** exposure timestamp.

**Out of scope in this FR-01 MVP guide:** whole-room exposure maps, irradiance/DNI, weather and clouds, reflection/refraction, outdoor shading, ML/DL, FR-02 film-control decisions, frontend, prediction persistence.

> **FR-01.4 범위 변경:** 이 guide가 작성될 때 명시적으로 제외했던 irradiance/DNI, weather, clouds는 이후 FR-01.4의 추가 확장 기능으로 선정되어 구현 범위에 포함됐다. 해당 구현은 이 guide를 확장하지 않고 [`FR-01.4_contract_v1.md`](FR-01.4_contract_v1.md)의 Stage 1·Stage 2 계약을 따른다.

**Invariants:** FR-06's right-handed ENU (x East, y North, z Up; metres), geometry conventions, and Scene schema must not be changed. `exposed` means a *geometric direct path*, not measured light exposure. Never claim complete surface coverage or exact continuous-time first contact.

---

## Stage 1 — Pure Python models + solar calculations

**Goal:** Build a testable, backend-independent foundation without merging FR-06.

### Files to implement

- `engine/fr01/__init__.py`
- `engine/fr01/models.py`
- `engine/fr01/solar.py`
- `tests/test_fr01_solar.py`
- Any minimal packaging/test configuration needed to import `engine.fr01` (avoid unrelated refactors).

### Implementation instructions

1. Read `docs/fr01_design.md`, especially sections 3–7, and inspect existing `engine/` conventions and dependencies. Read the FR-06 Scene contract in `backend/app/schemas/scene.py` for reference only; do not modify it.
2. Define typed, pure-Python computation DTOs for site, room, windows, cuboids, protected regions, prediction options, solar positions, sample/path/blocker results, and region/timeline results. Avoid imports from `backend`, FastAPI, SQLModel, or `app` in `engine/`. Use clear immutable or validated representations and document units.
3. Implement timezone-aware timestamp validation and a deterministic UTC prediction grid: default 120 minutes / 300 seconds; include both endpoints, adding the end timestamp if not aligned. Default yields **25 timestamps**.
4. Use `pvlib.solarposition.get_solarposition(..., method="nrel_numpy", altitude=0, pressure=101325)` to compute **geometric** `elevation` and `azimuth`. Record assumptions; do not use apparent elevation.
5. Convert elevation `h` and azimuth `a` (north=0°, clockwise) to the ENU unit vector pointing toward the sun: `s = (cos(h) sin(a), cos(h) cos(a), sin(h))`. Incoming light direction is `-s`.
6. Validate finite numbers, valid ranges, timezone offsets, and prediction limits. Use the design's proposed limits where applicable; no silent clipping.

### Tests / acceptance

- Solar vector cardinal directions, length ≈ 1, night/horizon cases.
- Offset-aware timestamps only; identical instants with different offsets produce identical results; cross-date ranges.
- Default 25 timestamps; non-aligned end included once; invalid intervals rejected.
- At least one independent published/reference solar-position fixture with a stated tolerance; don't assert correctness by calling the same function twice.
- Run focused tests and available lint/type checks; report exact commands and results.

**Stage 1 exit gate:** solar module and tests pass; no FR-06 schema changes; commit only after review.

---

## Stage 2 — 3D ray casting + temporal predictor

**Goal:** Compute whether selected sample points have an unobstructed direct-sun path through a window, and aggregate over time.

### Files to implement

- `engine/fr01/geometry.py`
- `engine/fr01/predictor.py`
- `tests/test_fr01_geometry.py`
- `tests/test_fr01_prediction.py`
- Extend `engine/fr01/models.py` only as needed.

### Implementation instructions

1. Use the Stage 1 DTOs. Implement reverse rays `r(t) = p + t*s`, `t >= 0`, where `p` is a protected sample point and `s` points toward the sun.
2. At each timestamp, reject `elevation <= 0` and back-facing surfaces (`dot(receiving_normal, s) <= angular_epsilon`).
3. Intersect rays with rectangular window planes and test the hit in each window's **own** `u_axis`/`v_axis` frame. Require a ray leaving the room through the window (`dot(inward_normal, s) < 0`). Handle edge/corner tolerance conservatively.
4. Reconstruct room walls by vertically extruding each floor-polygon edge to `height_m`; include floor and ceiling. For concave rooms, test **all** boundaries and reject paths where an opaque boundary is encountered before a valid window opening. A window opens only its rectangle, not its entire wall.
5. Intersect opaque cuboids by translating/rotating the ray into each occluder's local yaw frame, then applying a slab intersection. Account for parallel rays, grazing contact, and the nearest blocker.
6. For samples on a host cuboid face, ignore only a zero-distance outward contact, **not the entire host**. Reject samples strictly inside cuboids; do not treat free-floating `ProtectedRegion` surfaces as opaque objects.
7. Evaluate all selected sample points per timestamp; a region is `exposed` when **any** sample is exposed. Return a clear path with `window_id`, `window_hit_m`, and direction; otherwise include a diagnostic reason/blocker when known.
8. Set `first_predicted_exposure_time` to the first evaluated timestamp with `exposed=true`; `exposed_at_start` is true when already exposed at the first sample. Never claim a bounded continuous-time error.
9. Keep engine independent of HTTP/DB; no full-room irradiance grid or film control.

### Tests / acceptance

- Analytic south-facing window hit; rotated window frames; missed/parallel/boundary rays.
- Concave room blocking; wall/floor/ceiling; only the window aperture permits exit.
- Cuboid yaw 0°, 37°, 90°; parallel slab; tangent; nearer/farther blockers; host self-intersection.
- Surface normals and tilted faces; one sample exposed vs others blocked.
- Timeline false→true, true from start, never exposed, multiple exposure periods.
- Verify returned coordinates, window IDs, sample indices, and stable tie-breaking.

**Stage 2 exit gate:** independently checkable geometry fixtures and predictor tests pass; no HTTP/DB dependency.

---

## Stage 3 — FR-06 adapter, API + integration verification

**Goal:** Expose predictions through the existing FastAPI backend, using the FR-06 Scene contract unchanged.

### Files to implement

- `backend/app/schemas/sunpath.py`
- `backend/app/services/sunpath.py`
- `backend/app/api/sunpath.py`
- Minimal route registration in `backend/app/main.py` or existing router aggregator.
- `tests/test_sunpath_api.py`
- Minimal usage documentation/examples.

### Implementation instructions

1. Implement `POST /api/scenes/{scene_id}/sunpath-predictions` (synchronous, no persistence). Request: required timezone-aware `start_time`; optional `duration_minutes=120`, `interval_seconds=300`, `protected_region_ids` (omitted means all), `expected_scene_revision`.
2. Retrieve one `SceneRecord` snapshot, parse with existing `Scene.model_validate_json()`, and adapt existing ENU geometry into engine DTOs. Do not import FR-06 backend types into the engine.
3. Validate scene readiness and resource limits **before** calculation. Missing Scene→404, invalid request/geometry→422, revision mismatch→409; internal errors must not be mislabeled as `not_exposed`.
4. Return `scene_id`, actual `scene_revision`, timestamps, solar positions, per-region timelines, sample-level status/reason, unobstructed path/window intersection, blockers, first sampled exposure, and assumptions/warnings. Maintain stable response fields aligned with `docs/fr01_design.md` section 6.
5. Preserve FR-06 endpoints, models, schema version `0.2.0`, and stored Scene JSON. Do not use optional `scenario`/weather/DNI as required inputs; don't change optics or FR-02 policies.
6. Use existing FR-06 synthetic scene fixtures plus simple analytic scenes for API tests. Use an isolated test database and verify predictions do not mutate stored Scene or revision.

### Tests / acceptance

- API 200/404/409/422, unknown/duplicate region IDs, naive timestamps, oversized requests.
- Both a full FR-06 example and a valid scene without optional scenario/weather.
- Correct scene snapshot/revision and UTC timestamps; no DB mutations.
- Existing FR-06 regression tests remain green, plus `pytest`, `ruff`, `mypy` and frontend checks when relevant.
- Provide a runnable API request example, test summary, and known limitations.

**Stage 3 exit gate:** endpoint works with real FR-06 Scene data, tests pass, and existing FR-06 behavior is unchanged.

---

## Working rules for all stages

1. Implement one stage at a time, run its tests, and review before starting the next.
2. Preserve existing branches and teammates' files; never silently merge, reset, or overwrite them.
3. If this guide conflicts with the actual FR-06 code, identify the mismatch and agree on it before altering its contract.
4. Prefer small, testable functions and deterministic analytic fixtures. Explain any necessary deviation from the design.
