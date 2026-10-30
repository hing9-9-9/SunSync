# FR-01.4 Open-Meteo 실제 연동 검증 계획

## 1. 목적과 범위

이 계획은 실제 Open-Meteo Forecast API 응답이 SunSync의 production 경로를 통해 정상 처리되는지 **로컬에서만** 확인한다.

검증 대상 경로는 다음과 같다.

```text
서울시청 좌표 Scene
  → Open-Meteo worker
  → SQLite immutable weather snapshot / point
  → timestamp별 selector
  → sunpath prediction API v0.3 provenance
```

이 검증은 실제 DNI 값의 정확도나 특정 provider model의 성능을 주장하지 않는다. 검증하는 것은 외부 응답의 수집·정규화·저장·시간 구간 선택·fallback·provenance다.

현행 기능과 정책의 기준은 [FR-01.4 Open-Meteo 단일 source 미래 DNI 설계](fr01_4_open_meteo_dni_design.md)다. 결정론적 fixture 검증은 [FR-01 / FR-06 Mock Validation 계획](fr01_mock_test_plan.md)에서 계속 수행한다.

## 2. 실행 환경과 보관 정책

- 실행 위치: 개발자 로컬 환경만 허용한다.
- DB: repository의 기본 `sunsync.db`가 아닌 임시 SQLite 파일을 사용한다.
- 권장 경로: `artifacts/local/fr01_4_open_meteo/<run-id>/integration.db`
- 실제 HTTP 응답, SQLite DB, 로그 및 검사 결과는 Git에 추가하지 않는다. `*.db`와 `artifacts/local/`은 ignore 대상이어야 한다.
- 실행 도구·이 문서·검증 코드만 저장소에 둔다.
- 성공 후 DB는 다음 중 하나로 처리한다.
  - 단발성 연결 확인: 삭제한다.
  - 결함 조사·재현 필요: 로컬 artifact로 보관하고, 보관 사유와 실행 시각을 결과 요약에 기록한다.

각 실행은 UTC 기준 `<YYYYMMDDTHHMMSSZ>-<uuid8>` run ID를 사용한다. 이미 존재하는 run directory를 덮어쓰지 않는다.

## 3. 입력 fixture

임시 Scene은 실내 기하 결과가 아니라 Open-Meteo 수집 대상 site를 만들기 위한 최소 유효 Scene이어야 한다. 위치는 서울시청 인근으로 고정한다.

```text
latitude_deg  = 37.5665
longitude_deg = 126.9780
timezone      = Asia/Seoul
```

기존 `docs/examples/fr06_scene_example.json`의 `37.5, 127.0`은 synthetic test location이므로 이 실제 연동 검증의 위치로 사용하지 않는다.

prediction 요청 시각은 worker가 저장한 forecast의 실제 coverage에서 고른다. 실행 전에 고정된 과거 timestamp나 DNI 숫자를 기대값으로 두지 않는다.

## 4. 사전 조건

1. Python 의존성, 특히 `pvlib`, `sqlmodel`이 설치되어 있어야 한다.
2. 인터넷에서 `https://api.open-meteo.com/v1/forecast`로 HTTPS 요청이 가능해야 한다.
3. 임시 SQLite 파일과 artifact directory를 쓸 수 있어야 한다.
4. Open-Meteo worker와 API 요청은 서로 다른 동작임을 유지한다. API 요청이 worker 또는 provider HTTP를 실행해서는 안 된다.

## 5. 실행 절차

### 5.1 임시 DB와 Scene 준비

1. 고유 artifact directory와 임시 SQLite DB를 만든다.
2. 서울시청 좌표의 유효 FR-06 Scene을 DB에 저장한다.
3. 저장된 Scene의 `scene_id`, revision, site 좌표를 결과 요약에 기록한다.

### 5.2 실제 Open-Meteo 수집

1. `open_meteo_worker.run_once(engine, now=<UTC-aware instant>)`를 한 번 실행한다.
2. worker 결과에서 lease 획득, `sites=1`, `stored=1`, `failed=0`을 확인한다.
3. 오류가 나면 HTTP status·transport·normalization 오류를 분리해 기록하고 이후 selector/API 검증을 통과로 처리하지 않는다.

### 5.3 durable snapshot 검사

생성된 `weather_snapshot`과 `weather_point`에서 다음을 확인한다.

- provider는 `open_meteo`, product는 hourly DNI snapshot이다.
- `quality_status="valid"`, `fetched_at`은 worker 실행 시각과 같거나 그 직후다.
- raw payload hash가 존재하고 snapshot ID가 존재한다.
- provenance에 다음이 있다.
  - request latitude/longitude: 서울시청 입력 좌표
  - grid latitude/longitude/elevation: Open-Meteo 응답 대표 격자 정보
  - `model_selection="best_match"`
  - `resolved_model` 또는 명시적 `unknown`
- 각 point의 DNI는 `null` 또는 finite nonnegative 값이며 `0`은 유효하다.
- `temporal_meaning="mean"`, `interval_end=valid_time`, `interval_end-interval_start=1시간`이다.
- `weather_point`의 좌표는 request 좌표가 아니라 provider grid 좌표이며, request 좌표는 provenance에서만 확인한다.

### 5.4 interval selector 검사

수집 snapshot의 non-null DNI point 하나를 선택해 다음 시각을 검사한다.

| 입력 timestamp | 기대 source | 확인 사항 |
| --- | --- | --- |
| `interval_start` | `open_meteo` | 시작 경계 포함 |
| interval 내부의 5분 또는 10분 시각 | `open_meteo` | hourly mean 재사용, 선형 보간 없음 |
| `interval_end` | 다음 hourly point 또는 fallback | 이전 point가 선택되지 않음 |
| snapshot coverage 밖 시각 | `pvlib_clear_sky` 또는 `unknown` | timestamp별 partial fallback |

같은 interval 후보가 둘 이상이면 별도 fixture 또는 DB insertion으로 newest `fetched_at`, 이어서 stable snapshot ID 선택 규칙을 검사한다. 실제 provider를 반복 호출해 tie-break를 만들지 않는다.

### 5.5 prediction API 검증

1. snapshot coverage 안팎을 모두 포함하는 `POST /api/scenes/{scene_id}/sunpath-predictions` 요청을 보낸다.
2. HTTP 200, `result_version="0.3.0"`, `timestamps[]`와 `irradiance[]`의 1:1 대응을 확인한다.
3. Open-Meteo entry는 다음을 확인한다.
   - `source="open_meteo"`, `fallback_used=false`
   - snapshot ID와 `source_snapshots[]`의 provider/role/weight
   - `valid_time`, `interval_start`, `interval_end`, `fetched_at`
   - request/grid 좌표와 model provenance
   - 실제 계산한 `clear_sky_dni_W_m2`, 유효한 경우 `clear_sky_index=final_dni/clear_sky_dni`
4. coverage 밖 entry는 `pvlib_clear_sky` 또는 clear-sky 계산 실패 시 `unknown`이며, 나머지 entry의 Open-Meteo provenance를 제거하거나 응답 version을 낮추지 않는지 확인한다.
5. `direct_irradiance_W_m2`가 기존 기하 path 결과와 DNI·입사각 규칙을 계속 따르는지 확인한다.

### 5.6 API 네트워크 격리 확인

worker 실행 뒤 API 요청 구간에서 Open-Meteo transport를 실패시키거나 호출 횟수를 감시한다. API 요청이 새 HTTP 호출이나 worker 실행 없이 DB snapshot만 사용해야 통과다.

## 6. 통과 기준

다음이 모두 만족되어야 실제 연동 검증을 PASS로 기록한다.

1. 실제 provider 응답을 worker가 유효 immutable snapshot으로 저장한다.
2. request/grid 위치와 Best Match provenance가 보존된다.
3. hourly DNI의 `[interval_start, interval_end)` 의미가 selector/API에서 유지된다.
4. future `valid_time` 자체가 freshness 실패 사유가 아니며, `fetched_at` 기준의 freshness가 적용된다.
5. Open-Meteo 미적용 시 timestamp별 clear-sky fallback이 동작한다.
6. API 요청은 provider HTTP와 worker를 직접 실행하지 않는다.
7. SQLite DB와 Scene revision은 prediction 요청으로 변경되지 않는다.

## 7. 실패 분류

| 분류 | 예시 | 조치 |
| --- | --- | --- |
| `SETUP_FAIL` | 의존성, artifact directory, SQLite 생성 실패 | 로컬 환경을 수정한 뒤 재실행 |
| `PROVIDER_FAIL` | DNS, timeout, HTTP error, provider rate/availability | snapshot을 만들지 않고 시간·오류를 기록; 코드 결함으로 단정하지 않음 |
| `ADAPTER_FAIL` | 단위, JSON 구조, grid metadata, 시간 배열 검증 실패 | raw hash와 안전한 진단만 보관하고 adapter 검토 |
| `PERSISTENCE_FAIL` | snapshot/point/provenance 저장 불일치 | repository·migration 검토 |
| `SELECTION_FAIL` | interval 경계, freshness, tie-break, fallback 오류 | selector와 fixture를 검토 |
| `API_FAIL` | v0.3 provenance 누락, API가 HTTP/worker 실행 | service/API dependency 경계 검토 |
| `NUMERICAL_OBSERVATION` | provider DNI가 예상과 다름 | 수치 자체는 pass/fail 근거가 아니며 forecast 특성으로 기록 |

## 8. CI와 정기 실행

- 이 계획의 실제 HTTP 검증은 일반 CI와 unit test에 포함하지 않는다.
- CI는 mock provider fixture와 deterministic unit/integration test만 실행한다.
- 필요하면 개발자가 수동 실행하거나 별도 scheduler에서 smoke test로 실행한다.
- provider URL, 모델 선택, 응답 format 또는 운영 주기가 바뀐 징후가 있을 때 이 검증을 다시 수행한다.
