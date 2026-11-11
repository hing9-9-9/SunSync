# Functional Requirements

Numbering follows the requirement document (`docs/reports/SunSync_Requirements.pdf`, updated 2026-10-21; the submitted specification report is `docs/reports/SunSync_Specification.pdf`, and both have LaTeX sources in `docs/reports/`).
This term covers FR-01 to FR-07. FR-07 (deployment and accounts) was added on 2026-11-05 and is not in the requirement document. Other features outside this scope will be added later with new numbers.

FR numbers and titles follow the requirement document. The descriptions follow the current design, which changed after the report;
the differences are listed in [Changes since the requirement document](#changes-since-the-requirement-document)
and are merged into the report at its next revision.

## Overview

| ID | Requirement | Owner | Previous ID |
|---|---|---|---|
| FR-01 | Future sunlight path prediction | Misong Ha | FR-01 |
| FR-02 | Selective shading decision | Jiyeon Kim | FR-02 |
| FR-03 | QR-based spatial measurement | Daeun Lee | New |
| FR-04 | Stable control against temporary changes | - | FR-04 |
| FR-05 | Film control interface | - | New |
| FR-06 | Space information management | Heegyeong Chung | FR-06 |
| FR-07 | Deployment and accounts | Jiyeon Kim | New |

Note: in documents written before 2026-10-21, FR-03 referred to outdoor shadows.
It now refers to QR-based spatial measurement.

## Flow

```
FR-03 measure -> FR-06 store -> FR-01 predict -> FR-02 decide -> FR-04 smooth -> FR-05 apply
                    ^                                                             |
                    +------------- FR-05.2 shows the marker for FR-03 <-----------+
```

FR-06 is the shared contract: every other FR reads the space model from it.

What runs today: the screens apply the FR-02 plan to the film (`film-schedule-apply`). FR-04 is computed for the effect badges and can also be applied with `apply_to_virtual_film`. Which plan the film should receive by default is not decided yet.

## Sub-requirements

### FR-01 Future Sunlight Path Prediction
- FR-01.1 Solar position for each time step from the location and a time zone-aware start time
- FR-01.2 Direct-sun path tracing from the centre sample of each 5 cm x 5 cm protected-region tile toward the sun; for a valid path, return `candidate_path.window_id` and `candidate_path.window_hit_m` in ENU, not a film-cell index
- FR-01.3 Exposure timeline: exposure and reason per time step, first predicted exposure time
- FR-01.4 Sunlight intensity and future DNI prediction: the DNI of a prediction is selected from Scene weather, durable Open-Meteo hourly snapshots, or the pvlib clear-sky fallback; Open-Meteo is the sole provider of that selection. Separately, workers store a KMA AI x Open-Meteo quantile nowcast (q10, q50, q90 at 10, 30 and 60 minutes), read-only at `GET /api/scenes/{scene_id}/ai-nowcast/latest`; it runs in shadow mode by default and feeds the forecast chart and FR-04 (see [FR-01.4 AI guide](fr01_4_README.md))

### FR-02 Selective Shading Decision
- FR-02.1 Cell mapping: convert every exposed 5 cm protected-region tile's FR-01 `candidate_path.window_hit_m` from ENU to the selected window's local coordinates, expand it by 2.5 cm in each local direction, then return every overlapping 10 cm x 10 cm film cell `(window_id, row, col)`. A film cell is a shading candidate when at least one exposed tile maps to it. This is the sole owner of point-to-cell mapping and the input to shading decisions.
- FR-02.2 Grade-based decision: grade A shades every cell that a direct-sun path crosses, even when the intensity is zero or unknown; grades B and C compare the largest per-tile surface irradiance (`direct_irradiance_W_m2`) of an area in a cell with the grade's cap, and record `infeasible` when the film cannot meet the cap or `unknown` when the intensity is unknown; the stricter grade applies when a cell affects several areas
- FR-02.3 Advance shading that allows for switching time, processing delay and a safety margin
- FR-02.4 Shading schedule provided to the mobile and web applications as a `ShadingPlan` (cells, time segments, state and reason) through `POST /api/scenes/{scene_id}/shading-plans`; the format is in [fr02_schedule.md](fr02_schedule.md)

The exact FR-01 → FR-02.1 tile contract is in [fr01_fr02_tile_contract.md](fr01_fr02_tile_contract.md).

### FR-03 QR-Based Spatial Measurement
- FR-03.1 Marker detection and image quality check in each photo
- FR-03.2 Camera position from the known marker size and its four corners; photos without the whole marker are rejected
- FR-03.3 Furniture output: the measured points of the chosen shape (4 for a rectangle or circle, 6 for an ellipse, 3 to 16 for a polygon) are estimated jointly from the selected observations and give a cuboid (width, depth, height, rotation, position relative to the window and walls); see [single marker](fr03_single_marker.md), [measurement workflow](fr03_measurement_workflow.md) and [robust estimation](fr03_robust_measurement.md)
- FR-03.4 The marker is one 60 cm marker (6 x 6 cells of 10 cm in the centre of a 1 m film module) whose ID the server chooses for each capture session

### FR-04 Stable Control Against Temporary Changes
- FR-04.1 Gap filling: short transparent gaps between shaded periods stay opaque
- FR-04.2 Minimum holding time, except to strengthen protection
- FR-04.3 Threshold margin for grades B and C (switch on above the limit, off only below a lower value)
- FR-04.4 Edge margin: the neighboring cell is shaded when a light path crosses a cell boundary

Current design ([FR-04 guide](fr04_adaptive_control_lg_sgf.md)): by default FR-04 only smooths the FR-02 plan (FR-04.1 to FR-04.3) and never makes a cell brighter than FR-02. FR-04.4 is met by FR-02.1, which already shades every cell that a tile overlaps. The dynamic-programming controller that decides on its own is the experimental `replace` mode of `POST /api/scenes/{scene_id}/adaptive-shading-plan`.

### FR-05 Film Control Interface
- FR-05.1 Cell commands applied at the scheduled time and recorded
- FR-05.2 Marker display on a fixed group of cells during measurement, then restore
- FR-05.3 Replaceable device, independent of film hardware and type (PDLC or SPD)

### FR-06 Space Information Management
- FR-06.1 Space model: location, room size, zones, windows with film cell layout (`film_rows`, `film_cols`), furniture as boxes with a furniture type, an optional building floor level, and protected areas given as rectangles of four vertices (FR-01 divides them into 5 cm tiles, so no sample points are stored)
- FR-06.2 Window and marker specification: the marker occupies a fixed block of film cells (start row and column, 6 x 6 cells with border) and its physical size follows from the window's cell size
- FR-06.3 Measurement records (manual or marker-based)
- FR-06.4 Validation and versioning (409 on conflicting updates)
- FR-06.5 Synchronization between the mobile and web applications
- FR-06.6 Protection grades A to D
- FR-06.7 Furniture volumes in the same zone must not overlap (rejected when the space is saved); touching is allowed

### FR-07 Deployment and Accounts
- FR-07.1 Deployment: one server with an https address, services that restart after a reboot, database backups, and `main` deployed automatically every 15 minutes while a switch file exists ([deploy.md](deploy.md))
- FR-07.2 Google login: sessions end after 30 minutes without use and after 12 hours at most, and `/measure` needs the login too ([auth.md](auth.md))
- FR-07.3 A space belongs to the account that created it; another account cannot see or change it ([auth.md](auth.md))

## Changes since the requirement document

Numbers and titles are unchanged. The descriptions below differ from `docs/reports/SunSync_Requirements.pdf`
and are applied to the report at its next revision.

| ID | Requirement document | Current design |
|---|---|---|
| FR-01.2 | Trace a ray from sample points on each protected surface | The protected area is divided into 5 cm x 5 cm tiles and a ray is traced from each tile centre; a valid path returns `candidate_path.window_id` and `candidate_path.window_hit_m` |
| FR-01.4 | Clear-sky intensity, replaced by forecasts such as Open-Meteo when available | Final DNI per timestamp: Scene weather, then a stored Open-Meteo snapshot, then pvlib clear-sky, otherwise unknown; converted to a per-tile surface irradiance |
| FR-02.1 | Map each window crossing point to the film cell that contains it | The crossing point is expanded by 2.5 cm in each window direction and every overlapping film cell is returned |
| FR-02.2 | Apply the grade of each area; the stricter grade wins | Adds the grade A rule (shade regardless of intensity), the per-cell maximum irradiance check for grades B and C, and the `infeasible` and `unknown` states |
| FR-02.4 | Provide the planned cell states over time | The plan is a `ShadingPlan` returned by `POST /api/scenes/{scene_id}/shading-plans` |
| FR-06.1 | Protected areas with their surfaces, sample points and grades | Protected areas are rectangles of four vertices without stored sample points; adds the film grid (`film_rows`, `film_cols`), the furniture type and an optional floor level |
| FR-06.2 | Size, position and orientation of the QR marker on the window film | The marker is placed on a fixed block of film cells (start row and column, 6 x 6) and its size follows from the cell size |
| FR-01.4 | Forecasts such as Open-Meteo replace the clear-sky value | Adds a KMA AI x Open-Meteo quantile nowcast from separate workers (shadow mode by default), served read-only next to the prediction |
| FR-03.3 | Furniture corners from the camera positions of four photos | Measured points follow the shape and are fitted jointly from selected observations; one 60 cm marker replaces the board |
| FR-04 | Smooth the schedule with four rules | By default the rules smooth the FR-02 plan; FR-04.4 is met by FR-02.1; the dynamic-programming controller is experimental |
| FR-06.7 | Not in the requirement document | Furniture volumes in the same zone must not overlap |
| FR-07 | Not in the requirement document | Deployment, Google login and spaces per account |
