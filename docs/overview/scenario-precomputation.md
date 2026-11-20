# Rolling Time-Bucket & Event-Driven Scenario Precomputation Architecture

Related issue: `#164` (`[FR-01/FR-02/FR-04] Rolling + Time-Bucket 관측 및 로컬 이벤트 기반 차광 시나리오 선계산·캐싱 아키텍처 전환`)

## 1. 배경 및 문제 정의

기존에는 사용자가 공간 상세/미리보기 화면(`page === "detail"`)에 진입할 때마다 프론트엔드가 아래 3개의 계산 API를 순차/동시 호출했습니다.

1. `POST /api/scenes/{scene_id}/sunpath-predictions` (FR-01 일조 요약)
2. `POST /api/scenes/{scene_id}/shading-plans` (FR-02 셀 차광 스케줄)
3. `POST /api/scenes/{scene_id}/adaptive-shading-plan` (FR-04 적응형 제어 배지)

단일 워커 Uvicorn 환경에서 매 요청마다 동일한 공간의 3D 광선 추적(Raycast)이 3중 중복 실행되었고, Python GIL 경합과 `detail_level="diagnostic"`의 전체 타일 Pydantic 객체 직렬화 오버헤드가 겹쳐 프론트엔드 15초 타임아웃(`AbortSignal.timeout(15000)`)을 초과하는 문제가 있었습니다. 또한 `spaceApi.ts`가 `TimeoutError`를 네트워크 단절로 오인해 `"저장 서버에 연결할 수 없습니다..."` 오류를 표시했습니다.

스마트 필름 제어는 본질적으로 사용자가 화면을 열 때 계산하는 대시보드 작업이 아니라, **공간 변경(Local)과 기상/시간 버킷(Global) 변화에 따라 사전에 계산된 제어 시나리오를 유지하고, 화면 조회는 이를 즉시 읽어오는 Read-First 구조**여야 합니다.

---

## 2. 아키텍처 구조

### 2.1 기하 광선 추적과 기상 관측(DNI)의 분리

1일 예측에서 두 입력 축의 변동 주기는 다릅니다.

- **3D 기하 광선 추적 (`SceneRayTrace`)**: 방 크기, 창문, 가구(차광체), 보호 영역 기하가 바뀌지 않는 한(즉 `SceneGeometry` 및 날짜 창이 동일할 때) 각 보호 타일이 몇 시에 어느 창문 셀을 통과하는지는 불변입니다.
  - `server/backend/app/services/sunpath.py`의 `trace_scene_geometry`는 `_GEOMETRY_CACHE`에 `(geometry, region_ids, start_utc, duration_minutes, interval_seconds)` 키로 기하 교차 결과를 캐싱합니다.
  - `server/engine/fr01/predictor.py`의 `predict_region_exposure`는 타일 생성(`tile_centres`)을 타임스탬프 루프 밖으로 호이스팅하고, 지평선 아래(`SUN_BELOW_HORIZON`)·후면(`BACK_FACING`)·가구 내부 포함 검사를 조기 처리하여 단일 계산 비용을 절감합니다.
  - `extract_exposed_tiles`는 비노출 타일에 대한 Pydantic `SampleResultResponse` 생성을 생략하고 `sample.exposed`인 교차점만 직접 `ExposedTile`로 투영합니다.
- **기상/조도 관측 (`weather_fingerprint`)**: `server/backend/app/services/scenario_store.py`의 `compute_weather_fingerprint`가 ① `scene.weather`(유효기간 포함), ② 유효한 최신 `open_meteo` 스냅샷 ID 목록, ③ 활성화된 최신 `ai_nowcast_run.run_id`를 16자리 SHA-256 해시로 결합합니다.

### 2.2 2계층 갱신 사이클 (Local Event + Global Time-Bucket Rolling)

1. **Local Event (공간/보호영역 변경 시 무효화)**
   - `PUT /api/scenes/{scene_id}` ([scenes.py](../../server/backend/app/api/scenes.py)), `POST /api/scenes/{scene_id}/registrations`, `PUT /api/ui/spaces/{space_id}`, `DELETE /api/ui/spaces/{space_id}` ([ui_spaces.py](../../server/backend/app/api/ui_spaces.py)) 트랜잭션 내에서 `invalidate_scene_scenarios(session, scene_id)`를 호출해 구 버전 시나리오 스냅샷을 정리합니다.
2. **Global Time-Bucket Rolling (10분 주기 워커 갱신)**
   - 10분 단위 UTC 버킷(`current_time_bucket`, `operational_nowcast_worker.py`의 `run_once`)이 진행될 때 `run_rolling_scenario_update(engine, now=current)`가 등록된 모든 `scenes`를 순차(`Serial`)로 순회하며 `precompute_scene_daily_scenario`를 실행합니다.
   - 기상 입력(`weather_fingerprint`)과 날짜(`prediction_day_start_utc`)가 변하지 않았으면 SQLite 스냅샷을 그대로 재사용하고, 기상 스냅샷·AI 나우캐스트가 갱신되었거나 날짜가 바뀐 경우에만 `_GEOMETRY_CACHE`를 재사용해 FR-01/FR-02/FR-04 시나리오 묶음을 갱신합니다.
   - 추가 인프라(Redis, Celery 등)나 별도 서버 비용 없이 기존 SQLite(WAL 모드)와 systemd 타이머 위에서 동작합니다.

### 2.3 Single-Flight Coalescing & Read-First API

- **저장소**: `ScenarioSnapshotRecord` (`scenario_snapshots` 테이블, `server/backend/app/models/scenario_snapshot.py`)에 `snapshot_key` (`{scene_id}:{scene_revision}:{start_utc}:{duration_minutes}:{interval_seconds}`)를 기본 키로 하여 `sunpath_summary_json`, `shading_plan_json`, `adaptive_plan_json`, `time_bucket`, `weather_fingerprint`를 영속화합니다.
- **Single-Flight 동시 요청 병합**: Cold-cache 상태에서 클라이언트가 `sunpath-predictions`와 `shading-plans`를 동시에 요청하더라도 `scenario_store.py`의 `snapshot_key` 단위 락(`_SINGLE_FLIGHT_LOCKS`)으로 인해 3D 광선 추적은 단 1회만 수행되며, 대기 중이던 요청은 직후 SQLite에서 결과를 즉시 읽어 반환합니다.
- **신규 묶음 조회 엔드포인트**:
  - `GET /api/scenes/{scene_id}/scenario` (`duration_minutes=1440`, `interval_seconds=1800` 기본)
  - 사전에 계산된 `SceneScenarioResponse`(FR-01 요약 + FR-02 `ShadingPlan` + FR-04 `AdaptiveShadingPlanResponse` + 메타데이터 `time_bucket`, `weather_fingerprint`, `computed_at`)를 한 번에 반환합니다.

---

## 3. 프론트엔드 타임아웃 및 오류 구분 (`frontend/src/spaceApi.ts`)

- `predictSunpath`, `predictShadingPlan`의 요청 타임아웃을 `planAdaptiveShading`과 동일한 **45초(`45000ms`)**로 상향했습니다.
- `request<T>`의 예외 분기에서 `DOMException` (`TimeoutError` / `AbortError`, 호출자 명시 취소가 아닌 경우)은 HTTP `408` 상태와 `"계산 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."` 메시지를 반환해 실제 네트워크/서버 단절(`status === 0`)과 명확히 구분합니다.
