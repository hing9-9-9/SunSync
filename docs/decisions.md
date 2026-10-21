# Decisions

One line per decision. Add a row when the team makes a decision that affects scope, design or numbering.

| Date | Decision | Why | Trade-off |
|---|---|---|---|
| 2026-10-14 | Backend FastAPI, DB SQLite + SQLModel, Frontend React + Vite | Calculation libraries are Python; zero-install DB | SQLite is weak for concurrent writes; move to PostgreSQL if deployed |
| 2026-10-14 | Software only: no physical film, sensors or appliance control | Schedule and budget | No hardware demonstration |
| 2026-10-14 | Meeting notes are kept in a shared document, not in the repository | Easier for everyone to edit | No history in the repository |
| 2026-10-15 | Space model uses ENU coordinates (x east, y north, z up, metres) with Scene v0.2.0 | Common format for prediction, measurement and UI (PR #1) | Every module must convert to ENU |
| 2026-10-21 | Protection grades A-D follow the PR #1 definition | Already implemented and tested | Grades B and C need an intensity estimate |
| 2026-10-21 | The film is a virtual device with fine-grained, independently switchable cells | Hardware specifications are not public | Hardware feasibility is not verified |
| 2026-10-21 | Without weather forecasts, sunlight intensity is estimated under clear-sky conditions | Works without external data | May shade unnecessarily on cloudy days |
| 2026-10-21 | Spatial measurement uses a QR marker shown on the window film, video capture and VO/SLAM | Measures furniture as well as the room (Daeun's design) | High implementation difficulty |
| 2026-10-21 | FR renumbering: FR-03 = QR measurement, FR-05 = film control interface; the former FR-03, FR-05, FR-07 and FR-08 are removed and will be added later with new numbers | Reflect new functions and keep this term's list to FR-01 to FR-06 | FR-03 means something different in older documents |
| 2026-10-21 | Main scenario is a library reading room; the space model stays general | Clearest motivation and evidence | Other spaces are not tested this term |
