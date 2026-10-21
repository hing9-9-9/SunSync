# Decisions

One line per decision. Add a row when the team makes a decision that affects scope, design or numbering.

| Date | Decision | Why | Trade-off |
|---|---|---|---|
| 2026-10-14 | Backend FastAPI, DB SQLite + SQLModel, Frontend React + Vite | Calculation libraries are Python; zero-install DB | SQLite is weak for concurrent writes; move to PostgreSQL if deployed |
| 2026-10-14 | Software only: no physical film, sensors or appliance control | Schedule and budget | No hardware demonstration |
| 2026-10-14 | Meeting notes are kept in the shared Google document; all other documents (requirements, API, data, decisions, reports) live in `docs/`; the GitHub wiki is not used | One place per kind of document; `docs/` changes are reviewed with the code | Meeting notes have no history in the repository |
| 2026-10-15 | Space model uses ENU coordinates (x east, y north, z up, metres) with Scene v0.2.0 | Common format for prediction, measurement and UI (PR #1) | Every module must convert to ENU |
| 2026-10-21 | Protection grades A-D follow the PR #1 definition | Already implemented and tested | Grades B and C need an intensity estimate |
| 2026-10-21 | The film is a virtual device with fine-grained, independently switchable cells | Hardware specifications are not public | Hardware feasibility is not verified |
| 2026-10-21 | Without weather forecasts, sunlight intensity is estimated under clear-sky conditions | Works without external data | May shade unnecessarily on cloudy days |
| 2026-10-21 | Spatial measurement: the marker shown on the window film is photographed once from each of the four corners of a piece of furniture (no video, no VO/SLAM); the camera position from solvePnP is used as the corner, and photos without the whole marker are rejected | Agreed at the 10/21 meeting; simpler than video tracking and has no drift | Furniture height is entered manually; accuracy drops far from the window |
| 2026-10-21 | FR renumbering: FR-03 = QR measurement, FR-05 = film control interface; the former FR-03, FR-05, FR-07 and FR-08 are removed and will be added later with new numbers | Reflect new functions and keep this term's list to FR-01 to FR-06 | FR-03 means something different in older documents |
| 2026-10-21 | Main scenario is a library reading room; the space model stays general | Clearest motivation and evidence | Other spaces are not tested this term |
| 2026-10-21 | The film is modeled as PDLC by default: an opaque cell scatters direct sunlight into diffuse light; documents say "opaque", not "darken"; SPD is an alternative | PDLC is fast, clear when transparent and widely available | Opaque cells reduce the direct beam but not all light reaching the room |
| 2026-10-21 | A 1 m x 1 m window is divided into 10 x 10 cells of 10 cm | Fine enough to shade a single shelf and to show a marker | Real products offer only a few segments |
| 2026-10-21 | The marker on the film is an ArUco 4x4 marker (6 x 6 cells with border); the team keeps calling it the "QR marker" | The smallest QR code needs 21 x 21 cells and does not fit 10 x 10 | The marker carries only an ID |
| 2026-10-21 | Sunlight intensity uses Open-Meteo forecasts when available, with clear-sky values as the fallback | Weather changes intensity, which grades B and C depend on | Forecasts are model estimates, not on-site measurements |
| 2026-10-21 | FR-04 smooths the planned schedule (gap filling, minimum holding time, threshold margin for grades B and C, cell-edge margin) instead of reacting to passing clouds | Control is planned ahead at fixed time steps, so real-time cloud reactions do not apply | Some cells stay opaque slightly longer than needed |
| 2026-10-21 | All 36 issues were closed and will be recreated per sprint once owners are fixed; every issue uses the user-story format, and the story/task labels were removed | Scope and owners changed after the 10/21 meeting | Old issue numbers are not reused |
| 2026-10-21 | The requirement document (deliverable A) is stored as `docs/reports/SunSync_Requirements.pdf` | Keep the submitted version with the code | Edits are made in Word and the PDF is replaced by a new PR |
| 2026-10-21 | API paths use the `/api` prefix (for example `/api/scenes`); the `/api/v1` paths in the Google document are dropped | The implemented routes (PR #1, #2) already use `/api`, and the Google document disagrees | There is no version in the URL, so a breaking change later needs a new prefix |
