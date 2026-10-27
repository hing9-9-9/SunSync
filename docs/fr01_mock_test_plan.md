# FR-01 / FR-06 Mock Validation 계획

## 목적과 범위

이 계획은 FR-06 Scene 저장·검증과 FR-01 기하학적 직사광선 예측을 mock fixture로 end-to-end 확인하기 위한 것이다. 검증 도구는 production API 서버와 `sunsync.db`를 사용하지 않는다.

대상은 FR-06 Scene validation/storage, FR-01.4 prediction timeline·sample diagnostic·window hit·blocker·최초 sampled exposure, 그리고 예측 전후 Scene JSON/revision 불변성이다. Stage 1은 timestamp별 DNI/provenance와 표본점별 직접일사량도 검증한다. 창/필름 광학, reflection/refraction, outdoor shading, 확산광 및 FR-02 제어는 대상이 아니다.

## 확인된 실제 계약

| 항목 | 확인된 값 |
|---|---|
| App factory | `app.main.create_app(database_engine: Engine | None = None)` |
| DB 격리 | 전달한 engine으로 `init_db()` 실행 후 `get_session` dependency override |
| Scene 검증 | `POST /api/scenes/validate`, 성공 200, 저장하지 않음 |
| Scene 생성 | `POST /api/scenes`, 성공 201, 중복 ID 409 |
| Prediction | `POST /api/scenes/{scene_id}/sunpath-predictions`, 성공 200 |
| Prediction request | `start_time` 필수, `duration_minutes`, `interval_seconds`, 선택 `protected_region_ids`, `expected_scene_revision` |
| Unknown region | 422 |
| Revision mismatch | 409 |
| Missing scene | 404 |
| Response | `timestamps`, `solar_positions`, `regions[].timeline[].samples[]`, `candidate_path`, `blocker`, `first_predicted_exposure_time` |

runner는 `tempfile.TemporaryDirectory()` 아래의 고유 SQLite file로 engine을 만들고, 반드시 `with TestClient(create_app(engine))` 안에서만 사용한다. `app.db.engine`, `DATABASE_URL`, `sunsync.db`를 참조·수정하지 않는다.

## 디렉터리와 삭제 경계

```text
tools/
  mock_validation/
    README.md
    run_fr01_mock_validation.py
    fixtures/
      valid/
        mock_scene_clear_path.json
        mock_scene_occluder_blocked.json
        mock_scene_no_exposure.json
        mock_scene_seoul_multi_sample_temporal.json
      invalid/
        mock_scene_invalid_window_geometry.json

artifacts/
  mock_validation/
    .gitkeep
    <utc-run-id>-<uuid8>/
      summary.json
      report.txt
      <scenario>/
        request.json
        response.json
        scene_before.json
        scene_after.json
        failure.txt
```

`tools/mock_validation/`, `artifacts/mock_validation/`, 이 문서는 mock validation의 전체 삭제 경계다. production source와 `docs/examples/`에는 mock fixture나 mock 분기를 추가하지 않는다.

`artifacts/mock_validation/*`는 Git에서 무시하고 `.gitkeep`만 추적한다. run ID는 `YYYYMMDDTHHMMSSZ-<uuid8>` 형식이다. 이미 존재하는 run directory는 덮어쓰지 않고 실패한다.

## Fixture 분리와 이름 규칙

- 모든 mock 식별자와 파일명은 `mock_` 접두사를 사용한다.
- valid fixture는 `fixtures/valid/`에만 둔다.
- invalid fixture는 `fixtures/invalid/`에만 둔다.
- invalid fixture는 validate endpoint에서 422가 나와야 하며, runner는 create endpoint를 호출하지 않는다.
- valid fixture만 validate 200 이후 create 201을 호출한다.

예시 ID: `mock_scene_clear_path`, `mock_room_main`, `mock_window_south`, `mock_occluder_blocker`, `mock_region_target`.

## 독립 expected oracle

endpoint가 반환한 `solar_positions`, `candidate_path`, `blocker`로 expected 값을 계산하거나 갱신하지 않는다. 각 valid fixture의 별도 expected spec에는 다음을 고정한다.

1. 공개 SPA/NOAA reference의 timestamp, site, elevation, azimuth
2. 그 독립 태양 방향과 fixture의 명시적 plane/cuboid 방정식으로 사전 계산한 교점
3. 기대 exposure status/reason, window 또는 blocker ID, 최초 exposure timestamp

| 비교 | Tolerance |
|---|---:|
| solar elevation | ±0.10 degree |
| solar azimuth | ±0.10 degree |
| ENU vector component | ±0.002 |
| window/blocker hit coordinate | ±0.02 m |
| ray distance | ±0.02 m |
| exposure timestamp | 정확히 같은 ISO 8601 instant |

Tolerance는 결과를 본 뒤 변경하지 않는다.

## Scenario

| Fixture | 독립 expected 결과 |
|---|---|
| `mock_scene_clear_path.json` | 단일 sample, `exposed` / `clear_path`, 지정 window ID, 사전 계산한 `window_hit_m`, 첫 grid timestamp와 같은 최초 exposure |
| `mock_scene_occluder_blocked.json` | 같은 ray 중간의 cuboid, `not_exposed` / `occluder_blocked`, 지정 blocker ID/교점, 최초 exposure null |
| `mock_scene_no_exposure.json` | 공개 reference에서 태양이 지평선 아래인 time, 모든 sample `sun_below_horizon`, 최초 exposure null |
| `mock_scene_seoul_multi_sample_temporal.json` | 서울시청 인근 site, 2시간·10분 간격의 수직 보호면 3 sample. 13개 timestamp, 모든 sample의 결과·집계 count·최초 노출 시각을 확인하고, 창 높이 경계로 노출 sample 집합이 변하는지를 기하학적 불변조건으로 검증 |
| `mock_scene_invalid_window_geometry.json` | 직사각형이 아닌 window, validate 422, persistence 호출 없음, DB record 없음 |

Endpoint의 `duration_minutes` 최소값은 1이고 grid는 종료 시각을 포함하므로, 각 geometry scenario는 60초 간격의 두 timestamp를 사용한다. oracle은 시작 timestamp의 고정 reference와 최초 exposure를 비교하고, 두 번째 timestamp는 response 구조·상태 일관성만 확인한다. 기본 range·여러 timestamp contract 검증은 별도의 request-contract scenario에서 수행한다.

다중 sample scenario는 API가 반환한 태양 위치로 expected 값을 재계산하지 않는다. 독립적인 geometry invariant로 (1) 요청한 13개 UTC timestamp와 정확한 600초 간격, (2) timestamp당 세 sample의 stable index, (3) `exposed_sample_count`와 sample status의 일치, (4) region `any_sample` 집계, (5) 노출 ray의 south-window plane 교점과 aperture 내부 조건, (6) 낮은 z sample부터만 노출되는 prefix, (7) 2시간 동안 노출 sample 수 변화, (8) 시작 timestamp와 같은 최초 sampled exposure를 확인한다.

## Runner 절차

1. 고유 temporary SQLite engine과 artifact run directory를 만든다.
2. `with TestClient(create_app(engine))`에서 scenario를 실행한다.
3. valid fixture는 validate 200 → create 201 → prediction 200 → Scene before/after equality 순서다.
4. invalid fixture는 validate 422와 DB 무기록만 확인한다.
5. actual API로 unknown region 422, duplicate region 422, revision mismatch 409, missing scene 404를 확인한다.
6. request/response 원문은 artifact에, oracle 비교는 `summary.json`과 `report.txt`에 기록한다.
7. 한 scenario 실패가 다른 scenario를 막지 않는다. 하나라도 실패하면 exit 1이다.
8. temporary SQLite directory는 종료 시 제거한다. artifact만 남긴다.

## 출력과 실패 분류

| 결과 | 의미 |
|---|---|
| `PASS` | API contract와 독립 expected oracle 모두 일치 |
| `SETUP_FAIL` | fixture read, artifact 생성, temporary SQLite/TestClient 준비 실패 |
| `API_FAIL` | HTTP status 또는 response 구조 불일치 |
| `NUMERICAL_FAIL` | solar, hit coordinate, blocker, exposure timestamp가 tolerance 밖 |

```text
FR-01 / FR-06 mock validation
run: 20261017T030000Z-a1b2c3d4
database: temporary isolated SQLite

PASS           mock_scene_clear_path
  status: exposed / clear_path
  window: mock_window_south
  hit: expected=(4.74436, 0.00, 1.86266), actual=(4.74436, 0.00, 1.86216), tolerance=0.02 m

PASS           mock_scene_invalid_window_geometry
  validate: 422
  persisted: false

Summary: 4 PASS, 0 SETUP_FAIL, 0 API_FAIL, 0 NUMERICAL_FAIL
```

## 실행 명령

```powershell
$env:PYTHONPATH = "backend"
python tools/mock_validation/run_fr01_mock_validation.py
python tools/mock_validation/run_fr01_mock_validation.py --scenario mock_scene_clear_path --verbose

python -m pytest -q
python -m ruff check backend/app engine/fr01 tests tools/mock_validation
python -m ruff format --check backend/app engine/fr01 tests tools/mock_validation
python -m mypy backend/app engine/fr01
```

## Production defect 보고 규칙

runner 구현 중 defect가 발견되면 production source를 수정하지 않는다. 파일/함수, 재현 fixture, actual/expected response 또는 수치, 영향 범위, production 수정 필요 이유를 별도로 보고한다.
