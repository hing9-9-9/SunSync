# FR-01 / FR-06 Mock 데이터 검증 계획

## 목적

FR-06 공간 데이터 저장과 FR-01 태양 경로 예측을 사람이 읽을 수 있는 CLI 출력으로 검증한다. 이 계획의 mock Scene, 실행 보조 코드, 출력 파일은 제품 기능 코드와 명확히 분리하고, 검증이 끝나면 한 경로 단위로 안전하게 제거할 수 있어야 한다.

이 계획은 테스트 실행, API 서버 실행, mock 파일 생성 또는 기존 코드 변경을 포함하지 않는다.

## 분리 원칙

- 제품 코드인 `backend/app/`, `engine/fr01/`, `frontend/`에는 mock 전용 분기, fixture 경로, CLI 출력 코드를 넣지 않는다.
- FR-06 `Scene` schema, `SceneRecord`, 기존 API endpoint와 FR-01 prediction endpoint의 계약을 바꾸지 않는다.
- mock 데이터는 실제 측정 데이터처럼 보이는 이름을 사용하지 않는다.
- 출력물은 Git 추적 대상이 아니어야 하며, 하나의 전용 디렉터리만 삭제하면 정리할 수 있어야 한다.
- 기존 `docs/examples/`는 FR-06의 공식 계약 예제이므로 새 smoke-test fixture를 추가하지 않는다.

## 제안 디렉터리 구조

```text
tools/
  mock_validation/
    README.md
    run_fr01_mock_validation.py
    format_report.py
    fixtures/
      mock_scene_south_window.json
      mock_scene_occluder_blocked.json
      mock_scene_no_exposure.json
      mock_scene_invalid_geometry.json

artifacts/
  mock_validation/
    .gitkeep
    <run-id>/
      request.json
      response.json
      report.txt
      stderr.txt
```

`tools/mock_validation/`은 삭제 가능한 검증 도구와 입력 fixture만 둔다. `artifacts/mock_validation/`은 실행 결과 전용이며 `.gitignore`에 등록한다. `artifacts/mock_validation/<run-id>/`의 `<run-id>`는 UTC 기준 `YYYYMMDDTHHMMSSZ` 형식을 사용한다.

## 이름 규칙

### 파일과 디렉터리

- mock 관련 파일은 반드시 `mock_` 또는 `mock_validation` 접두사를 사용한다.
- fixture 파일명은 `mock_scene_<scenario>.json` 형식을 사용한다.
- 검증 script는 `run_fr01_mock_validation.py`처럼 동작과 mock 목적을 함께 드러낸다.
- 제품 코드 아래에 `mock.py`, `demo.py`, `test_data.py` 같은 모호한 파일명을 만들지 않는다.

### Scene 식별자와 객체 ID

- `scene_id`: `mock_scene_<scenario>`
- `room_id`: `mock_room_<scenario>`
- `window_id`: `mock_window_<orientation>`
- `occluder_id`: `mock_occluder_<purpose>`
- `region_id`: `mock_region_<purpose>`
- `policy_id`, `weather_id`, `scenario_id`, `observation_id`: 모두 `mock_` 접두사 사용

예:

```json
{
  "scene_id": "mock_scene_south_window",
  "room": {"room_id": "mock_room_main"},
  "windows": [{"window_id": "mock_window_south"}],
  "protected_regions": [{"region_id": "mock_region_target"}]
}
```

## Fixture 시나리오

모든 fixture는 FR-06 `Scene` schema를 통과해야 한다. geometry는 작은 정수 또는 단순 소수 좌표를 사용해 ray path를 손으로 재검증할 수 있게 만든다.

| Fixture | 목적 | 기대 FR-01 결과 |
|---|---|---|
| `mock_scene_south_window.json` | 남향 창과 보호면의 단순한 직사광선 경로 | 적어도 하나의 sample에서 `clear_path`, `window_hit_m` 확인 |
| `mock_scene_occluder_blocked.json` | 창과 보호면 사이 cuboid | 대상 sample에서 `occluder_blocked`, blocker ID 확인 |
| `mock_scene_no_exposure.json` | 창 없음 또는 back-facing 보호면 | 모든 timestamp에서 미노출, 첫 노출 null |
| `mock_scene_invalid_geometry.json` | 검증 불가 input | FR-06 저장/검증 단계에서 422 확인. DB에 저장하지 않음 |

weather, DNI, scenario는 FR-01 기하 경로 계산에 필요하지 않으므로 기본 fixture에서는 생략한다. 필요하면 별도 fixture를 만들되 이름에 `with_weather`를 포함한다.

## 실행 방식

검증 script는 제품 서버를 별도 프로세스로 띄우지 않고, `app.main.create_app()`과 임시 SQLite database를 사용한다. 이를 통해 로컬 `sunsync.db`를 생성·수정하지 않는다.

각 run은 다음 순서로 수행하도록 설계한다.

1. run 전용 artifact 디렉터리를 만든다.
2. fixture JSON을 `POST /api/scenes/validate`로 검증한다.
3. 유효 fixture만 `POST /api/scenes`로 임시 DB에 저장한다.
4. `POST /api/scenes/{scene_id}/sunpath-predictions`를 호출한다.
5. request, 원본 response JSON, 사람이 읽는 `report.txt`를 artifact에 기록한다.
6. 예측 전후 `GET /api/scenes/{scene_id}`를 비교해 Scene JSON과 revision이 변하지 않았는지 검증한다.
7. script 종료 시 임시 DB를 dispose하고 삭제한다.

CLI는 기본적으로 종료 코드 0/1을 반환한다. `--verbose`일 때만 sample별 ray detail을 출력한다. JSON 응답은 stdout에 섞지 않고 `response.json`에 기록한다.

## 사람이 읽는 출력 형식

`report.txt`와 stdout 기본 출력은 제품 입력의 전체 dump가 아니라 계산 결과 중심으로 구성한다.

```text
FR-01 Mock Validation: PASS
Run: 20261017T030000Z
Fixture: mock_scene_south_window.json

Prediction result
  timestamps evaluated: 25
  selected regions:     1

First predicted exposure
  region:    mock_region_target
  timestamp: 2026-10-17T03:10:00+00:00
  sample:    0
  window:    mock_window_south
  hit:       (3.00, 0.00, 2.10) m

Exposure periods
  EXPOSED      12:10–12:35 KST
  NOT_EXPOSED  12:40–14:00 KST

Diagnostics
  clear_path:        6
  occluder_blocked:  0
  room_blocked:      0
  window_missed:    19

Persistence check: PASS (scene revision unchanged)
```

`--verbose` 출력에는 timestamp별 `status`, `reason`, `sample_index`, `window_id`, `window_hit_m`, blocker ID와 좌표만 추가한다. 실제 endpoint의 모든 JSON field를 반복 출력하지 않는다.

## 확인 항목

- FR-06 validation이 유효 Scene을 허용하고 invalid fixture를 저장하지 않는지
- Scene 저장·조회 결과가 fixture와 동일한지
- FR-01 응답의 timestamp 수, UTC timestamp, 태양 position 수가 일치하는지
- `clear_path`의 `window_id`, `window_hit_m`, 방향 벡터가 기대 fixture와 맞는지
- `occluder_blocked`의 blocker ID와 근접 교점이 맞는지
- `first_predicted_exposure_time`이 첫 `exposed` sample timestamp와 일치하는지
- 노출이 없는 fixture에서 첫 노출이 null인지
- 예측 호출이 Scene revision과 저장 JSON을 바꾸지 않는지
- invalid request, unknown region, revision mismatch가 422/409으로 처리되는지

## 정리 계획

검증 종료 후 제거 대상은 다음뿐이다.

```text
tools/mock_validation/
artifacts/mock_validation/
docs/fr01_mock_test_plan.md
```

이 범위 밖의 `backend/app/`, `engine/fr01/`, FR-06 fixture, production API contract는 mock 검증 작업에서 변경하지 않는 것이 원칙이다. mock 검증 도구를 삭제하기 전에는 artifact 중 보존할 보고서가 있는지 확인하고, 필요하면 `artifacts/mock_validation/` 밖으로 명시적으로 복사한다.
