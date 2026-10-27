# Mock validation tools

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

`mock_scene_seoul_multi_sample_temporal`은 서울시청 인근의 합성 Scene에서 세 `sample_points_m`을 2시간 동안 10분 간격으로 평가한다. `--verbose`를 지정하면 timestamp별 태양 위치, 노출 sample 수, sample별 `candidate_path` 또는 차폐 사유를 출력한다.

```powershell
$env:PYTHONPATH = "backend"
python tools/mock_validation/run_fr01_mock_validation.py --scenario mock_scene_seoul_multi_sample_temporal --verbose
```
