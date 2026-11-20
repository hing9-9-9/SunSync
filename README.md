# SunSync

Predicts future sunlight and switches only the needed cells of a smart film to protect designated targets while keeping daylight.

![3D view of a room: only the film cells that block sun from the protected display case are opaque](docs/images/readme-3d-view.png)

*A sample room at 15:00. Blue film cells are opaque; the rest of the window stays clear.*

## The problem
Direct sunlight can damage things like old books or artwork. A curtain or blind protects them, but it blocks the whole window, including the daylight people want.

## The idea
Cover the window with a smart film divided into small cells that can be controlled one by one, and make only the cells that matter opaque.

1. **Measure the room.** A phone photo of one printed marker gives the size and position of the window and furniture (FR-03, FR-06).
2. **Predict the sun.** From the room's location and the weather forecast, SunSync predicts where direct sunlight falls, hour by hour (FR-01).
3. **Choose the cells.** It makes opaque only the cells that block sun from the protected object, and keeps the plan from changing needlessly (FR-02, FR-04).

The film is simulated in software (a virtual film, FR-05). Connecting a real film is future work.

## How a user uses it
1. Log in, register a room and enter the window size.
2. A marker appears on the film attached to the window. Take a photo with a phone so that the marker is in view. The size and position of the furniture are measured automatically.

   ![QR measurement guide](docs/images/readme-measure.png)

3. Choose what to protect from the recognized furniture, such as a bookshelf or a desk top.

   ![Choosing the protected furniture](docs/images/readme-protect.png)

4. SunSync predicts where sunlight will fall, hour by hour, and decides which film cells are opaque at each time. Pick a time in the 3D view to see it.

Places where daylight is welcome but direct sunlight should be avoided, such as libraries, offices and museums, could use it instead of curtains.

## Documents

**Reports**
- [Requirements report (PDF)](docs/reports/SunSync_Requirements.pdf): what the system must do (LaTeX source in `docs/reports/requirements`)
- [Specification report (PDF)](docs/reports/SunSync_Specification.pdf): how the system is designed (LaTeX source in `docs/reports/specification`)
- [AI & Applications proposal](docs/ai_applications/README.md): project proposal blog for ITE 351 (Task 1 & Task 3)
- Meeting notes: shared Google document (not in the repository)

**Overview**
- [Functional requirements](docs/features.md): FR-01 to FR-07, owners and sub-requirements
- [Decisions](docs/decisions.md): one line per team decision
- [Architecture](docs/architecture.md): modules and data flow
- [Scenario precomputation](docs/scenario-precomputation.md): how predictions and shading scenarios are computed ahead of time and cached
- [Database](docs/db.md): tables, migrations and persistence
- [UI flow](docs/ui-flow.md): screens, login and the APIs they call
- [Measurement flow with an account](docs/device-handoff.md): how a phone and a PC share one measurement session
- [Deployment on one server](docs/deploy.md): https address, services, backup
- [Google login](docs/auth.md): how it works, the settings, the Google console and how to switch it on

<details>
<summary><b>Space data and measurement (FR-06, FR-03) · 6 docs</b></summary>

- [FR-06 space data](docs/fr06_design.md) and [JSON schema](docs/fr06_scene_schema.json): how rooms, windows, furniture and protected regions are stored (examples: [full scene](docs/examples/fr06_scene_example.json), [room only](docs/examples/fr06_manual_room.json))
- [Single 60 cm marker](docs/fr03_single_marker.md): the one printed marker used for measuring
- [Measurement workflow](docs/fr03_measurement_workflow.md): how photo results become furniture in the room
- [Robust estimation](docs/fr03_robust_measurement.md): choosing good photos and fitting shapes to reduce measurement error
- [Selected film](docs/fr03_selected_film.md): connecting the film chosen for the window to the photo session
- [Vision AI 3D furniture estimation](docs/fr03_vision_ai_3d_estimation.md): estimating furniture size from a photo with the marker as a reference

</details>

<details>
<summary><b>Sunlight prediction (FR-01) · 10 docs</b></summary>

- [FR-01.1 pvlib path design](docs/fr01_1_pvlib_design.md): how the sun path into the room is calculated
- [API](docs/fr01_api.md): the request and response of the prediction endpoint
- [FR-01 to FR-02 tile contract](docs/fr01_fr02_tile_contract.md): the 5 cm tile output that the shading step reads
- [Prediction errors (FR-01-7)](docs/fr01_7_prediction_error_implementation.md): how failures are reported in a structured way
- [Large spaces (FR-01-8)](docs/fr01_8_large_space_implementation.md): response size and calculation limits for big rooms
- [FR-01.4 Open-Meteo DNI design](docs/fr01_4_open_meteo_dni_design.md): predicting future direct sunlight from forecast data
- [FR-01.4 KMA AI guide](docs/fr01_4_README.md): overview of the KMA-data AI extension
- [KMA data analysis](docs/fr01_4_ai_kma_analysis.md): record of the KMA data analysis
- [KMA AI model](docs/fr01_4_ai_model_implementation.md): the frozen specification of the direct-sunlight model
- [Mock test plan](docs/fr01_mock_test_plan.md): how prediction is checked with mock rooms

</details>

<details>
<summary><b>Shading and film (FR-02, FR-04, FR-05) · 5 docs</b></summary>

- [Shading plan format](docs/fr02_schedule.md) and [JSON schema](docs/fr02_shading_plan_schema.json): what a `ShadingPlan` contains
- [FR-02 design](docs/fr02_design.md): how the plan is built from the prediction
- [FR-02 API](docs/fr02_api.md): the request and response of the plan endpoint
- [FR-04 adaptive control & LG SGF](docs/fr04_adaptive_control_lg_sgf.md): keeping the film stable against short changes, and the LG integration guide
- [FR-05 film protocol](docs/fr05_protocol.md): how the server controls the virtual film and the simulation clock

</details>

<details>
<summary><b>Records of past work · 5 docs (not kept up to date)</b></summary>

- [UI and FR-06 integration](docs/ui-fr06-integration.md) and [UI data integration](docs/ui-data-integration.md): how the screens were first connected to saved space data (2026-11-01)
- [Award example integration](docs/award-integration.md) and [design specification](docs/design-spec-2026-10-23.md): the reference project and the screen design written in October
- [Hero image record](docs/hero-asset.md): how the introduction image was made

</details>

## Architecture
![SunSync architecture: user, frontend, backend pipeline, measurement server, workers and database](docs/images/architecture.png)

How the parts work together: [Architecture](docs/architecture.md).

## Team
| Name | Part |
|---|---|
| Misong Ha | FR-01 Future sunlight path prediction |
| Jiyeon Kim | FR-02 Selective shading decision, FR-07 Deployment and accounts |
| Daeun Lee | FR-03 QR-based spatial measurement |
| Heegyeong Chung | FR-06 Space information management |
| Dain Lee | UI (web and app screens) |

---

# For developers

## Structure
```
backend/    FastAPI + SQLModel + SQLite
frontend/   React + Vite (TypeScript)
engine/     sunlight prediction (FR-01), shading plan (FR-02), stable control (FR-04), Vision AI
measure/    ArUco measurement server (/measure) and its registration core
mobile/     native camera adapters (Android, iOS)
deploy/     server files: Caddy, systemd units, update, backup
tools/      experiments, mock validation and dummy data scripts
data/       FR-01 experiment data (fr01/)
artifacts/  output of local validation runs (not tracked by Git)
tests/      backend tests
docs/       requirements, architecture, decisions, data contracts, reports
```

Guided furniture registration: [measurement core and API](measure/registration-core/README.md),
[mobile integration contract](measure/registration-core/integration/CONTRACT.md).
Native camera adapters and automatic camera setup: [mobile/README.md](mobile/README.md).
Browser photo capture is an opt-in development test and is disabled in production builds.

## Setup
Requirements: Python 3.11+, Node.js 22 or newer (CI uses 24, the server 22)

> **Python 3.11+ is required.** On macOS, the built-in `python3` is 3.9 and will fail (`int | None` syntax). Install 3.12 (`brew install python@3.12`) and create the venv with `python3.12 -m venv .venv`.

### Backend
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cd backend
uvicorn app.main:app --reload
```
API docs: http://localhost:8000/docs. Login is off unless `SUNSYNC_AUTH=google` is set ([auth](docs/auth.md)).

### Frontend
```bash
cd frontend
npm install
npm run dev
```
App: http://localhost:5173

### Checks
From the repository root, after activating the venv:
```bash
pip install -r requirements-dev.txt
PYTHONPATH=backend python -m app.scene_cli validate docs/examples/fr06_scene_example.json
pytest -q
ruff check backend tests
ruff format --check backend tests
mypy backend/app
PYTHONPATH=backend python -m app.scene_cli schema --check
```
Frontend: `cd frontend && npm ci && npm test && npm run lint && npm run build`.
GitHub Actions runs the backend checks on Python 3.11/3.12 and the frontend checks.

Space data ([FR-06 guide](docs/fr06_design.md), [example](docs/examples/fr06_scene_example.json)),
sunlight prediction ([FR-01 API](docs/fr01_api.md)) and its mock validation
([test plan](docs/fr01_mock_test_plan.md)) are described in `docs/`.

## Git rules
- Branch: `feature/<short-name>` from `main`, merge by PR (1 reviewer)
- Commit: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`
