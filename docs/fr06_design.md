# FR06 공간 데이터

수동 입력과 ArUco 바닥점 결과를 같은 `Scene`으로 검증하고 SQLite에 저장한다.
기존 FastAPI + SQLModel 구조를 사용한다. 계약 버전은 `0.2.0`이며 공간 데이터
저장과 조회까지 구현한다. FR01 태양 경로 계산, FR02 제어 최적화, 사진에서의
ArUco 검출, UI 화면 개발은 각 담당 모듈의 후속 작업이다.

## 기준과 결정

- [10/14 미팅](https://docs.google.com/document/d/1EIlqPKcd9ibQDjtm9UPqc6R-lqO6-LaJq1HS6M_27Cc/edit?tab=t.778uge8elyic): FR01·02·04·06 선택, FR06·FR01 선행.
- [FR06 설계](https://docs.google.com/document/d/1EIlqPKcd9ibQDjtm9UPqc6R-lqO6-LaJq1HS6M_27Cc/edit?tab=t.vp7o25nkajaw): `0.2.0` 형상·출처·순간 보호 정책.
- Library `SunSync_FR06_JSON_검증묶음.zip` v2의 전체 합성 예제를 유지한다.
- 공유 문서의 SQLModel 검토 상태보다 저장소 [결정](decisions.md)의 명시적 선택을 적용한다.
- `engine/`에는 아직 좌표 구현이 없다. ENU는 FR01 담당자와 연결 시 확인할 계약이다.

전체 예제의 필드를 모두 요구하면 방 하나를 입력하는 데도 예보와 시나리오가
필요해진다. 이 구현은 `windows`, `occluders`, `protected_regions`, `markers`,
`optical_profiles`의 빈 배열과 기상·시나리오 없는 공간 저장을 허용한다.
보호 영역이 있으면 정책, 창이 있으면 광학 프로파일, 시나리오가 있으면 연결된
정책·기상이 필요하다. 제공된 전체 예제도 그대로 검증된다.

## 공통 형상

| 객체 | 입력 |
|---|---|
| Room | 바닥 다각형 `floor_vertices_m`, 양의 `height_m`, 선택적 층수 `floor_level` (-100~1000), 이름, 출처 |
| Window | 벽 위 직사각형 네 점, `inward_normal`, `u_axis`, `v_axis`, 실외 방위각, 광학 프로파일, 필름 격자 `film_rows`/`film_cols` (기본 10×10) |
| Occluder | 직육면체 바닥 중심, `[가로, 깊이, 높이]`, 동쪽 기준 반시계방향 yaw, 가구 종류 `type` (`bookshelf`/`desk`/`chair`/`other`, 기본 `other`) |
| ProtectedRegion | 평면 직사각형 네 점, `receiving_normal`, A–D 등급, 면 위 평가점, 선택적 host 가림물 |
| Marker | 마커 종류·사전·ID, 한 변 길이, 평면 `plane` (`floor`, `window`, `vertical`, `horizontal`), ENU 4×4 강체 변환 행렬, 창 마커 격자 배치 (`window_id`, `start_row`, `start_col`, `marker_rows`, `marker_cols`) |
| Observation | `manual`, `aruco_planar`, `aruco_board_camera_at_point`, 시각, 확인 상태, 불확실성, 취득 내용 |

- 단위 m, 오른손 ENU: x 동쪽, y 북쪽, z 위쪽. 방 바닥은 z=0.
- `Room`: 선택적 층수 `floor_level` (정수 -100~1000층, 기본값 `null`). 건물 내 층수 정보를 기록하며 하위 호환성을 위해 생략 가능하다.
- `Window`: 필름 칸 배치 `film_rows`, `film_cols` (기본값 10×10, 0.1m 칸 기준 다중 모듈 확장 지원). 실내 기준 좌측 상단 `(row 0, col 0)` 좌표계와 창 면 `(u, v) ↔ (row, col)` 변환 함수를 제공한다.
- 물리 필름은 1m × 1m 모듈(10×10셀)들을 빈틈없이 배치한다. 창 전체의 크기를 1m로 제한하지 않는다. 촬영·pairing은 `module_row`, `module_col`(실내 좌상단 0 기준)로 필름을 선택하며 시작 셀은 `(10×module_row+2, 10×module_col+2)`다. 모듈 배치와 벽→창 여백의 좌표식은 [단일 마커 연결 계약](fr03_single_marker.md#다중-필름-선택과-벽-기준-위치)을 따른다.
- `Occluder`: 가구 종류 `type` (`bookshelf`, `desk`, `chair`, `other`, 기본값 `other`). 기하 형상 `kind: cuboid`와 구분하여 용도와 의미론적 특성을 보존한다.
- `Marker`: 평면 `plane` (`"floor"` 또는 `"window"`). 창 마커의 경우 `window_id`, `start_row`, `start_col` (기본 `(2, 2)`), `marker_rows=6`, `marker_cols=6` (ArUco 4×4 테두리 포함 6×6칸 = 0.6m)를 지정하며, 마커가 창 범위를 벗어나지 않도록 검증한다.
- 창 방위각은 **실외 법선** 기준 진북 0°, 시계방향이며 가림물 yaw와 다르다.
- Room 바닥은 첫 점을 끝에 반복하지 않는다. 정점 순서는 해당 법선을 향해 오른손 법칙을 따른다.
- Room 바닥의 단순 평면 형상, 보호영역의 직사각형 형상, 유한수, 법선, 직사각 창, 방 안의 형상, 가림물의 실제 면,
  평가점 포함, 중복 ID와 누락 참조를 검증한다.
- **가구 3D 겹침 규칙 (#115)**:
  - **겹침의 정의**: 같은 구역(`zone`) 안의 두 가구가 차지하는 3D 직육면체 `[x, x + width] × [y, y + depth] × [0, height]`의 내부 부피가 세 축 모두 허용 오차 `1e-6` m보다 크게 교차할 때(`min(a.x2, b.x2) - max(a.x1, b.x1) > 1e-6`, `min(a.y2, b.y2) - max(a.y1, b.y1) > 1e-6`, `min(a.z2, b.z2) - max(a.z1, b.z1) > 1e-6`) 겹침으로 판정한다. 저장 단계(`POST /api/ui/spaces`, `PUT /api/ui/spaces/{space_id}`, 프런트엔드 저장 검증)에서는 이를 422 오류로 거절하고 겹치는 두 가구 이름이 들어간 한국어 메시지(`'<가구1>'과 '<가구2>' 가구가 서로 겹칩니다. 가구 위치나 크기를 조정해 주세요.`)를 반환한다.
  - **맞닿음 허용**: 두 가구의 옆면·모서리가 정확히 맞닿는 경우(`교차 길이 <= 1e-6` m)는 내부 부피가 겹치지 않으므로 저장을 허용한다.
  - **위에 올려 둠 규칙**: UI 가구(`UIFurniture`)는 기본적으로 바닥(`baseHeight = 0`)에서 시작하므로 한 가구 위에 다른 바닥 가구를 같은 `(x, y)` 위치로 포개어 두는 것은 Z축 구간 `[0, height]`이 겹쳐 거절된다. 가구 상판 위 물품 보호는 별도 가구를 겹쳐 추가하는 대신 해당 가구의 `상단` 보호영역(`ProtectedRegion`)으로 설정하며, 하위 `Scene` 모델에서 설치 바닥 높이가 분리된 가림물(`z_min >= other.z_max - 1e-6`)은 Z축 부피가 겹치지 않는다.
  - **기존 저장 공간 열람**: 이미 저장된 공간에 겹치는 가구가 있더라도 목록·상세 조회(`GET /api/ui/spaces`, `GET /api/ui/spaces/{space_id}`)와 로컬 저장소 복원은 막지 않으며, 공간을 열었을 때 겹침 안내 문구와 수정 진입 버튼을 표시한다.
- `quality.observation_ids`로 형상과 관측을 연결한다. 미측정 불확실성은 `null`이며 0이 아니다.
- `confirmed`는 입력 검수 기록이다. 합성 예제의 `confirmed=true`는 현장 측정을 의미하지 않는다.

### 수동 / ArUco 연결

[최소 수동 예제](examples/fr06_manual_room.json)는 방과 수동 관측만 담는다.
[전체 합성 예제](examples/fr06_scene_example.json)는 수동 창·높이 입력과
ArUco 바닥점 출력을 하나의 Scene으로 결합한다. 두 예제 모두 실제 측정값이 아니다.

ArUco 모듈은 바닥 마커 로컬 점 `points_marker_m`와 ENU 점 `points_enu_m`를
내보내고, 행 우선 4×4 `T_ENU_marker`와 일치해야 한다. 변환은 오른손 강체
변환이어야 하며 바닥을 바닥으로 보낸다. `derived_fields`는 바닥/XY/yaw에
해당하는 결과를 명시한다. 높이와 수직면을 바닥 사진에서 구했다고 표시할 수 없다.
이 바닥점 관측만 사용할 경우 방·창·가림물 높이와 올라간 보호면에는 별도 수동 관측 출처가 필요하다.
마커만으로 진북이 결정되는 것도 아니므로 `north_alignment`를 별도로 기록한다.
`derived_fields`는 출처와 허용된 바닥 필드를 검사한다. 점에서 가림물 치수나 yaw를
재계산하는 매핑은 이 계약에 없으므로 취득 정확도나 도출 과정 자체를 인증하지 않는다.

### 보드 촬영으로 가구 등록

측정 모듈의 `sunsync.fr06-fragment/1` 출력은
`POST /api/scenes/{scene_id}/registrations`로 전달한다.
본문은 `{"expected_revision": 현재revision, "fragment": 측정모듈응답}`이다.
방은 먼저 생성되어 있어야 하며 가구의 room_id와 측정 ENU 원점·진북·바닥이
기존 방의 기준과 같아야 한다. 이 API는 방·창·기상을 보존하며 가구를 추가하고
revision 및 scenario.scene_revision을 한 번 증가시킨다. 충돌은 409,
잘못된 입력은 422이며 저장값은 변경하지 않는다.

가구 등록 전 Scene 생성/PUT으로 확인된 `registration_calibration`을 저장한다.
`calibration_id`는 측정 reference_id, `marker`는 확인된 기준 마커의 규격과 ENU 변환이다.
가구 요청의 보정 ID 및 전체 마커 값이 저장값과 같아야 하며 미등록 보정을 자동 채택하지 않는다.
보정이 다른 요청은 room_id가 같아도 거절한다.

`aruco_board_camera_at_point`는 직사각형·원 4점, 타원 6점, 단순 다각형 3~16점의 일반 3D 좌표를 받는다.
상판 종류는 `tabletop_shape`, 다각형의 순서 있는 외곽은 `tabletop_outline_enu_m`에 보존한다.
표준 모델의 경계상자 치수는 실제 외곽과 구분한다.
수직/수평 마커의 `plane`은 측정점의 평면을 뜻하지 않는다. 높이 산출 모드는
`surface_is_top`, 최고점 H1을 추가한 `capture_highest`, 실측 `manual`이다.
떠 있는 가구는 수동 높이와 설치 바닥 높이를 모두 기록한다. 관측에는 실제
촬영 시각, 표준 모델 ID와 경계상자 근사 여부도 저장한다. 시각을 모르면 내보내기를
거절하며 처리 시각을 촬영 시각으로 대체하지 않는다.

서버는 점/마커 변환 일치와 선택한 도형의 적합 잔차, 수평 상판,
가구 치수·중심·yaw·높이 및 방 내부 배치를 검증한다. 형상 경로는
`dimensions_m[0:2]`, `dimensions_m[2]`, `base_center_m`, `yaw_deg_ccw_from_east`이다.
기존 `aruco_planar`의 바닥점 제한과 방/창의 수동 높이 출처 규칙은 유지한다.
같은 마커 ID는 규격·변환이 같을 때 재사용하며 기존 가구/관측 ID는 덮어쓰지 않는다.
전체 Scene 저장·검증 API도 새 관측 타입을 지원한다.

## 보호 등급

| 등급 | 의미 |
|---|---|
| A | 보호면으로 향하는 기하학적 직사광선 경로 차단 요구. DNI=0/null이어도 해제하지 않는다. |
| B | 평가점별 순간 직사광선 강도 최대값에 낮은 상한을 적용한다. |
| C | B보다 높은 순간 강도 상한을 적용한다. |
| D | 추가 차광 요청 없이 채광을 우선한다. 공유 필름에서 A/B/C 제약이 우선할 수 있다. |

등급은 고정 필름 투과율이나 누적 노출 예산이 아니다. B/C 상한은 설정 가능하며
`0 < B < C`를 만족해야 한다. 예제의 30/70 W/m²는 시연값으로 재료 보존
또는 안전 기준이 아니다. 평가 지표는 `direct_solar_irradiance_W_m2`, 집계는
`max_over_sample_points`이다. 샘플 사이 핫스폿까지 보장하지 않는다.

필름의 0 투과율·연속 조절과 SGF 세부 패턴은 검증된 하드웨어 특성이 아니다.
후속 제어기는 A의 관련 경로가 남고 최소 달성 투과율이 0보다 크면 DNI와 무관하게
`infeasible`을 보고해야 한다. B/C 결측 강도는 `unknown`으로 다뤄야 한다.
이 API는 정책을 저장·검증하며 경로 차단 여부나 실제 제어 성공을 계산하지 않는다.

## 기상과 시각

기상은 공간 저장에 선택 사항이다. 제공하면 DNI와 W/m²를 사용하며 GHI, lux,
누적 Wh/m²로 대체하지 않는다. `dni_W_m2=null`을 실제 0과 구별해서 저장한다.
모든 시각은 실제 ISO 8601 날짜와 UTC 오프셋을 요구한다. 발행 시각을 모르면
`forecast_reference_time`을 null로 유지하며 `fetched_at`으로 대체하지 않는다.
원래 오프셋을 포함한 시각을 보존한다.

`instant`는 구간이 null인 순간 추정값, `preceding_hour_mean`은 유효 시각 직전
1시간 구간 평균이다. 평균을 순간 자료로 표시하거나 선형 보간하도록 설정하면
거부한다. 예제의 DNI 600/400은 합성값이다. 저장 성공은 예보 최신성·시각 범위·
결측·광학 확인·진북 확인을 포함하는 계산 준비 상태를 보장하지 않는다.
해당 실행 시점의 준비 검사는 FR01/FR02 연결 시 별도로 필요하다.

## API 사용

README대로 서버를 실행한 뒤 http://localhost:8000/docs 에서 입력할 수 있다.
아래 명령은 저장소 루트에서 실행한다.

```bash
curl -sS -X POST http://localhost:8000/api/scenes \
  -H 'Content-Type: application/json' \
  --data-binary @docs/examples/fr06_scene_example.json
curl -sS http://localhost:8000/api/scenes/scene_demo_001
curl -sS 'http://localhost:8000/api/scenes?offset=0&limit=20'
```

| 경로 | 동작 |
|---|---|
| `POST /api/scenes/validate` | 저장 없이 동일 모델·기하·참조 검증 |
| `GET /api/scene-schema` | UI/측정 모듈용 JSON Schema |
| `POST /api/scenes` | 새 ID의 Scene 저장, 성공 201, 중복 ID 409 |
| `GET /api/scenes` | offset/limit 기반 목록 |
| `GET /api/scenes/{scene_id}` | 전체 형상·출처·정책 조회, 미존재 404 |
| `PUT /api/scenes/{scene_id}` | 전체 교체. 새 revision은 현재 값+1, 충돌 409 |
| `POST /api/scenes/{scene_id}/registrations` | 측정 fragment를 검증해 가구 추가, 성공 시 revision+1 |

POST는 기존 자료의 양의 revision을 보존하므로 전체 예제는 revision 2로 저장된다.
PUT에서는 URL과 본문 scene_id가 같아야 하고, 포함된 scenario.scene_revision도
새 revision과 같아야 한다. 예를 들어 revision 2를 읽었다면 본문 revision과
scenario.scene_revision을 3으로 바꾸어 PUT한다. 서버가 DB의 이전 revision을
조건으로 원자적으로 갱신하므로 두 편집자가 같은 revision을 덮어쓰지 못한다.
유효하지 않은 입력은 422를 반환하며 기존 저장값을 변경하지 않는다.

검증된 Scene 전체를 별도 SQLModel 테이블에 JSON으로 저장한다. 기존 초안 Room
테이블은 삭제·변경하지 않는다. 현 단계는 로컬 개발용 API로 계정/프로젝트 접근
제어는 포함하지 않는다. 이 작업은 배포나 기존 DB 이전을 수행하지 않는다.

## 검증과 스키마 갱신

```bash
PYTHONPATH=backend python -m app.scene_cli validate docs/examples/fr06_scene_example.json
PYTHONPATH=backend python -m app.scene_cli validate docs/examples/fr06_manual_room.json
PYTHONPATH=backend python -m app.scene_cli schema
pytest -q
```

[JSON Schema](fr06_scene_schema.json)는 Pydantic 모델에서 생성한다. CI는 체크인된
파일과 모델의 일치를 검증한다. JSON Schema는 형식/범위를, 서버와 CLI는 추가로
기하학·관측·객체 간 관계를 검사한다. UI의 JSON Schema 검증만으로 저장 승인을
판단하지 말고 서버의 422 오류 위치와 설명을 표시한다.
