# Functional Requirements

Numbering follows the requirement document (`docs/reports/SunSync_Requirements.pdf`, updated 2026-10-21).
This term covers FR-01 to FR-06. Features outside this scope will be added later with new numbers.

## Overview

| ID | Requirement | Owner | Previous ID |
|---|---|---|---|
| FR-01 | Future sunlight path prediction | Misong Ha | FR-01 |
| FR-02 | Selective shading decision | Heegyeong Chung | FR-02 |
| FR-03 | QR-based spatial measurement | Daeun Lee | New |
| FR-04 | Stable control against temporary changes | - | FR-04 |
| FR-05 | Film control interface | - | New |
| FR-06 | Space information management | Heegyeong Chung | FR-06 |

Note: in documents written before 2026-10-21, FR-03 referred to outdoor shadows.
It now refers to QR-based spatial measurement.

## Flow

```
FR-03 measure -> FR-06 store -> FR-01 predict -> FR-02 decide -> FR-04 smooth -> FR-05 apply
                    ^                                                             |
                    +------------- FR-05.2 shows the marker for FR-03 <-----------+
```

FR-06 is the shared contract: every other FR reads the space model from it.

## Sub-requirements

### FR-01 Future Sunlight Path Prediction
- FR-01.1 Solar position for each time step from the location and a time zone-aware start time
- FR-01.2 Direct-sun path tracing from the centre sample of each 5 cm x 5 cm protected-region tile toward the sun; for a valid path, return `candidate_path.window_id` and `candidate_path.window_hit_m` in ENU, not a film-cell index
- FR-01.3 Exposure timeline: exposure and reason per time step, first predicted exposure time
- FR-01.4 Sunlight intensity and future DNI prediction: select DNI from Scene weather, durable Open-Meteo hourly snapshots, or pvlib clear-sky fallback; Open-Meteo is the sole production provider

### FR-02 Selective Shading Decision
- FR-02.1 Cell mapping: convert every exposed 5 cm protected-region tile's FR-01 `candidate_path.window_hit_m` from ENU to the selected window's local coordinates, expand it by 2.5 cm in each local direction, then return every overlapping 10 cm x 10 cm film cell `(window_id, row, col)`. A film cell is a shading candidate when at least one exposed tile maps to it. This is the sole owner of point-to-cell mapping and the input to shading decisions.
- FR-02.2 Grade-based decision; the stricter grade applies when a cell affects several areas
- FR-02.3 Advance shading that allows for switching time, processing delay and a safety margin
- FR-02.4 Shading schedule provided to the mobile and web applications

The exact FR-01 → FR-02.1 tile contract is in [fr01_fr02_tile_contract.md](fr01_fr02_tile_contract.md).

### FR-03 QR-Based Spatial Measurement
- FR-03.1 Marker detection and image quality check in each photo
- FR-03.2 Camera position from the known marker size and its four corners; photos without the whole marker are rejected
- FR-03.3 Furniture output: the camera positions of the four corner photos become the furniture corners (width, depth, position relative to the window and walls)

### FR-04 Stable Control Against Temporary Changes
- FR-04.1 Gap filling: short transparent gaps between shaded periods stay opaque
- FR-04.2 Minimum holding time, except to strengthen protection
- FR-04.3 Threshold margin for grades B and C (switch on above the limit, off only below a lower value)
- FR-04.4 Edge margin: the neighboring cell is shaded when a light path crosses a cell boundary

### FR-05 Film Control Interface
- FR-05.1 Cell commands applied at the scheduled time and recorded
- FR-05.2 Marker display on a fixed group of cells during measurement, then restore
- FR-05.3 Replaceable device, independent of film hardware and type (PDLC or SPD)

### FR-06 Space Information Management
- FR-06.1 Space model: location, room size, zones, windows with film cell layout, furniture as boxes with a type, protected areas
- FR-06.2 Window and marker specification
- FR-06.3 Measurement records (manual or marker-based)
- FR-06.4 Validation and versioning (409 on conflicting updates)
- FR-06.5 Synchronization between the mobile and web applications
- FR-06.6 Protection grades A to D
