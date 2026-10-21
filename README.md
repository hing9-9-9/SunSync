# SunSync

Predicts future sunlight and switches only the needed cells of a smart film to protect designated targets while keeping daylight.

## Structure
```
backend/   FastAPI + SQLModel + SQLite
frontend/  React + Vite (TypeScript)
engine/    sunlight calculation (FR-01)
measure/   ArUco measurement (research)
docs/      architecture, decisions (meeting notes: shared doc)
```

## Setup
Requirements: Python 3.11+, Node.js LTS

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
[FR06 guide](docs/fr06.md), [complete synthetic example](docs/examples/fr06_scene_example.json),
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

Frontend verification: `cd frontend && npm ci && npm run lint && npm run build`.
GitHub Actions runs the backend checks on Python 3.11/3.12 and the frontend checks.

### FR-01 future sunlight path prediction

FR-01 predicts geometric direct-sun paths from a stored FR-06 Scene through
`POST /api/scenes/{scene_id}/sunpath-predictions`. It evaluates every selected
`ProtectedRegion.sample_points_m` at each requested timestamp and returns solar
positions, sample-level window hits or blockers, exposure counts, and the first
sampled exposure timestamp. See the [FR-01 MVP guide](docs/fr01_design.md) and
[API contract](docs/fr01_api.md).

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
