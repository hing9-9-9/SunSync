# FR-05 스마트 필름 제어 프로토콜 및 시뮬레이션 클록 가이드

스마트 필름 하드웨어 제어 인터페이스, 가상 디바이스 상태 머신, 스케줄 플레이어, 영속 로그 및 다중 사용자 PPT ArUco 데모 격리 프로토콜 문서입니다.

---

## 1. 개요: REST API 기반 가상 필름 프로토콜

SunSync는 물리적 스마트 필름 하드웨어가 없는 시뮬레이션 환경에서도 실시간 필름 상태 전환과 스케줄을 검증할 수 있도록 **REST API 기반 가상 필름 상태 머신(Virtual Film State Machine)**을 제공합니다.

### 1.1 MQTT 대신 REST API를 선정한 이유
- **독립 실행 및 제로 인프라**: 별도의 MQTT Broker(Mosquitto 등) 설치/유지보수 없이 표준 HTTP 클라이언트(브라우저, 프론트엔드 UI, 테스트 스크립트)만으로 완결된 개발 및 시연 환경을 제공합니다.
- **1:1 하드웨어 매핑**: 정의된 JSON 제어 계약(`row`, `col`, `opacity`, `state`)은 추후 마이크로컨트롤러(ESP32, Raspberry Pi Pico W 등)의 HTTP 폴링 또는 경량 MQTT 토픽 페이로드로 1:1 직접 변환됩니다.
- **원자적 상태 및 트랜잭션 보장**: SQLite WAL 동시성 및 데이터베이스 트랜잭션과 결합하여 셀 단위 전송 실패 시 원자적 롤백과 감정 추적 로그 생성을 보장합니다.

---

## 2. 하드웨어 비종속적 `FilmDeviceProtocol` 및 디바이스 모델

```python
class FilmDeviceProtocol(Protocol):
    film_type: str
    rows: int
    cols: int

    def get_opacity(self, row: int, col: int) -> float: ...
    def get_grid(self) -> list[list[float]]: ...
    def apply_commands(self, commands: Sequence[tuple[int, int, float] | CellCommand]) -> list[tuple[int, int, float, float]]: ...
```

### 2.1 필름 유형 및 불투명도(Opacity) 체계
- **불투명도(Opacity)**: `0.0` (완전 투명, Transparent) ~ `1.0` (완전 불투명, Opaque) 범위의 단일 연속형 수치입니다.
  - `0.0`: `transparent`
  - `1.0`: `opaque`
  - `0.0 < opacity < 1.0`: `dimmed`
- **PDLC (Polymer Dispersed Liquid Crystal)**: 직사광선을 산란시켜 투과 광선을 확산광으로 전환하는 On/Off 2단계 상태 제어 (`0.0` 또는 `1.0`).
- **SPD (Suspended Particle Device)**: 전압 제어를 통해 빛 투과율을 세밀하게 조절하는 연속 디밍 제어 (`0.0 .. 1.0`).
- **가상 격자(Grid)**: 기본 10×10 격자(1m × 1m 창문 기준 10cm × 10cm 셀)를 제공하며, 임의의 $R \times C$ 격자 크기를 모두 지원합니다.

---

## 3. 스케줄 플레이어 및 시뮬레이션 클록 (`#61`)

FR-02에서 생성된 `ShadingPlan`을 실제 가상 스마트 필름에 적용하고, 시간의 흐름에 따라 변화하는 필름 상태를 추적합니다.

### 3.1 상태 매핑 및 "목표 미달" / "조도 미상" 규칙
`ShadingPlan`의 각 `Segment.state`는 필름 불투명도와 감사 로그 상태로 변환됩니다:
- `transparent`: `opacity = 0.0`, 상태 `"applied"`, 비고 `""`
- `opaque`: `opacity = 1.0`, 상태 `"applied"`, 비고 `""`
- `infeasible`: `opacity = 1.0`, 상태 `"target_unmet"`, 비고 `"목표 미달"` (조도 한계 초과 등으로 제어가 불가능하지만 최대 차광 유지)
- `unknown`: `opacity = 1.0`, 상태 `"unknown_intensity"`, 비고 `"조도 미상"` (기상 데이터 결측 시 보수적으로 차광 적용)

> **중요 감사 규칙**: 이전 세그먼트로 인해 셀이 이미 `1.0` 불투명 상태였더라도, 후속 세그먼트가 `infeasible` 상태로 진입하는 경우 세그먼트 시작 시각에 `status="target_unmet"`, `note="목표 미달"` 로그가 반드시 기록되어 모든 미달 사유를 영구 추적할 수 있습니다.

### 3.2 시뮬레이션 클록 (Simulation Clock Seek)
- `GET /api/scenes/{scene_id}/film-state?at=2026-10-14T09:30:00Z`와 같이 `at` 파라미터를 전달하면, 해당 특정 시점의 필름 셀 배열(`cells`)과 적용 상태(`states`)를 즉각 계산하여 반환합니다.
- 활성 스케줄(`active_plan_json`)이 존재하는 경우 스케줄 타임라인을 평가하며, 수동 명령만 누적된 경우 해당 시점까지의 `FilmApplicationLogRecord`를 재생(Playback)하여 필름 상태를 복원합니다.
- 시간대 오프셋이 누락된 ISO 8601 시각(`2026-10-14T09:30:00`)은 엄격한 검증에 의해 `422 Unprocessable Entity`로 거부됩니다.

### 3.3 스케줄 적용 멱등성 (Idempotency) 및 이력 보존
- `POST /api/scenes/{scene_id}/film-schedule-apply`가 동일한 `plan_id`로 재호출(네트워크 재시도, 페이지 새로고침 등)되는 경우, 기존 동일 `plan_id` 감사 로그를 정리 후 재기록하여 로그 중복 적재를 방지하는 완전한 멱등성을 보장합니다.
- 서로 다른 `plan_id`를 가진 신규 플랜이 적용되는 경우, 이전 플랜의 로그는 감사 이력으로 영구 보존되며 활성 플랜 상태만 신규 플랜으로 전환됩니다.

### 3.4 UTC ISO 8601 정규화 및 SQLite 사전순 정렬 일관성
- SQLite의 문자열 비교(`<`, `<=`, `>`, `>=`, `ORDER BY`)가 실제 시각의 선후 관계와 100% 일치하도록 모든 타임스탬프(`timestamp`, `since`, `until`, `at`)는 UTC 기준 `YYYY-MM-DDTHH:MM:SSZ` 포맷으로 자동 정규화되어 저장 및 조회됩니다. KST(`+09:00`) 등 서로 다른 오프셋이 입력되더라도 정확한 시계열 비교가 보장됩니다.

---

## 4. 다중 사용자 동시 PPT ArUco 데모 격리 프로토콜

발표 및 실습 환경에서 여러 사용자가 동일한 PPT 슬라이드에 투사된 ArUco 마커(`code_id=0`)를 각자의 스마트폰으로 동시에 촬영하더라도 시스템이 충돌 없이 안전하게 동작하도록 다중 안전장치를 제공합니다.

### 4.1 `(scene_id, window_id)` 스코프 격리
- 모든 필름 상태(`film_states`), 감사 로그(`film_application_logs`), 3D 씬(`scenes`)은 `scene_id`로 완전히 분리되어 관리됩니다.
- User A와 User B가 모두 `code_id=0` 마커를 등록하더라도 서로 다른 `scene_id`에 격리되므로 데이터 경합이나 오염이 발생하지 않습니다.

### 4.2 자동 창문 생성 (Auto-Provision Window)
- 사용자가 업로드한 3D 공간에 창문이 아직 정의되지 않은 경우(`scene.windows == []`), `auto_provision_window=True` 옵션을 통해 방 벽면의 유효한 모서리에 1.0m × 1.0m (10×10 셀) 규격의 표준 창문과 유효한 직교 좌표계(`u_axis`, `v_axis`, `inward_normal`)를 즉각 자동 생성합니다.

### 4.3 룸 경계 가구 자동 클램핑 (`clamp_furniture_to_room`)
- 스마트폰 ArUco 측정 시 카메라 오차나 잘못된 입력으로 가구 좌표가 방 바깥(예: `base_center_m = [12.0, -5.0, 0.0]`)에 위치하는 경우, 씬 검증 `422` 오류로 데모가 중단되는 대신 회전된 바운딩 박스(`cuboid_footprint`)와 높이가 방 내부 바닥 다각형(`floor_vertices_m`) 안쪽으로 안전하게 클램핑됩니다.
- 회전 각도(`yaw_deg_ccw_from_east != 0`)를 포함한 4개 바닥 꼭짓점의 볼록 껍질을 계산하여 다각형 경계를 벗어나지 않도록 단계적 축소 및 중심 이동 알고리즘을 적용하며, 클램핑된 가구의 ID는 응답의 `clamped_furniture_ids` 필드에 기록됩니다.

### 4.4 마커 표시 및 이전 상태 복원 (FR-05.2)
- `POST /api/scenes/{scene_id}/film-marker-mode` (`enabled=True`): 현재 필름 셀의 불투명도 스냅샷을 백업한 뒤 6×6 ArUco `DICT_4X4_50` 패턴(외곽 1셀 불투명 테두리 + 내부 4×4 이진 패턴)을 창문 필름에 표시합니다.
- 실제 OpenCV `cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)`의 사전 비트 매트릭스를 사용하여 화면에 렌더링된 가상 필름을 스마트폰 OpenCV 검출기로 촬영 시 정확한 `code_id`가 검출됩니다.
- `POST /api/scenes/{scene_id}/film-marker-mode` (`enabled=False`): 마커 표시 직전의 셀 불투명도 상태를 오차 없이 100% 완벽히 복원합니다. 마커 활성화 중 발생한 마커 영역 외 수동 셀 변경사항 역시 복원 시 보존됩니다.

### 4.5 `UISpaceRecord` 동기화 보장
- `/api/ui/spaces`를 통해 생성된 공간에 대해 `marker-pairing`이 수행되어 `SceneRecord.revision`이 증가할 때, 연관된 `UISpaceRecord.scene_revision` 및 `payload_json.serverRevision`이 잠금 단계에서 동시에 갱신되어 UI 클라이언트의 후속 `PUT` 호출 시 409 충돌을 방지합니다.

---

## 5. API 엔드포인트 레퍼런스 및 curl 예제

### 5.1 필름 상태 조회
```bash
# 현재 필름 상태 조회
curl -sS -X GET "http://localhost:8000/api/scenes/scene_demo_001/film-state"

# 시뮬레이션 클록 특정 시점 조회
curl -sS -X GET "http://localhost:8000/api/scenes/scene_demo_001/film-state?at=2026-10-14T09:00:00Z"
```

### 5.2 수동 셀 제어 명령 전송
```bash
curl -sS -X POST "http://localhost:8000/api/scenes/scene_demo_001/film-commands" \
  -H "Content-Type: application/json" \
  -d '{
    "window_id": "window_001",
    "commands": [
      {"row": 0, "col": 0, "state": "opaque"},
      {"row": 1, "col": 2, "opacity": 0.4}
    ],
    "timestamp": "2026-11-04T10:00:00Z",
    "note": "수동 블라인드 차광"
  }'
```

### 5.3 ArUco 마커 모드 토글 (FR-05.2)
```bash
# 마커 모드 활성화 (코드 ID 5, 시작 좌표 (2, 2))
curl -sS -X POST "http://localhost:8000/api/scenes/scene_demo_001/film-marker-mode" \
  -H "Content-Type: application/json" \
  -d '{
    "window_id": "window_001",
    "enabled": true,
    "code_id": 5,
    "start_row": 2,
    "start_col": 2
  }'

# 마커 모드 비활성화 (이전 셀 상태 복원)
curl -sS -X POST "http://localhost:8000/api/scenes/scene_demo_001/film-marker-mode" \
  -H "Content-Type: application/json" \
  -d '{"window_id": "window_001", "enabled": false}'
```

### 5.4 스케줄 적용 (FR-02 ShadingPlan 연동)
```bash
curl -sS -X POST "http://localhost:8000/api/scenes/scene_demo_001/film-schedule-apply" \
  -H "Content-Type: application/json" \
  -d '{
    "plan_request": {
      "start_time": "2026-10-14T04:30:00Z",
      "duration_minutes": 60,
      "interval_seconds": 60
    }
  }'
```

### 5.5 필름 적용 감사 로그 조회
```bash
curl -sS -X GET "http://localhost:8000/api/scenes/scene_demo_001/film-logs?status=target_unmet"
```

### 5.6 다중 사용자 PPT ArUco 바인딩 및 가구 클램핑
```bash
curl -sS -X POST "http://localhost:8000/api/scenes/scene_demo_001/marker-pairing" \
  -H "Content-Type: application/json" \
  -d '{
    "code_id": 0,
    "auto_provision_window": true,
    "enable_marker_display": true,
    "clamp_furniture_to_room": true,
    "furniture": [
      {
        "occluder_id": "desk_student_01",
        "name": "열람실 책상",
        "type": "desk",
        "base_center_m": [12.0, -5.0, 0.0],
        "dimensions_m": [1.2, 0.8, 0.75],
        "yaw_deg_ccw_from_east": 0.0
      }
    ]
  }'
```

## FR-03 공용 규격 확정 (2026-11-06)

1m × 1m 필름의 10cm 셀 100개 중 중앙 6×6셀을 사용하는 60cm 단일 ArUco 마커를 채택합니다. 공용 ID는 0이며 기존 다른 ID API는 유지합니다. 좌표계 및 측정 연결은 [단일 마커 계약](fr03_single_marker.md)을 따릅니다.
