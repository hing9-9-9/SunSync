# main 브랜치 프로젝트 분석

기준: `main`의 커밋 `04e05a8` (2026-10-21). 이 문서는 2026-10-22에 수행한 코드·문서 검토 결과이며, 코드 변경은 포함하지 않는다.

## 1. FR-01·FR-06 외 기능 변화

### 확인된 사실

- 요구사항 범위는 FR-01~FR-06으로 정리되었다.
  - FR-02: 선택적 차광 결정
  - FR-03: QR 기반 공간 측정
  - FR-04: 일시 변화에 대한 안정 제어
  - FR-05: 필름 제어 인터페이스
- 번호 체계가 변경되었다. 과거 문서의 FR-03은 실외 그림자를 의미했으나, 현재 FR-03은 QR 기반 측정이다. 이전 FR-07~FR-10은 이번 범위에서 제외되었다.
- FR-03~FR-05는 요구사항과 아키텍처에는 정의되어 있으나, 완성된 백엔드 API·엔진 구현은 확인되지 않았다.
- `measure/`에는 ArUco 측정 관련 연구용 코드가 있으나, 백엔드 API 및 FR-06 Scene 저장 흐름과의 완성된 연결은 없다.
- 프런트엔드는 React/Vite 골격 수준이며, Scene 또는 FR-01 API를 호출하는 구현은 확인되지 않았다.

## 2. README·문서·요구사항 보고서 변경

### 확인된 사실

- `README.md`는 FR-06 Scene CRUD, FR-01 예측 API, mock 검증 방법, FR-01 관련 문서 4종 링크를 추가했다.
- `docs/features.md`는 FR-01~FR-06 전체 요구사항, 담당자, 세부 요구사항, 처리 흐름을 정리했다.
- `docs/architecture.md`는 `FR-03 → FR-06 → FR-01 → FR-02 → FR-04 → FR-05` 흐름과 모듈 경계를 명시했다.
- `docs/decisions.md`는 ENU 좌표계, 가상 필름, PDLC 기본 모델, 10 x 10 셀, Open-Meteo 우선/clear-sky 대체, `/api` 경로 정책, 스프린트 일정을 기록했다.
- 요구사항 보고서 PDF가 `docs/reports/SunSync_Requirements.pdf`에 추가되었다.
- 문서상 Open-Meteo 활용은 결정되었지만, 현재 FR-01 API는 날씨·DNI를 명시적으로 제외한다.

## 3. 현재 백엔드 구조와 API 연결

```text
FastAPI app
 ├─ /api/health
 ├─ /api/scene-schema
 ├─ /api/scenes/*
 │    → Scene 검증·SQLite 저장·조회·낙관적 버전 갱신
 └─ /api/scenes/{id}/sunpath-predictions
      → 저장 Scene 조회
      → FR-06 Pydantic Scene 검증/역직렬화
      → services/sunpath.py 어댑터
      → engine/fr01 (태양 위치·광선 교차·노출 판정)
      → 비영속 예측 응답
```

### 확인된 사실

- 앱 진입점은 `backend/app/main.py`이다.
- SQLite와 SQLModel을 사용하며, `SceneRecord(scene_id, revision, scene_json)` 형태로 전체 Scene JSON을 저장한다.
- FR-06 API는 다음과 같다.
  - `GET /api/scene-schema`
  - `POST /api/scenes/validate`
  - `POST /api/scenes`
  - `GET /api/scenes`
  - `GET /api/scenes/{scene_id}`
  - `PUT /api/scenes/{scene_id}`: revision이 정확히 1 증가할 때만 갱신하며 충돌 시 409를 반환한다.
- FR-01 API는 `POST /api/scenes/{scene_id}/sunpath-predictions`이다. 저장된 Scene만 입력으로 사용하며 예측 결과를 저장하지 않는다.
- `engine/fr01`은 백엔드에 의존하지 않는 계산 계층이다.
  - `solar.py`: pvlib 기반 태양 고도·방위각·ENU 태양 방향
  - `geometry.py`: 창, 방 경계, 회전 직육면체 가림체의 교차 계산
  - `predictor.py`: 표본점·시각별 노출 판정
  - `models.py`: 불변 DTO 및 결과 모델

## 4. FR-01 구현 범위와 미구현 사항

### 확인된 구현

- 시간대가 포함된 시작 시각과 위치 기반 태양 위치 계산
- UTC 정규화 시간 격자 생성: 기본 2시간, 5분 간격
- 보호 영역의 모든 표본점에서 태양 방향 역광선 추적
- 창 통과, 방 경계, 가림체 차단 여부 판정
- 표본별 상태·원인·창 교차점·차단 물체 반환
- 영역별 `any_sample` 집계, 노출 표본 수, 첫 번째 샘플링 노출 시각 반환
- 선택 영역, Scene revision 일치 검사, 계산량 제한, 404/409/422 처리
- m·ENU 좌표계, 단위·기하·API 통합·mock 검증 테스트

### 미구현 또는 명시적 범위 제외

- FR-01.4의 일사량(DNI), clear-sky 산정, Open-Meteo 연동
- 구름·실외 건물/지형 차폐, 반사·굴절·반그림자, 창 광학 특성
- 연속 시간의 정확한 최초 노출 시각 및 전체 표면/실내 조도 지도
- FR-02 셀 매핑·차광 의사결정, FR-04 스케줄 평활화, FR-05 제어 명령
- 예측 결과 저장, 비동기 작업, 프런트엔드 시각화

## 5. Open-Meteo 기상·일사량 예측 추가 시 고려점

### 확인된 기존 기반

- FR-06 `Scene`에는 선택적 `weather` 계약이 있다. `source`는 `open_meteo`, `pvlib_clear_sky`, `synthetic`을 구분하며, DNI, 유효 시각, 취득 시각, 예보 기준 시각, 보간 방식, 최대 허용 노후 시간을 표현한다.
- `source="open_meteo"`이면 `fetched_at`이 필수로 검증된다.
- 현재 FR-01 응답은 `exposure_basis="geometric_direct_path"`, `optics_applied=false`이며, 서비스 assumptions에도 DNI·날씨·광학 제외가 명시되어 있다.
- `docs/examples/fr06_scene_example.json`에는 `open_meteo_forecast_adapter` provider hint가 있다.

### 제안

- 외부 HTTP 호출은 엔진이 아닌 별도 백엔드 weather 어댑터 계층에 둔다. `engine/fr01`에는 시간별 DNI 배열만 전달해 순수 계산 계층의 경계를 유지한다.
- 예측 요청 중 네트워크를 호출할지, 저장된 `Scene.weather`만 읽을지 정책을 먼저 정한다. 재현성·응답 지연·외부 장애를 고려하면, 수집/갱신 후 저장 또는 캐시하고 FR-01은 저장값을 소비하는 구성이 안정적이다.
- Open-Meteo의 시간 해상도와 현재 `interval_seconds`(1초~1시간)를 맞출 규칙이 필요하다. 기존 `instant`/`preceding_hour_mean`, `linear_estimate`/`none` 계약을 준수해야 한다.
- `dni_W_m2 = null`은 0이 아니라 미상이다. 누락·노후·API 실패 시 clear-sky fallback 적용 여부와 데이터 출처·시각을 응답에 포함하는 것이 좋다.
- 기하 노출과 강도 결과를 분리한다. 기존 `status`는 유지하고 timestamp 또는 sample에 DNI, 입사각 보정 직접 일사량, `weather_source`, fallback 여부를 추가할 수 있다.
- 보호 등급 B/C와의 연결은 FR-02 책임으로 남긴다. FR-01은 강도 추정과 품질 메타데이터를 제공하고, 임계값과 셀 차광 결정은 FR-02가 담당하는 구성이 현재 아키텍처에 맞는다.

## 관련 파일

- `backend/app/api/sunpath.py`
- `backend/app/services/sunpath.py`
- `backend/app/schemas/sunpath.py`
- `backend/app/schemas/scene.py`
- `engine/fr01/solar.py`
- `engine/fr01/geometry.py`
- `engine/fr01/predictor.py`
- `docs/fr01_design.md`
- `docs/fr01_api.md`
- `docs/architecture.md`
- `docs/decisions.md`
