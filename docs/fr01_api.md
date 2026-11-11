# FR-01 Sunpath Prediction API

이 문서는 현재 구현된 FR-01.4 API(v0.4.0)를 설명한다. 저장된 FR-06 `Scene`의 직사각형 보호영역을 자동 생성한 5 cm 타일 중심점에 대해 기하학적 direct-sun path와 직접일사량을 예측한다. API는 외부 provider HTTP를 호출하지 않으며, DNI는 timestamp별로 `Scene.weather` → durable Open-Meteo hourly snapshot → pvlib clear-sky 순서로 선택한다. 창/필름 광학, 반사·굴절, 실외 차폐, 확산광 및 FR-02 제어는 적용하지 않는다.

Open-Meteo snapshot 수집은 별도 worker의 책임이며, 선택·시간 구간·provenance 규칙은 [현행 FR-01.4 설계](fr01_4_open_meteo_dni_design.md)를 따른다.

## KMA AI operational nowcast

```text
GET /api/scenes/{scene_id}/ai-nowcast/latest
```

이 endpoint는 background worker가 미리 계산한 최신 10·30·60분 결과를 읽기만 한다.
외부 provider 호출, 모델 학습, 요청 시 추론은 수행하지 않는다. 기본 shadow 운영에서는
세 horizon 모두 Open-Meteo가 사용자 값이며 AI 결과는 내부 shadow 필드에만 저장된다.
active 전환 후에는 10·30분에 동결 V1의 P10·P50·P90을 제공하고 60분은 Open-Meteo를
유지한다. KMA가 20분보다 오래됐거나 필수값이 없으면 Open-Meteo로 fallback하며,
Open-Meteo도 없으면 `prediction_source=unavailable`, `dni_W_m2=null`을 반환한다.

응답은 `rollout_mode`, `model_version`, `model_manifest_sha256`, `prediction_origin`,
입력 snapshot ID·관측/발행 시각, `fallback_reason`, `quality_flags`를 포함해 재현 가능하다.
scene이 없으면 404, 아직 유효한 worker 결과가 없으면 503이다. 구형
`GET /nowcast-comparison`은 deprecated alias이며 POST 입력 API는 존재하지 않는다.

## Endpoint

```text
POST /api/scenes/{scene_id}/sunpath-predictions
```

`scene_id`는 이미 `POST /api/scenes`로 저장된 FR-06 Scene ID여야 한다.

## Request

```json
{
  "start_time": "2026-10-17T10:30:00+09:00",
  "duration_minutes": 120,
  "interval_seconds": 600,
  "detail_level": "summary",
  "protected_region_ids": ["region_A"],
  "expected_scene_revision": 2
}
```

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---:|---|
| `start_time` | string | - | 필수. timezone offset이 있는 ISO 8601 timestamp |
| `duration_minutes` | integer | 120 | 1..1440 |
| `interval_seconds` | integer | 300 | 1..3600 |
| `detail_level` | `summary` 또는 `diagnostic` | `summary` | 기본 응답은 집계만 반환한다. `diagnostic`은 모든 sample 경로 정보를 추가한다. |
| `protected_region_ids` | string array 또는 null | null | 생략/null이면 모든 region, 빈 배열·중복 ID·unknown ID는 422 |
| `expected_scene_revision` | integer 또는 null | null | 지정 시 현재 Scene revision과 같아야 함 |

입력과 출력 timestamp는 내부적으로 UTC instant로 정규화된다. grid는 시작·종료를 포함하며, 종료 시각이 interval의 배수가 아니어도 마지막 timestamp로 포함한다.

## Response

성공 시 HTTP `200`과 다음 구조를 반환한다.

```json
{
  "result_version": "0.4.0",
  "scene_id": "scene_demo_001",
  "scene_revision": 2,
  "coordinate_frame": "ENU",
  "units": "m",
  "exposure_basis": "geometric_direct_path",
  "optics_applied": false,
  "start_time": "2026-10-17T01:30:00Z",
  "end_time": "2026-10-17T03:30:00Z",
  "interval_seconds": 600,
  "timestamps": ["..."],
  "solar_positions": ["..."],
  "irradiance": ["..."],
  "regions": ["..."],
  "assumptions": ["..."]
}
```

| 구조 | 주요 필드 | 의미 |
|---|---|---|
| `solar_positions[]` | `timestamp`, `elevation_deg`, `azimuth_deg_true`, `sun_direction_enu` | geometric solar position과 sample에서 태양을 향하는 ENU vector |
| `irradiance[]` | `timestamp`, `baseline_dni_W_m2`, `final_dni_W_m2`, `source`, `fallback_used`, `fallback_reason`, `valid_time`, `interval_start`, `interval_end`, `fetched_at` | timestamp별 DNI와 선택 provenance |
| `regions[]` | `region_id`, `aggregation`, `first_predicted_exposure_time`, `exposed_at_start`, `exposure_intervals`, `timeline` | 한 FR-06 protected region의 결과 |
| `timeline[]` | `timestamp`, `status`, `sample_count`, `exposed_sample_count`, `max_direct_irradiance_W_m2` | 한 timestamp의 region 집계. summary에는 이 필드들만 들어간다. |
| `samples[]` | `sample_index`, `point_m`, `status`, `reason`, `candidate_path`, `blocker`, `direct_irradiance_W_m2` | `detail_level=diagnostic`에서만 제공하는 개별 sample path 진단. 기하 비노출은 `0`, clear path인데 DNI 미상은 `null` |
| `candidate_path` | `window_id`, `window_hit_m`, `sample_to_window_distance_m`, `reverse_direction_enu`, `incoming_direction_enu` | `clear_path`일 때의 유효 window path, 그 외 `null` |
| `blocker` | `kind`, `object_id`, `hit_m`, `distance_m` | blocker가 확인된 경우의 정보, 그 외 `null` |

`status`는 `exposed` 또는 `not_exposed`다. `reason`은 `clear_path`, `sun_below_horizon`, `back_facing`, `no_window`, `window_missed`, `boundary_ambiguous`, `room_blocked`, `occluder_blocked` 중 하나다.

`irradiance[]`는 `timestamps[]`와 길이·순서·timestamp가 정확히 일치한다. Open-Meteo 선택 entry는 `nowcast_run_id`, `nowcast_horizon_seconds`, `clear_sky_dni_W_m2`, `clear_sky_index`, `source_snapshots[]`와 request/grid coordinate·model provenance를 추가로 제공한다. hourly DNI는 `valid_time`이 가리키는 `[interval_start, interval_end)`에만 적용한다. `dni_W_m2=0`은 유효한 물리값이고 `null`은 미상으로 다음 source의 fallback 대상이다. `fallback_reason`은 `scene_weather_missing`, `scene_weather_stale`, `scene_weather_invalid`, `scene_weather_out_of_coverage`, `open_meteo_out_of_coverage`, `clear_sky_error` 중 하나 또는 `null`이다.

각 `timeline` entry는 선택한 region의 자동 생성 5 cm tile 중심을 같은 timestamp에 평가한다. `sample_count`는 전체 tile 수, `exposed_sample_count`는 노출 tile 수다. `max_direct_irradiance_W_m2`는 sample 최댓값이며 하나라도 DNI 미상이면 `null`이다. region `status`는 하나라도 노출되면 `exposed`인 `any_sample` 집계다. `first_predicted_exposure_time`은 연속시간의 최초 도달 시각이 아니라 timeline에서 처음 `exposed`인 sampled timestamp다. `exposure_intervals`는 이웃한 `exposed` timeline entry를 `[start_time, end_time]`으로 병합한 목록이며, 경계는 보간하지 않은 grid timestamp다.

## 계산량과 운영 목표

교차 검사 예상량은 다음 공식으로 계산한다.

```text
timestamp_count × sample_count × (window_count + occluder_count + room_corner_count + 2)
```

기본 한도는 sample 10,000개, 교차 검사 5,000,000회다. 한도를 넘으면 `INTERSECTION_LIMIT_EXCEEDED` 또는 `SAMPLE_LIMIT_EXCEEDED`와 실제값·한도를 반환한다. 사용자는 `interval_seconds`를 늘리거나 보호 영역 또는 가구 수를 줄일 수 있다.

운영 목표는 12×8×3.5 m, 서가 4개 기준 summary 응답 1 MB 이하, warm-run 중앙값 7초 이하다. 2026-11-09 Windows 개발 환경 측정 결과는 74,132 bytes, 3회 4.744~6.890초, 중앙값 5.833초였다. `tools/measure_fr01_large_space.py`로 재현할 수 있다. 서가 9개 시나리오는 예상 5,080,320회로 현재 5,000,000회 한도를 넘으며 구조화된 422를 반환한다.

## 오류

| HTTP | 조건 |
|---:|---|
| 404 | Scene이 없음. 로그인이 켜져 있으면 다른 계정의 Scene도 404이다 ([auth.md](auth.md)) |
| 409 | `expected_scene_revision`이 현재 revision과 다름 |
| 422 | request validation 실패, unknown/duplicate region, invalid stored Scene geometry, sample/resource limit 초과 |

FR-01 도메인 422의 `detail`은 `code`, 안전한 한국어 `message`, 관련 대상과 `context`를 가진 객체다. 계산 한도 context에는 예상 검사량과 timestamp/sample/window/occluder/room corner 수가 포함된다. 요청 body 자체의 Pydantic 검증 오류는 기존 detail 배열 형식을 유지한다.

## curl 예시

```bash
curl -sS -X POST http://localhost:8000/api/scenes/scene_demo_001/sunpath-predictions \
  -H 'Content-Type: application/json' \
  --data '{
    "start_time": "2026-10-17T10:30:00+09:00",
    "duration_minutes": 120,
    "interval_seconds": 600,
    "detail_level": "summary",
    "protected_region_ids": ["region_A"],
    "expected_scene_revision": 2
  }'
```

계산 방식, mock validation, 알려진 제한사항은 [FR-01.1 pvlib 경로 설계](fr01_1_pvlib_design.md)를 참고한다.
