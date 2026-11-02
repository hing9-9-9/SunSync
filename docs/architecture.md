# Architecture

```text
Mobile / Web PWA → FastAPI backend → SQLite Scene storage → FR-01 prediction
                                                    ↓
                          FR-05 virtual film ← FR-04 smoothing ← FR-02 decision
```

- **Space data (FR-06).** A shared Pydantic `Scene` contract at `backend/app/schemas/scene.py` holds metric ENU geometry and observation provenance. FastAPI validates it before storing it in SQLModel `SceneRecord` rows. `POST /api/scenes` imports, `GET` reads/lists, and `PUT` replaces with an atomic revision check. See [FR-06](fr06_design.md).
- **Prediction (FR-01).** `engine/fr01/` calculates solar positions with pvlib and traces direct-sun paths from protected sample points through windows. `POST /api/scenes/{scene_id}/sunpath-predictions` combines this with DNI selection. A separate worker stores Open-Meteo hourly DNI snapshots in SQLite; each request chooses `Scene.weather`, then a durable snapshot, then pvlib clear-sky. See [FR-01.4 design](fr01_4_open_meteo_dni_design.md) and [API](fr01_api.md).
- **Measurement (FR-03).** The app photographs the window-film marker from furniture corners; OpenCV ArUco and solvePnP produce the corresponding Scene coordinates.
- **Shading (FR-02, FR-04, FR-05).** FR-02 maps window crossings to film cells, FR-04 smooths the schedule, and FR-05 applies it to the virtual film.
- **Frontend.** React + Vite PWA provides a 2D mobile view and a Three.js web view.

The FR-02 output, the `ShadingPlan`, is described in [fr02_schedule.md](fr02_schedule.md) (format), [fr02_design.md](fr02_design.md), [fr02_api.md](fr02_api.md). How FR-04 and FR-05 use it remains to be agreed in `decisions.md`.
