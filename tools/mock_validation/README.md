`run_fr01_mock_validation.py` validates FR-06 storage and FR-01 prediction through
an isolated `TestClient` and temporary SQLite database. It never starts Uvicorn,
never opens the repository's `sunsync.db`, and mocks the Open-Meteo adapter so the
validation does not depend on external network availability.

Run from the repository root:

```powershell
$env:PYTHONPATH = "backend"
python tools/mock_validation/run_fr01_mock_validation.py
```

Artifacts are written beneath `artifacts/mock_validation/` and ignored by Git.

`mock_scene_seoul_multi_sample_temporal` evaluates the protected rectangle's
automatically generated 5 cm tile centres for two hours at 10-minute intervals.
With `--verbose`, it prints the sun position, exposed tile count, and each tile's
`candidate_path` or non-exposure reason at every timestamp.

```powershell
$env:PYTHONPATH = "backend"
python tools/mock_validation/run_fr01_mock_validation.py --scenario mock_scene_seoul_multi_sample_temporal --verbose
```
