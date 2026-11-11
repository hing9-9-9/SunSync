# SunSync 데이터베이스 아키텍처 및 영속성 가이드

SunSync의 SQLite 스키마, WAL(Write-Ahead Logging) 동시성 구성, 테이블 구조 및 서버 재시작 시 영속성 보장(`#62`)을 설명합니다.

---

## 1. 개요 및 SQLite 동시성 구성

SunSync는 서버 재시작 후에도 씬 구성, 날씨 예측 스냅샷, 가상 필름 상태 머신, 적용 이력 로그가 완전히 보존되도록 **SQLite 3 + SQLModel/SQLAlchemy** 기반 영속 계층을 사용합니다.

### 1.1 WAL 모드 및 Concurrency PRAGMA
동시 읽기 및 쓰기 성능을 극대화하고 `database is locked` 오류를 방지하기 위해 다음과 같은 PRAGMA 설정이 엔진 연결 이벤트 리스너를 통해 자동 활성화됩니다:

- `PRAGMA journal_mode = WAL;`: Write-Ahead Logging 모드를 활성화하여 쓰기 트랜잭션 진행 중에도 읽기 트랜잭션이 블로킹되지 않습니다.
- `PRAGMA synchronous = NORMAL;`: WAL 모드에서 무결성을 유지하면서 디스크 I/O 병목을 최소화합니다.
- `PRAGMA busy_timeout = 30000;`: 쓰기 락 충돌 시 최대 30초 동안 대기하여 동시 요청을 안전하게 직렬화합니다.
- `create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30.0})`: 다중 스레드 환경 및 FastAPI 비동기 요청 처리 지원.

---

## 2. 테이블 스키마 상세

### 2.1 `scenes` (`SceneRecord`)
FR-06 씬 애그리게잇(방 기하구조, 창문, 차광체 가구, 보호 영역, 마커, 정책 등)의 스냅샷을 저장합니다.
- `scene_id` (TEXT, PK): 씬 고유 식별자.
- `revision` (INTEGER): 낙관적 락 및 스케줄/시나리오 정합성 검증을 위한 버전 번호.
- `scene_json` (TEXT, NOT NULL): 검증된 전체 씬 데이터 문서.
- `owner_id` (TEXT, Nullable, Index): 공간을 만든 계정(`authuser.id`). 계정 도입 전에 만든 공간은 비어 있고, 로그인이 켜져 있으면 목록에 나타나지 않는다 (마이그레이션 5, [auth.md](auth.md)).

### 2.2 `film_states` (`FilmStateRecord`)
창문 스마트 필름의 현재 물리적/가상 셀 상태 머신을 저장합니다 (`#60`).
- `state_key` (TEXT, PK): `{scene_id}:{window_id}` 복합 키.
- `scene_id` (TEXT, Index): 소속 씬 식별자.
- `window_id` (TEXT, Index): 창문 식별자.
- `film_type` (TEXT, NOT NULL): 필름 하드웨어 모델 (`"PDLC"` 또는 `"SPD"`).
- `film_rows` (INTEGER, NOT NULL): 필름 행 수 (기본 10).
- `film_cols` (INTEGER, NOT NULL): 필름 열 수 (기본 10).
- `cells_json` (TEXT, NOT NULL): 현재 셀 불투명도 2차원 리스트 (`0.0 .. 1.0`).
- `marker_mode_active` (BOOLEAN, NOT NULL): FR-05.2 ArUco 마커 표시 모드 활성화 여부.
- `marker_code_id` (INTEGER, Nullable): 현재 표시 중인 마커의 사전 코드 번호 (`0..49`).
- `marker_start_row` / `marker_start_col` (INTEGER, Nullable): 마커 좌상단 시작 행/열 좌표.
- `marker_backup_json` (TEXT, Nullable): 마커 모드 활성화 직전 셀 불투명도 복원용 백업 스냅샷.
- `active_plan_id` (TEXT, Nullable): 현재 필름에 적용된 활성 스케줄 플랜 식별자.
- `active_plan_json` (TEXT, Nullable): 시뮬레이션 클록 탐색을 위한 원본 `ShadingPlan` JSON.
- `updated_at` (TEXT, NOT NULL): 상태 갱신 시각 (ISO 8601 UTC).

### 2.3 `film_application_logs` (`FilmApplicationLogRecord`)
스마트 필름의 모든 셀 전이 및 스케줄 적용 이력을 영구 추적하는 추가 전용(Append-only) 감사 로그 테이블입니다 (`#61`, `#62`).
- `log_id` (INTEGER, PK, Autoincrement): 로그 순번.
- `scene_id` (TEXT, Index): 씬 식별자.
- `window_id` (TEXT, Index): 창문 식별자.
- `timestamp` (TEXT, Index): 셀 상태가 전이된 시뮬레이션/실제 시각 (ISO 8601).
- `row` / `col` (INTEGER): 대상 셀 좌표.
- `previous_opacity` / `new_opacity` (REAL): 변경 전/후 셀 불투명도 수치 (`0.0 .. 1.0`).
- `previous_state` / `new_state` (TEXT): 변경 전/후 적용 상태 (`"transparent"`, `"opaque"`, `"dimmed"`).
- `plan_id` (TEXT, Nullable): 스케줄 계획 ID (수동 명령인 경우 Null).
- `status` (TEXT, NOT NULL): 전이 결과 상태 (`"applied"`, `"target_unmet"`, `"unknown_intensity"`).
- `note` (TEXT, NOT NULL): 상세 비고 (`infeasible`인 경우 `"목표 미달"`, `unknown`인 경우 `"조도 미상"`).
- `reasons_json` (TEXT, NOT NULL): 해당 차광 결정에 영향을 준 보호 영역 및 등급 리스트.
- `created_at` (TEXT, NOT NULL): 레코드 기록 시점 시각 (ISO 8601 UTC).

### 2.4 `weather_snapshot` & `weather_point`
Open-Meteo 기반 DNI 기상 예보 수집 스냅샷과 시계열 예보 포인트.
- `weather_snapshot`: `snapshot_id` (TEXT, PK, 수집 스냅샷 해시 식별자), `provider`, `product`, `model`, `observed_at`, `issued_at`, `fetched_at` (API 호출 시각), `raw_payload_hash`, `raw_payload_path`, `quality_status` (`valid`, `degraded`, `invalid`), `provenance_json` (마이그레이션 2), `site_key` (마이그레이션 4, 위도·경도 기반 지점 키).
- `weather_point`: `id`, `snapshot_id` (FK), `valid_time`, `latitude_deg`, `longitude_deg`, `grid_resolution_km`, `dni_W_m2` (법선면 직달일사량), `ghi_W_m2`, `direct_horizontal_W_m2`, `cloud_cover_percent`, `temporal_meaning` (`instant`, `mean`), `interval_start`, `interval_end`.

### 2.5 `nowcast_run` & `nowcast_point`
요청과 별개로 워커가 계산해 저장하는 DNI 나우캐스트 실행과 시점별 값.
- `nowcast_run`: `run_id` (PK), `site_key`, `created_at`, `input_snapshot_ids_json`, `algorithm_version`, `status`, `result_hash`, `expires_at`.
- `nowcast_point`: `id`, `run_id` (FK), `valid_time`, `clear_sky_dni_W_m2`, `clear_sky_index`, `final_dni_W_m2`, `prediction_method`, `contributors_json`, `fallback_reason`, 그리고 분위수 `dni_p10_W_m2`, `dni_p50_W_m2`, `dni_p90_W_m2`와 `kma_ai_dni_W_m2`, `open_meteo_dni_W_m2` (마이그레이션 3).

### 2.6 `ai_nowcast_run` & `ai_nowcast_point`
운영 AI 나우캐스트(KMA AI x Open-Meteo)의 실행과 10, 30, 60분 뒤 예측 (마이그레이션 4). 기본은 `shadow`, `active`일 때만 사용자 응답에 쓰인다.
- `ai_nowcast_run`: `run_id` (PK), `scene_id`, `site_key`, `prediction_origin`, `created_at`, `model_version`, `model_manifest_sha256`, `input_snapshot_ids_json`, `rollout_mode` (`shadow`, `active`), `status` (`ready`, `degraded`), `result_hash`, `expires_at`.
- `ai_nowcast_point`: `id`, `run_id` (FK), `horizon_minutes` (10, 30, 60), `valid_time`, `prediction_source` (`ai_model`, `open_meteo_default`, `open_meteo_fallback`, `unavailable`), `dni_W_m2`, 분위수 `dni_p10_W_m2`, `dni_p50_W_m2`, `dni_p90_W_m2`, `open_meteo_dni_W_m2`, 섀도 분위수 3개, `kma_snapshot_id`, `kma_observed_at`, `open_meteo_snapshot_id`, `open_meteo_fetched_at`, `fallback_used`, `fallback_reason`, `quality_flags_json`.

### 2.7 `worker_lease`
워커 중복 실행을 막는 임대: `lease_name` (PK), `owner_id`, `expires_at` (마이그레이션 1).

### 2.8 `ui_spaces` (`UISpaceRecord`)
화면 전용 정보(건물, 층, 구역 등)를 공통 Scene과 같은 트랜잭션으로 저장한다.
- `space_id` (TEXT, PK): 화면의 공간 ID.
- `scene_id` (TEXT, Unique): 연결된 `scenes.scene_id`. 소유자는 이 Scene의 `owner_id`로 정한다.
- `scene_revision` (INTEGER): 화면 정보가 만들어진 Scene 버전. 다른 모듈이 Scene을 바꾸면 달라져 목록에서 "다른 모듈에서 수정됨"으로 표시된다.
- `payload_json` (TEXT): 화면용 공간 문서.

### 2.9 `ui_deleted_spaces` (`DeletedSpaceRecord`)
삭제한 공간 ID의 기록. 같은 ID로 다시 올리는 것을 막고, 다른 기기가 지역 사본을 지우게 한다.
- `space_id` (TEXT, PK), `deleted_at` (TEXT), `owner_id` (TEXT, Nullable, Index): 삭제한 계정. 마이그레이션 6 이전에 삭제된 ID는 비어 있다.

### 2.10 로그인 테이블 (`docs/auth.md`)
- `authuser`: `id` (PK), `google_sub`, `email`, `name`, `created_at`, `last_login_at`. 구글 토큰은 저장하지 않는다.
- `authsession`: `token_hash` (PK, 쿠키 값의 해시), `user_id`, `created_at`, `last_seen_at`. 미활동 30분과 최대 12시간이 지나면 쓸 수 없고, 만료된 행은 새 로그인이 만들어질 때 지운다.
- `loginattempt`: `state_hash` (PK), `nonce`, `code_verifier`, `next_path`, `browser_hash`, `created_at`. 10분 안에 한 번만 쓰고, 동시에 최대 1000개.

### 2.11 연결과 측정 작업 테이블
- `handoff`: `token` (PK), `code` (Unique, 8자리), `space_id`, `status`, `expires_at`. 과거 호환용 연결 코드(30분 유효).
- `measurementjob`: `token` (PK), `space_id`, `status`, `created_at`. 웹이 만든 측정 요청을 같은 계정의 앱이 이어받는 지속 작업 ([device-handoff.md](device-handoff.md)).
- `capturemarkersession`: `scene_id` (PK), `session_id`, `window_id`, `marker_json`, `expires_at`, `active`. 촬영 세션마다 서버가 고른 마커와 표시 상태 ([fr03_single_marker.md](fr03_single_marker.md)). Scene당 하나이며 60분 유효.

### 2.12 `room` (과거 초안)
초기 개발의 Room 테이블. 지금은 `scenes`가 대신하며, 삭제하거나 변경하지 않는다.

---

## 2A. 마이그레이션

SQLModel이 `create_all`로 테이블을 만든 뒤 `upgrade_database`가 SQLite `PRAGMA user_version`을 보고 필요한 단계만 한 번에(한 트랜잭션으로) 적용한다. 새 DB는 모델에 이미 있는 열을 다시 추가하지 않도록 각 단계가 확인한다. 현재 `user_version`은 6이다.

| 버전 | 내용 |
|---|---|
| 1 | `weather_snapshot`, `weather_point`, `nowcast_run`, `nowcast_point`, `worker_lease`와 색인 |
| 2 | `weather_snapshot.provenance_json` |
| 3 | `nowcast_point`의 분위수와 `kma_ai_dni_W_m2`, `open_meteo_dni_W_m2` |
| 4 | `weather_snapshot.site_key`, `ai_nowcast_run`, `ai_nowcast_point` |
| 5 | `scenes.owner_id`와 색인 (기존 공간은 소유자 없음) |
| 6 | `ui_deleted_spaces.owner_id`와 색인 |

DB 파일 위치는 환경변수 `SUNSYNC_DATABASE_URL`이고 기본값은 `sqlite:///./sunsync.db`이다. 서버는 `/var/lib/sunsync/sunsync.db`를 쓰며 매일 백업한다 ([deploy.md](deploy.md)).

---

## 3. 서버 재시작 시 영속성 보장 (`#62`)

- 서버 프로세스가 종료되거나 재시작되어도 DB 파일(기본 `sqlite:///./sunsync.db`, 서버는 `SUNSYNC_DATABASE_URL`이 가리키는 파일)에 저장된 모든 테이블 데이터는 그대로 유지됩니다.
- 가상 필름의 현재 셀 상태(`cells_json`), 활성 스케줄(`active_plan_json`), 마커 모드 상태 및 백업(`marker_backup_json`)이 모두 복원되므로 서버 재시작 후에도 `GET /api/scenes/{scene_id}/film-state` 및 `GET /api/scenes/{scene_id}/film-logs` 엔드포인트를 통해 재시작 전과 100% 동일한 상태를 조회할 수 있습니다.
- `tests/test_fr05_api.py::test_server_restart_persistence` 단위/통합 테스트를 통해 동일한 DB 파일 위에서 앱 인스턴스를 재생성하고 영속성을 엄격하게 검증합니다.
