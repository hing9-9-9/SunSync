# Architecture (draft)

```
Mobile app (PWA)  -- marker photos, space input -->  Backend (FastAPI)
Web app (PWA)     -- protected areas, 3D view  -->     |
                                                       v
   FR-03 measurement -> FR-06 Scene (SQLite) -> FR-01 prediction (engine/fr01)
                                                       |
                         FR-05 virtual film <- FR-04 smoothing <- FR-02 decision
                                                       |
                                 cell states and schedule -> mobile and web apps
```

- **Space data (FR-06).** A shared Pydantic `Scene` contract at `backend/app/schemas/scene.py` holds metric ENU
  geometry (x east, y north, z up, floor at z = 0) with observation provenance. FastAPI validates it before storing
  it in the SQLModel `SceneRecord` table. `POST /api/scenes` imports, `GET` reads and lists, and `PUT` replaces a
  scene with an atomic revision check (409 on conflict). See [FR06](fr06_design.md).
- **Prediction (FR-01).** `engine/fr01/` computes solar positions with pvlib and traces direct-sun paths from
  protected sample points through windows. The backend reads `Scene` directly and exposes
  `POST /api/scenes/{scene_id}/sunpath-predictions`. FR-01.1~FR-01.3 geometry와 FR-01.4 DNI selection은
  현재 API 동작이다. 별도 worker가 Open-Meteo hourly DNI snapshot을 SQLite에 보존하고, 요청은
  `Scene.weather → durable Open-Meteo snapshot → pvlib clear-sky` 순서로 선택한다. GK2A는
  experimental package에 보존되며 운영 후보가 아니다. 상세는 [현행 FR-01.4 설계](fr01_4_open_meteo_dni_design.md)와
  [FR-01 API](fr01_api.md)를 따른다.
- **Measurement (FR-03).** The app photographs the marker shown on the window film from each furniture corner;
  OpenCV ArUco and solvePnP give the camera position, which becomes the furniture corner in the scene.
- **Shading (FR-02, FR-04, FR-05).** FR-02 maps window crossing points to the 10 x 10 film cells and decides cell
  states by grade, FR-04 smooths the schedule, and FR-05 applies it to a virtual film that can later be replaced
  by a real controller.
- **Frontend.** React + Vite PWA: a 2D top view in the mobile app, a Three.js 3D view on the web.

Module boundaries and data formats between FR-02, FR-04 and FR-05 are still to be agreed (see `decisions.md`).
