# FR-01 Future Sunlight Path Prediction MVP 설계

## 1. 목적과 범위

FR-06 `ProtectedRegion.sample_points_m`에 미래 직사광선이 도달할 수 있는지를 시간별로 예측한다. `pvlib`로 태양 위치를 계산하고, 각 sample point에서 태양 방향으로 reverse ray casting을 수행한다. 창까지의 경로, 실내 차폐, 시간별 노출 상태, 최초 예측 노출 시각을 반환한다.

기본 예측 범위는 시작 시각부터 2시간이며 간격은 5분이다. 시작과 종료를 포함하여 기본 25개 시각을 평가한다. 이 결과는 맑은 하늘을 보장하는 실제 일사 예보가 아니라 **주어진 실내 형상에서 가능한 기하학적 직사광선 도달 예측**이다.

포함하지 않는 기능:

- 방 전체의 노출 면적·분포 시뮬레이션 또는 mesh 생성
- 반사, 산란, 굴절, 태양 원반 크기와 penumbra
- 기상 예측·기상 API 호출·DNI 또는 irradiance 계산
- ML/DL, FR-02 차광 제어·등급별 정책 실행
- 실외 건물·수목·지형 차폐, 창틀·벽 두께·창 recess
- 예측 결과 DB 저장, frontend 화면, background job

이 문서는 설계와 향후 구현 계획이다. 아래 제안 모듈·API·출력 계약은 아직 구현되지 않았다.

## 2. FR-06 기준과 확인된 계약

분석 기준은 로컬 `origin/feature/fr06-scene-data`의 commit `b3cc4d90193e963cca671c1ae67f99a438adbd63`이다. 원격 서버의 최신 상태를 확인하는 `fetch`는 수행하지 않았다.

| 기존 파일 | 재사용할 계약 |
|---|---|
| `backend/app/schemas/scene.py` | `Scene`, `Site`, `Room`, `Window`, `Occluder`, `ProtectedRegion`, 관측·품질 정보 |
| `backend/app/schemas/geometry.py` | ENU 및 cuboid 변환 해석의 기준. ray intersection 구현 자체는 없음 |
| `backend/app/models/scene.py` | `SceneRecord(scene_id, revision, scene_json)` |
| `backend/app/api/scenes.py` | Scene 조회와 `Scene.model_validate_json()` 사용 방식 |
| `backend/app/db.py` | `get_session()` |
| `docs/fr06.md` | 공간 계약, 검증 범위, 저장 성공과 계산 준비 상태의 구분 |
| `docs/examples/fr06_scene_example.json` | 합성 통합 fixture |
| `tests/test_scene.py` | 회전, concave room, 기울어진 보호면 등 지원 범위 |

확인된 geometry conventions:

- metre, 오른손 ENU: x East, y North, z Up, 바닥 z=0.
- `Room.floor_vertices_m`은 단순 다각형이며 concave 형상도 허용한다. `height_m`은 일정한 방 높이이다.
- `Window.vertices_m`은 벽 위 3D 직사각형이다. `inward_normal`은 수평이며 실내를 향한다.
- 창의 `u_axis`, `v_axis`는 단위 직교축이며 `cross(u_axis, v_axis) == inward_normal`이다. 첫 정점에서 두 번째 정점이 +u, 첫 정점에서 네 번째 정점이 +v이다.
- `Window.azimuth_deg_true`는 실외 법선 기준 진북 0°, 시계방향이다.
- `Occluder`는 `material="opaque"`인 수직 cuboid이다. `base_center_m`은 바닥면 중심이고 `dimensions_m`은 local width/depth/height이다. yaw는 East 기준 반시계방향이다.
- `ProtectedRegion`은 부피가 아닌 평면 다각형이다. `receiving_normal`과 면 위 sample points가 있다. host가 있으면 host 외향 면과 일치한다.
- 방위·형상은 이미 ENU로 제공된다. `north_alignment`를 근거로 추가 회전하지 않는다.

확인되지 않은 사항은 ENU 원점의 지리적 대응, datum, 고도이다. MVP는 `Site`를 방의 대표 태양 위치 계산 지점으로 사용하고 실내 좌표에는 별도 geographic 변환을 적용하지 않는다고 가정한다. 이 가정은 결과 metadata에 표시한다. FR-06 모델을 변경하지 않는다.

## 3. MVP 판정 결정

### 3.1 노출의 의미

`exposed`는 태양이 지평선 위에 있고, 보호면 전면을 향하며, sample point에서 시작한 ray가 불투명 물체나 방 경계에 먼저 막히지 않고 창의 유효 내부로 나가는 상태이다.

유리·필름 transmittance, `grade`, DNI는 이 boolean 판정에 적용하지 않는다. 따라서 완전 차광 필름 설정이 있어도 기하 경로는 `exposed`일 수 있다. 결과의 `exposure_basis="geometric_direct_path"`와 `optics_applied=false`로 이를 명시한다. 실제 투과광이나 보호 정책 충족 여부는 결과에서 주장하지 않는다.

각 region은 해당 시각에 sample 하나라도 `exposed`이면 `exposed`, 아니면 `not_exposed`이다. 이것은 sample 집계이며 면 전체의 미노출을 보증하지 않는다. `ProtectedRegion` 자체는 가림물로 취급하지 않고 `Occluder`만 불투명 물체로 취급한다.

### 3.2 최초 노출과 시간 해상도

`first_predicted_exposure_time`은 평가한 시각 중 처음 `exposed`가 된 시각이다. 연속시간의 정확한 최초 도달 시각이 아니다.

- 기본 5분 간격 사이의 짧은 노출은 누락될 수 있다.
- 최초 true 이전 sample이 false여도 그 사이에 단 한 번의 전이만 있었다고 보장하지 않는다.
- 시작 시각부터 true이면 `exposed_at_start=true`이며, 실제 노출이 언제 시작했는지는 알 수 없다.
- 모두 false이면 null이다. 예측 구간 밖이나 sample 사이의 미노출을 보장하지 않는다.
- MVP는 보간, 이분 탐색, event refinement를 제공하지 않는다. 따라서 최대 5분의 오차 보장을 표기하지 않는다.

### 3.3 시간과 자원 제한

시작 시각은 요청 필수값으로 두어 재현성을 확보한다. offset이 있는 ISO 8601만 허용하며 내부 계산과 출력은 UTC로 통일한다. `Site.timezone`은 표시용 metadata이며 offset 있는 입력을 재해석하지 않는다.

제안 제한은 `duration_minutes` 1..1440, `interval_seconds` 1..3600, 시각 수 최대 1441, 선택된 전체 sample point 수 최대 10000이다. 추가로 `T × P × (W + O + E + 2) <= 5_000_000`을 적용한다. T는 시각 수, P는 sample 수, W는 창 수, O는 cuboid 수, E는 방 경계 edge 수이다. 초과 요청은 실행 전 거부한다. 제한값은 초기 운영 보호값이며 성능 측정 후 조정한다.

시각은 `start + k × interval`로 생성하고 종료보다 큰 값은 제외한다. 종료가 격자에 없으면 종료 시각을 한 번 추가한다. 마지막 간격은 짧을 수 있으며 실제 `timestamps`를 반환한다.

## 4. 제안 module 구조

| 제안 경로 | 책임 |
|---|---|
| `engine/fr01/__init__.py` | 공개 계산 함수 export |
| `engine/fr01/models.py` | web·DB와 독립적인 입력 DTO, 결과 타입, reason enum |
| `engine/fr01/solar.py` | UTC 시간 격자, pvlib 호출, solar vector 변환 |
| `engine/fr01/geometry.py` | ray-plane, window frame, room boundary, yaw cuboid slab intersection |
| `engine/fr01/predictor.py` | geometry 준비, 시각·sample 순회, 경로 판정, 집계 |
| `backend/app/schemas/sunpath.py` | HTTP 요청·응답 Pydantic 계약 |
| `backend/app/services/sunpath.py` | Scene 조회, readiness, DTO 변환, engine 호출 |
| `backend/app/api/sunpath.py` | 예측 endpoint와 오류 변환 |
| `tests/test_fr01_geometry.py` | 합성 ray/geometry 정답 검증 |
| `tests/test_fr01_solar.py` | 태양 위치·좌표·시간 검증 |
| `tests/test_fr01_prediction.py` | 시계열·집계·최초 노출 검증 |
| `tests/test_sunpath_api.py` | 기존 Scene과의 API 통합 |

공개 engine 함수 제안은 `predict_sunlight(scene_geometry, prediction_options) -> PredictionResult`이다. Engine은 FastAPI, SQLModel, `app`을 import하지 않는다. Backend adapter가 기존 `Scene`을 계산 DTO로 변환한다. geometry DTO는 계산용 snapshot이며 새 FR-06 저장 계약이 아니다. module import 방식은 구현 시작 시 현재 packaging과 backend 실행 경로에 맞춰 확인한다.

## 5. 입력 schema

### 5.1 제안 API

`POST /api/scenes/{scene_id}/sunpath-predictions` — 저장 없는 동기 계산, 성공 200.

```json
{
  "start_time": "2026-10-16T12:00:00+09:00",
  "duration_minutes": 120,
  "interval_seconds": 300,
  "protected_region_ids": ["region_A", "region_B"],
  "expected_scene_revision": 2
}
```

| 필드 | 계약 |
|---|---|
| `start_time` | 필수 timezone-aware ISO 8601 문자열 |
| `duration_minutes` | 정수, 기본 120 |
| `interval_seconds` | 정수, 기본 300 |
| `protected_region_ids` | 생략/null이면 전체 region. 빈 배열·중복·미존재 ID는 422 |
| `expected_scene_revision` | 선택 양의 정수. 현재 revision과 다르면 409 |

미지 필드와 잘못된 scalar coercion은 거부한다. Scene이 없으면 404, 요청·지원 형상·준비 상태가 부적합하면 422, 저장된 Scene이 현재 계약으로 역직렬화되지 않으면 내부 데이터 오류로 처리한다. 내부 계산 실패를 `not_exposed`로 숨기지 않는다.

### 5.2 Engine 입력과 readiness

Engine DTO는 `site(latitude_deg, longitude_deg)`, 방 바닥·높이, 창 ID·정점·frame, cuboid ID·바닥 중심·치수·yaw, region ID·normal·sample points, Scene identity/revision, 요청 시간을 담는다. sample identity는 원본 `sample_points_m`의 0-based `sample_index`로 유지한다. region 선택은 요청 배열 순서가 아닌 Scene 순서로 처리한다.

Backend는 기존 `Scene.model_validate_json()` 검증 후 다음을 확인한다.

- `schema_version="0.2.0"`, `units="m"`, `coordinate_frame="ENU"`.
- 선택 region이 적어도 하나 있고 계산 자원 제한을 만족함.
- 선택 sample이 cuboid의 엄격한 내부에 있으면 `sample_inside_occluder` 오류로 거부함. 경계 위 sample은 아래 self-intersection 규칙으로 처리함.
- pvlib 입력·출력은 finite여야 함. 유효하지 않은 결과는 실행 오류임.

`quality.confirmed=false`, `north_alignment.confirmed=false`, 미확인 north accuracy, `synthetic_fixture`, assumed optics는 실행을 막는 대신 구조화된 warning으로 전달한다. 정확도 보증을 하지 않는다. 기존 Scene validator가 관측 참조를 검증하므로 adapter는 원시 ArUco 점을 다시 측량하거나 최종 ENU geometry를 덮어쓰지 않는다.

`Scene.scenario`, `weather`, `policy.max_time_step_s`는 이 계산의 시간 입력으로 사용하지 않는다. 요청의 시간 격자가 유일한 기준이다. FR-06 scenario의 policy 상한은 제어·정책 시나리오용이며, MVP의 기본 300초와 충돌하지 않는다. 기상·scenario 없는 Scene도 계산 가능하다. 창이 없는 유효 Scene은 전 sample이 `no_window`가 된다.

## 6. 출력 schema

모든 거리·좌표는 m, 각도는 degree이다. 다음 필드를 갖는 독립 결과 계약 `result_version="0.1.0"`을 제안한다.

| 구조 | 필드 |
|---|---|
| `PredictionResult` | `result_version`, `scene_id`, `scene_revision`, `coordinate_frame`, `units`, `exposure_basis`, `optics_applied`, `start_time`, `end_time`, `interval_seconds`, `timestamps`, `solar_model`, `warnings`, `solar_positions`, `regions` |
| `solar_model` | `library="pvlib"`, 실제 `library_version`, `method="nrel_numpy"`, `elevation_kind="geometric"`, `altitude_m=0`, `pressure_pa=101325`, `site_timezone`, `site_role="representative_location"` |
| `Warning` | `code`, `object_id` (없으면 null), `message` |
| `SolarPosition` | `timestamp`, `elevation_deg`, `azimuth_deg_true`, `sun_direction_enu` |
| `RegionPrediction` | `region_id`, `aggregation="any_sample"`, `first_predicted_exposure_time`, `exposed_at_start`, `timeline` |
| `RegionTimeResult` | `timestamp`, `status`, `exposed_sample_count`, `samples` |
| `SampleResult` | `sample_index`, `point_m`, `status`, `reason`, `candidate_path`, `blocker` |
| `CandidatePath` | `window_id`, `window_hit_m`, `sample_to_window_distance_m`, `reverse_direction_enu`, `incoming_direction_enu`, `geometric_transmittance_ignored=true` |
| `Blocker` | `kind` (`occluder`/`wall`/`floor`/`ceiling`), `object_id`, `hit_m`, `distance_m` |

`status`는 `exposed` 또는 `not_exposed`이다. `reason`은 `clear_path`, `sun_below_horizon`, `back_facing`, `no_window`, `window_missed`, `boundary_ambiguous`, `room_blocked`, `occluder_blocked` 중 하나이다.

- `candidate_path`는 전면 창과의 유효 교점이 있을 때 제공한다. 차폐되어도 진단용으로 유지하며, `status="exposed"`인 경우에만 유효 sunlight path이다.
- `blocker`는 차폐가 검출되었을 때만 제공하고 나머지는 null이다. ID는 cuboid ID 또는 파생 면 ID `wall:{edge_index}`, `floor`, `ceiling`을 쓴다.
- 복수 후보 창은 ray 거리 순, 동일 거리에서는 `window_id` 순으로 선택한다. 첫 방 경계에 해당하는 창만 clear path가 될 수 있다.
- region의 최초 노출은 그 region의 timeline에서 최초 true이며, sample별 최초 노출은 timeline에서 유도할 수 있다.
- warning code 예: `synthetic_scene`, `unconfirmed_geometry`, `unconfirmed_north`, `unknown_north_accuracy`, `sampled_time_only`, `optics_ignored`, `external_shading_ignored`, `site_origin_unspecified`.

`no_window` 상태의 sample 예:

```json
{
  "sample_index": 0,
  "point_m": [3.0, 2.7, 0.9],
  "status": "not_exposed",
  "reason": "no_window",
  "candidate_path": null,
  "blocker": null
}
```

## 7. Solar position algorithm

1. 요청 시간을 UTC `pandas.DatetimeIndex`로 만든다. naive datetime은 받지 않는다.
2. `pvlib.solarposition.get_solarposition()`에 위도·경도, `method="nrel_numpy"`, `altitude=0`, `pressure=101325`를 명시한다.
3. 결과의 `elevation`과 `azimuth`를 사용한다. `apparent_elevation`은 사용하지 않는다. 대기 굴절은 MVP 범위 밖이다. 고도 0 m는 FR-06에 고도가 없어 사용하는 가정이다.
4. azimuth는 진북 0°, East 90°인 시계방향으로 유지한다. elevation h, azimuth a를 radian으로 바꾸어 다음 벡터를 만든다.

```text
s = (cos(h) * sin(a), cos(h) * cos(a), sin(h))
reverse_direction_enu = s
incoming_direction_enu = -s
```

`s`는 sample에서 태양을 향한다. `h <= 0`이면 `sun_below_horizon`, `dot(receiving_normal, s) <= angular_epsilon`이면 `back_facing`이다. 남향 창 예제에서 남쪽 태양의 s는 음의 y 성분을 가지며, 들어오는 광선은 양의 y 방향이다.

공식 API는 timezone-aware time 입력과 `nrel_numpy`를 지원한다. 구현 시 설치한 pvlib 버전을 기록하고 해당 버전에서 반환 column과 수치 fixture를 확인한다. 참고: [pvlib get_solarposition](https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.solarposition.get_solarposition.html), [pvlib solarposition source](https://pvlib-python.readthedocs.io/en/stable/_modules/pvlib/solarposition.html).

## 8. Reverse ray casting algorithm

### 8.1 공통 규칙

ray는 `r(t) = p + t*s`, `t >= 0`이며 s는 단위벡터라 t는 거리이다. 제안 수치값은 `distance_epsilon_m=1e-6`, `angular_epsilon=1e-9`이다. 측정 불확실성과 혼동하지 않는다. geometry와 window frame은 Scene snapshot당 한 번 준비한다.

판정 순서는 night → back face → 창 후보 → 방 경계 → cuboid → clear path이다. geometry가 모호한 경계는 보수적으로 미노출 처리하되 reason을 남긴다.

### 8.2 Window intersection

창의 첫 정점을 q, normal을 n이라 두면:

```text
denom = dot(s, n)
t_window = dot(q - p, n) / denom
hit = p + t_window * s
u = dot(hit - q, u_axis)
v = dot(hit - q, v_axis)
width = norm(vertices_m[1] - q)
height = norm(vertices_m[3] - q)
```

- `dot(n, s) < -angular_epsilon`인 창만 실내에서 실외로 나가는 후보이다.
- parallel ray와 `t_window <= distance_epsilon_m`은 후보에서 제외한다.
- `distance_epsilon_m < u < width-distance_epsilon_m` 및 v에 대한 동일 조건을 만족해야 유효 내부이다.
- edge/corner의 tolerance band는 `boundary_ambiguous`로 처리한다. 경계는 창틀 정보가 없어 통과를 보장하지 않는다.
- 후보가 없고 창도 없으면 `no_window`, 경계 후보만 있으면 `boundary_ambiguous`, 나머지는 `window_missed`이다.
- 창끼리 겹치면 선택 ID는 위의 거리·ID 규칙으로 고정한다. optics를 적용하지 않으므로 광학적 합성은 하지 않는다.

### 8.3 Room boundary와 창 개구부

바닥 다각형의 각 edge를 z=0..height로 extrusion한 수직 벽, 바닥, 천장을 계산용 경계로 사용한다. 방 전체를 시간별 simulation하는 것이 아니라 ray의 출구 검사에만 사용한다.

- 벽은 평면 교점 후 edge parameter와 z 범위를 검사한다. 바닥·천장은 수평 평면 교점 후 원래 floor polygon의 XY 내부·경계를 검사한다.
- 모든 양의 교점 중 가장 가까운 경계를 구한다. concave floor에서도 창보다 먼저 만나는 다른 벽이 있으면 `room_blocked`이다.
- 첫 경계의 거리·좌표가 후보 창 교점과 tolerance 안에서 같고, 해당 벽 위 창 내부이면 그 경계는 개구부로 처리한다. 창이 놓인 벽 전체를 제거하지 않는다.
- 창보다 앞의 경계는 opaque이고 바닥·천장도 동일하다. 여러 경계가 동시에 만나는 corner는 `boundary_ambiguous`이다.
- sample이 바닥·벽·천장 위에 있을 수 있다. t=0 교점은 무조건 무시하지 않는다. `p + epsilon*s`가 방의 엄격한 내부로 진입하면 원점 접촉을 무시하고 계속한다. 실외로 나가거나 경계를 따라가면 보수적으로 `room_blocked` 또는 `boundary_ambiguous`이다.

### 8.4 Occluder intersection

각 cuboid에 대해 ray origin과 direction을 yaw 역회전하여 local frame으로 옮긴다. origin에는 `base_center_m`을 빼고 direction에는 평행이동을 적용하지 않는다.

```text
local_origin = Rz(-yaw) * (p - base_center_m)
local_direction = Rz(-yaw) * s
bounds = [-width/2, width/2] × [-depth/2, depth/2] × [0, height]
```

slab method로 각 축의 entry/exit interval을 교차한다. direction 성분이 거의 0이면 해당 slab 안에 있는지를 별도 검사하여 0 나눗셈을 피한다. 창 전의 양의 ray 구간에 cuboid 교차가 있으면 `occluder_blocked`이다. 접선·모서리 접촉도 보수적으로 차폐한다. 가장 가까운 blocker를 반환한다.

host cuboid를 통째로 제외하지 않는다. 원점의 host 면 접촉만 허용한다. ray가 host 외부로 나가면 t=0 접촉을 무시하지만, 내부로 들어가거나 면을 따라가면 차폐한다. 이 규칙은 host 아닌 cuboid 경계 위 sample에도 동일하다. strict interior sample은 readiness에서 거부한다.

### 8.5 집계와 비용

각 시각에 solar vector를 한 번 계산하고 각 sample을 평가한다. region별 `any_sample`을 집계하고 처음 true인 시각을 기록한다. 기본 구현 비용은 `O(T × P × (W + O + E))`이다. MVP에서는 BVH나 spatial index를 추가하지 않는다. 결과 크기도 T×P에 비례하므로 실행 전 제한 검사가 필요하다.

## 9. FR-06 integration 전략

1. FR-06이 통합된 기준에서 `SceneRecord`를 조회한다. 현재 FR-01 작업 브랜치에는 FR-06 파일 일부가 없으므로 실제 구현 착수 전 해당 계약의 이용 가능성이 선행 조건이다. 이 설계 작업에서 branch 전환·merge는 하지 않는다.
2. 새 `sunpath` service에서 기존 `Scene`으로 역직렬화하고 expected revision을 확인한다.
3. 같은 DB 읽기에서 얻은 Scene snapshot으로 전체 요청을 처리한다. 계산 도중 Scene을 재조회하지 않는다.
4. adapter가 최종 ENU geometry를 engine DTO로 복사하고 provenance warning을 수집한다.
5. engine은 DB·HTTP·weather·policy에 접근하지 않는다.
6. 결과는 Scene ID와 실제 사용 revision을 포함한다. 계산 중 후속 revision이 저장되어도 결과는 원래 snapshot을 가리킨다.
7. 새 router만 `/api`에 등록한다. 기존 `/api/scenes` endpoint, FR-06 schema, `SceneRecord`, 기존 초안 `Room` 테이블은 변경하지 않는다.

재사용 시 `app.models.room.Room`과 `app.schemas.scene.Room`을 혼동하지 않는다. Engine에서 backend geometry helper를 import하여 계층을 역전시키지 않고, 기존 helper의 ENU/yaw 규약과 동일한 결과를 integration test로 확인한다.

## 10. Edge cases와 testing plan

| 영역 | 필수 검증 |
|---|---|
| 시간 | 기본 25개, 종료 비정렬, UTC/+09:00 동일 instant, DST offset 입력, naive 거부, 자정·날짜 경계 |
| 태양 | cardinal azimuth의 ENU 부호, 벡터 길이 1, 밤·지평선, 고위도, finite 결과 |
| 창 | 남향 합성 창, 회전된 frame, parallel, 뒤쪽 창, 내부 hit, 외부 miss, edge/corner, 겹친 창의 결정성 |
| 방 | concave room 중간 벽, floor/ceiling 선차폐, 개구부만 허용, 방 경계 위 sample, 경계 접선 |
| cuboid | yaw=0/37/90°, parallel slab, 가까운/창 뒤 blocker, tangent, host 표면 self-hit, host 내부 진입, strict interior 거부 |
| 보호면 | 위·수직·기울어진 normal, back face, region 중 한 점만 노출, sample index 보존, host 없는 면은 비차폐 |
| 시계열 | false→true, 시작부터 true, 전부 false, 여러 재노출, sample 사이 짧은 이벤트 누락을 명시하는 fixture |
| 계약 | FR-06 전체 예제·기상 없는 입력, no windows, unknown region, revision 충돌, warning 전달, optics/DNI/grade 비의존성 |
| API | 200/404/409/422, 자원 상한, 결과 UTC·revision, 계산 전후 Scene 불변, 내부 오류의 미노출 오인 방지 |

geometry 테스트는 pvlib를 사용하지 않고 지정한 ray로 수작업 정답을 검증한다. 예를 들어 남쪽 태양 `s=(0,-1/√2,1/√2)`과 `p=(3,2,0)`에서 y=0 교점은 `(3,0,2)`가 되어야 한다. FR-06 예제 창 내부에 해당하며, 다른 차폐물이 없는 fixture에서는 exposed이다.

solar 테스트는 선택한 pvlib 버전의 공식 예제 또는 출처가 기록된 SPA reference 값으로 허용오차가 명시된 regression fixture를 만든다. 같은 pvlib 함수를 테스트에서 다시 호출한 값만으로 정확성을 주장하지 않는다. ENU 변환·timezone 동등성은 별도로 검증한다.

FR-06 예제의 `scenario.time_step_s=10`을 그대로 따라가지 않고 요청 기본 300초를 사용하는지 검증한다. FR-06 regression suite도 함께 통과해야 한다. 네트워크나 실제 로컬 SQLite DB를 사용하지 않고 API 테스트는 임시 DB를 사용한다. 검증 명령은 향후 구현 후 `pytest`, `ruff check`, `ruff format --check`, `mypy backend/app`과 engine에 대한 별도 type check를 포함한다. 현재 문서 작성 단계에서는 실행하지 않는다.

## 11. 구현 순서와 완료 기준

1. **계약 검토:** 이 문서의 geometric exposure, sample aggregation, 최초 sampled 노출, 경계 보수 판정, 고도 가정을 승인된 MVP 기준으로 확정한다. FR-06 계약이 개발 기준에서 사용 가능한지 확인한다.
2. **입출력 정의:** engine DTO와 HTTP schema, reason/warning enum, 시간·자원 validation을 작성한다. 기존 FR-06 모델은 수정하지 않는다.
3. **Solar adapter:** pvlib 버전을 확인하고 UTC 격자·solar vector를 구현한다. reference·방향·시간 테스트를 통과시킨다.
4. **Geometry kernel:** window, room first boundary, yaw cuboid 교차를 각각 구현한다. 경계·concavity·self-hit 테스트를 먼저 통과시킨다.
5. **Predictor:** 지정 ray 기반 판정 순서, candidate path/blocker, region timeline과 최초 노출 집계를 연결한다.
6. **Backend integration:** 새 service와 endpoint, Scene snapshot adapter, readiness/warning을 연결한다. DB 변경이나 결과 저장은 하지 않는다.
7. **통합 검증:** FR-06 합성 예제와 단순 analytic scene을 사용하여 snapshot 불변성, revision, scope 제외 항목의 비의존성을 확인한다. 기존 FR-06 테스트와 정적 검사를 수행한다.
8. **성능·사용 문서:** 기본 요청 및 자원 제한 부근 요청을 측정하고 실행 시간·출력 크기를 기록한다. 실제 측정 전 지연시간 SLA는 약속하지 않는다. 호출 예제와 결과 한계를 문서화한다.

완료 기준은 기본 2시간/5분 요청이 25개 시각의 solar position과 선택 sample의 경로·상태·region 최초 예측 노출을 반환하고, geometry·시간·계약 테스트를 통과하며, FR-06 계약과 저장값을 변경하지 않는 것이다. 결과만으로 실제 날씨, 보호면 전체 미노출, 연속시간 최초 도달 또는 FR-02 제어 성공을 보증하지 않는다.
