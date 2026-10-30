# FR-01 Sunpath Prediction API

이 문서는 현재 구현된 FR-01.4 API(v0.3.0)를 설명한다. 저장된 FR-06 `Scene`의 보호면 sample point에 대해 기하학적 direct-sun path와 직접일사량을 예측한다. API는 외부 provider HTTP를 호출하지 않으며, DNI는 timestamp별로 `Scene.weather` → durable Open-Meteo hourly snapshot → pvlib clear-sky 순서로 선택한다. 창/필름 광학, 반사·굴절, 실외 차폐, 확산광 및 FR-02 제어는 적용하지 않는다.

Open-Meteo snapshot 수집은 별도 worker의 책임이며, 선택·시간 구간·provenance 규칙은 [현행 FR-01.4 설계](fr01_4_open_meteo_dni_design.md)를 따른다.

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
  "protected_region_ids": ["region_A"],
  "expected_scene_revision": 2
}
```

| 필드 | 타입 | 기본값 | 설명 |
|---|---|---:|---|
| `start_time` | string | - | 필수. timezone offset이 있는 ISO 8601 timestamp |
| `duration_minutes` | integer | 120 | 1..1440 |
| `interval_seconds` | integer | 300 | 1..3600 |
| `protected_region_ids` | string array 또는 null | null | 생략/null이면 모든 region, 빈 배열·중복 ID·unknown ID는 422 |
| `expected_scene_revision` | integer 또는 null | null | 지정 시 현재 Scene revision과 같아야 함 |

입력과 출력 timestamp는 내부적으로 UTC instant로 정규화된다. grid는 시작·종료를 포함하며, 종료 시각이 interval의 배수가 아니어도 마지막 timestamp로 포함한다.

## Response

성공 시 HTTP `200`과 다음 구조를 반환한다.

```json
{
  "result_version": "0.3.0",
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
| `regions[]` | `region_id`, `aggregation`, `first_predicted_exposure_time`, `exposed_at_start`, `timeline` | 한 FR-06 protected region의 결과 |
| `timeline[]` | `timestamp`, `status`, `exposed_sample_count`, `samples` | 한 timestamp의 region 집계와 모든 sample 결과 |
| `samples[]` | `sample_index`, `point_m`, `status`, `reason`, `candidate_path`, `blocker`, `direct_irradiance_W_m2` | 개별 sample path 진단 및 표면 직접일사량. 기하 비노출은 `0`, clear path인데 DNI 미상은 `null` |
| `candidate_path` | `window_id`, `window_hit_m`, `sample_to_window_distance_m`, `reverse_direction_enu`, `incoming_direction_enu` | `clear_path`일 때의 유효 window path, 그 외 `null` |
| `blocker` | `kind`, `object_id`, `hit_m`, `distance_m` | blocker가 확인된 경우의 정보, 그 외 `null` |

`status`는 `exposed` 또는 `not_exposed`다. `reason`은 `clear_path`, `sun_below_horizon`, `back_facing`, `no_window`, `window_missed`, `boundary_ambiguous`, `room_blocked`, `occluder_blocked` 중 하나다.

`irradiance[]`는 `timestamps[]`와 길이·순서·timestamp가 정확히 일치한다. Open-Meteo 선택 entry는 `nowcast_run_id`, `nowcast_horizon_seconds`, `clear_sky_dni_W_m2`, `clear_sky_index`, `source_snapshots[]`와 request/grid coordinate·model provenance를 추가로 제공한다. hourly DNI는 `valid_time`이 가리키는 `[interval_start, interval_end)`에만 적용한다. `dni_W_m2=0`은 유효한 물리값이고 `null`은 미상으로 다음 source의 fallback 대상이다. `fallback_reason`은 `scene_weather_missing`, `scene_weather_stale`, `scene_weather_invalid`, `scene_weather_out_of_coverage`, `open_meteo_out_of_coverage`, `clear_sky_error` 중 하나 또는 `null`이다.

각 `timeline` entry는 선택한 region의 모든 `ProtectedRegion.sample_points_m`을 같은 timestamp에 평가한다. `exposed_sample_count`는 `samples[].status == "exposed"` 개수이며, region `status`는 하나라도 노출되면 `exposed`인 `any_sample` 집계다. `first_predicted_exposure_time`은 연속시간의 최초 도달 시각이 아니라 timeline에서 처음 `exposed`인 sampled timestamp다.

## 오류

| HTTP | 조건 |
|---:|---|
| 404 | Scene이 없음 |
| 409 | `expected_scene_revision`이 현재 revision과 다름 |
| 422 | request validation 실패, unknown/duplicate region, invalid stored Scene geometry, sample/resource limit 초과 |

## curl 예시

```bash
curl -sS -X POST http://localhost:8000/api/scenes/scene_demo_001/sunpath-predictions \
  -H 'Content-Type: application/json' \
  --data '{
    "start_time": "2026-10-17T10:30:00+09:00",
    "duration_minutes": 120,
    "interval_seconds": 600,
    "protected_region_ids": ["region_A"],
    "expected_scene_revision": 2
  }'
```

계산 방식, mock validation, 알려진 제한사항은 [fr01_design.md](fr01_design.md)를 참고한다.
