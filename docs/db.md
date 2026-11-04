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
Open-Meteo 기반 DNI 기상 예보 수집 스냅샷 및 시계열 예보 포인트.
- `snapshot_id` (TEXT, PK): 수집 스냅샷 해시 식별자.
- `fetched_at` (TEXT, NOT NULL): API 호출 시각.
- `dni_W_m2` (REAL): 법선면 직달일사량 추정치.

### 2.5 `ui_spaces` (`UISpaceRecord`)
사용자 친화적 3D 뷰어 및 공간 관리용 메타데이터.

---

## 3. 서버 재시작 시 영속성 보장 (`#62`)

- 서버 프로세스가 종료되거나 재시작되어도 `sqlite:///./sunsync.db` 파일에 저장된 모든 테이블 데이터는 그대로 유지됩니다.
- 가상 필름의 현재 셀 상태(`cells_json`), 활성 스케줄(`active_plan_json`), 마커 모드 상태 및 백업(`marker_backup_json`)이 모두 복원되므로 서버 재시작 후에도 `GET /api/scenes/{scene_id}/film-state` 및 `GET /api/scenes/{scene_id}/film-logs` 엔드포인트를 통해 재시작 전과 100% 동일한 상태를 조회할 수 있습니다.
- `tests/test_fr05_api.py::test_server_restart_persistence` 단위/통합 테스트를 통해 동일한 DB 파일 위에서 앱 인스턴스를 재생성하고 영속성을 엄격하게 검증합니다.
