# SunSync

Predicts future sunlight and switches only the needed cells of a smart film to protect designated targets while keeping daylight.

## Structure
```
backend/   FastAPI + SQLModel + SQLite
frontend/  React + Vite (TypeScript)
engine/    sunlight prediction (FR-01), shading plan (FR-02), stable control (FR-04), Vision AI
measure/   ArUco measurement server (/measure) and its registration core
mobile/    native camera adapters (Android, iOS)
deploy/    server files: Caddy, systemd units, update, backup
tools/     experiments, mock validation and dummy data scripts
tests/     backend tests
docs/      requirements, architecture, decisions, data contracts, reports
```

Guided furniture registration: [measurement core and API](measure/registration-core/README.md),
[mobile integration contract](measure/registration-core/integration/CONTRACT.md).

Native camera adapters and automatic camera setup: [mobile/README.md](mobile/README.md).
Browser photo capture is an opt-in development test and is disabled in production builds.

## Documents
- [Functional requirements](docs/features.md): FR-01 to FR-07, owners and sub-requirements
- [Decisions](docs/decisions.md): one line per team decision
- [Architecture](docs/architecture.md): modules and data flow
- [FR-06 space data](docs/fr06_design.md) and [JSON schema](docs/fr06_scene_schema.json)
- FR-01 sunlight prediction: [FR-01.1 pvlib path design](docs/fr01_1_pvlib_design.md), [FR-01.4 KMA AI implementation guide](docs/fr01_4_README.md), [FR-01.4 Open-Meteo DNI design](docs/fr01_4_open_meteo_dni_design.md), [prediction errors (FR-01-7)](docs/fr01_7_prediction_error_implementation.md), [large spaces (FR-01-8)](docs/fr01_8_large_space_implementation.md), [API](docs/fr01_api.md), [FR-01 → FR-02 tile contract](docs/fr01_fr02_tile_contract.md), [mock test plan](docs/fr01_mock_test_plan.md)
- FR-02 & FR-04 shading plan & adaptive control: [format](docs/fr02_schedule.md), [design](docs/fr02_design.md), [API](docs/fr02_api.md), [FR-04 uncertainty-aware adaptive control & LG SGF](docs/fr04_adaptive_control_lg_sgf.md)
- FR-03 measurement: [single 60 cm marker](docs/fr03_single_marker.md), [measurement workflow](docs/fr03_measurement_workflow.md), [robust estimation](docs/fr03_robust_measurement.md), [selected film](docs/fr03_selected_film.md)
- FR-03 & FR-06 Vision AI 3D furniture estimation: [ArUco-calibrated depth & 3D bounding box pipeline](docs/fr03_vision_ai_3d_estimation.md)
- FR-05 film control: [protocol](docs/fr05_protocol.md)
- [Database](docs/db.md): tables, migrations and persistence
- [UI flow](docs/ui-flow.md): screens, login and the APIs they call
- [Deployment on one server](docs/deploy.md): https address, services, backup
- [Google login](docs/auth.md): how it works, the settings, the Google console and how to switch it on
- [AI & Applications Proposal](docs/ai_applications/README.md): Project proposal blog for ITE 351 (Task 1 & Task 3)
- Requirement document: `docs/reports/SunSync_Requirements.pdf`
- Meeting notes: shared Google document (not in the repository)

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
API docs: http://localhost:8000/docs

### FR06 space data

The backend validates and stores versioned Room, Window, cuboid Occluder and
ProtectedRegion data through `/api/scenes`. Manual input and ArUco floor-point
exports share the same ENU JSON contract. Start with the
[FR06 guide](docs/fr06_design.md), [complete synthetic example](docs/examples/fr06_scene_example.json),
or [room-only example](docs/examples/fr06_manual_room.json).

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

Frontend verification: `cd frontend && npm ci && npm test && npm run lint && npm run build`.
GitHub Actions runs the backend checks on Python 3.11/3.12 and the frontend checks.

### FR-01 future sunlight path prediction

FR-01 predicts geometric direct-sun paths from a stored FR-06 Scene through
`POST /api/scenes/{scene_id}/sunpath-predictions`. It deterministically divides
each selected rectangular `ProtectedRegion` into 5 cm tiles, evaluates every
tile centre at each requested timestamp, and returns solar positions, tile-level
window hits or blockers, exposure counts, sampled exposure intervals, and the
first sampled exposure timestamp. See the [FR-01.1 pvlib path design](docs/fr01_1_pvlib_design.md)
and [API contract](docs/fr01_api.md).

Run the isolated mock validation from the repository root:

```powershell
$env:PYTHONPATH = "backend"
python tools/mock_validation/run_fr01_mock_validation.py
python tools/mock_validation/run_fr01_mock_validation.py --scenario mock_scene_seoul_multi_sample_temporal --verbose
```

The mock runner uses a temporary SQLite database and does not open `sunsync.db`.

### Frontend
```bash
cd frontend
npm install
npm run dev
```
App: http://localhost:5173

## Git rules
- Branch: `feature/<short-name>` from `main`, merge by PR (1 reviewer)
- Commit: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`
