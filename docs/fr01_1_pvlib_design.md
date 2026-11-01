# FR-01.1 pvlib 기반 햇빛 경로 예측

## 완료 상태

FR-01.1 MVP는 구현 완료 상태다. 저장된 FR-06 `Scene`을 읽어 각 직사각형 `ProtectedRegion`을 5 cm × 5 cm 타일로 나누고, 각 타일 중심점의 기하학적 직사광선 도달 가능성을 미래 timestamp별로 계산한다. 결과는 저장하지 않으며, FR-06 Scene schema와 API 계약은 변경하지 않는다.

> **문서 범위:** 이 문서는 FR-01.1~FR-01.3의 기하학 MVP만 다룬다. DNI·기상·직접일사량은 FR-01.4 확장으로 분리되며, 현행 기준은 [`fr01_4_open_meteo_dni_design.md`](fr01_4_open_meteo_dni_design.md)다.

| 경로 | 책임 |
|---|---|
| `engine/fr01/models.py` | FastAPI·DB와 독립적인 immutable DTO와 prediction result |
| `engine/fr01/tiling.py` | 직사각형 보호영역의 deterministic 5 cm 중심 타일 생성 |
| `engine/fr01/solar.py` | UTC timestamp grid, `pvlib` 태양 위치, ENU 태양 방향 |
| `engine/fr01/geometry.py` | window, room boundary, yaw cuboid ray intersection |
| `engine/fr01/predictor.py` | sample·timestamp 순회, path 판정, region 집계 |
| `backend/app/services/sunpath.py` | FR-06 `Scene` → FR-01 DTO adapter와 calculation resource limit |
| `backend/app/api/sunpath.py` | `POST /api/scenes/{scene_id}/sunpath-predictions` |
| `backend/app/schemas/sunpath.py` | request·response Pydantic contract |

## 공간 입력과 좌표계

FR-01은 검증·저장된 FR-06 `Scene`을 입력으로 재사용한다. 좌표 단위는 metre이며, 오른손 ENU frame에서 `x`는 East, `y`는 North, `z`는 Up이고 room floor는 `z=0`이다.

- `Site`: `latitude_deg`, `longitude_deg`, `timezone`은 태양 위치 계산에 사용한다.
- `Room`: `floor_vertices_m`와 `height_m`은 floor, ceiling, wall boundary를 정의한다.
- `Window`: 직사각형 `vertices_m`, `inward_normal`, `u_axis`, `v_axis`가 aperture와 실내 방향을 정의한다.
- `Occluder`: opaque cuboid의 `base_center_m`, `dimensions_m`, `yaw_deg_ccw_from_east`를 사용한다.
- `ProtectedRegion`: 꼭짓점 네 개로 된 직사각형 표면과 `receiving_normal`을 제공한다. 입력 sample point는 저장하지 않으며 FR-01이 5 cm 타일 중심점을 생성한다.

FR-06 geometry는 이미 ENU이므로 FR-01에서 별도 북쪽 보정이나 geographic-to-local 변환을 하지 않는다. `Site`는 room의 대표 태양 위치 계산 지점이다.

## 요청과 시간 격자

`POST /api/scenes/{scene_id}/sunpath-predictions` 요청은 timezone offset이 있는 `start_time`을 요구한다. `duration_minutes`의 기본값은 120, `interval_seconds`의 기본값은 300이다. 허용 범위는 각각 1..1440분, 1..3600초다.

FR-01은 입력 시각을 UTC로 정규화하고 시작과 종료를 포함하는 deterministic grid를 만든다. 종료 시각이 interval grid에 맞지 않으면 종료 시각을 한 번 추가한다. 기본 요청은 2시간·5분 간격으로 25 timestamps를 평가한다. 최대 timestamp 수는 1,441개다.

`protected_region_ids`를 생략하면 모든 region을 계산한다. 선택 sample point 수는 최대 10,000개이고, `timestamps × samples × (windows + occluders + room edges + 2)`가 5,000,000을 넘으면 요청을 거부한다.

## 태양 위치 계산

`engine/fr01/solar.py`는 `pvlib.solarposition.get_solarposition()`을 다음 고정 조건으로 호출한다.

```text
method="nrel_numpy"
altitude=0.0
pressure=101325.0
```

`elevation`은 geometric elevation이며 `apparent_elevation`은 사용하지 않는다. azimuth는 true north 기준 0°, 시계방향이다. elevation `h`, azimuth `a`에서 sample point가 태양을 향하는 ENU unit vector는 다음과 같다.

```text
s = (cos(h) * sin(a), cos(h) * cos(a), sin(h))
```

태양 고도가 0° 이하이면 `sun_below_horizon`이고, `dot(receiving_normal, s)`가 양의 angular tolerance를 넘지 않으면 `back_facing`이다.

## Reverse ray casting과 판정

각 sample point `p`에서 태양 방향 `s`로 reverse ray `r(t) = p + t × s`, `t >= 0`를 추적한다.

1. 태양이 지평선 위이고 보호면의 전면을 향하는지 확인한다.
2. 각 window plane과 교차하고, 해당 window의 `u_axis`/`v_axis` frame에서 직사각형 aperture 내부인지 확인한다. edge/corner는 보수적으로 `boundary_ambiguous` 처리한다.
3. floor polygon을 높이까지 extrusion한 wall, floor, ceiling 중 가장 가까운 room boundary를 찾는다. 유효 window hit가 첫 boundary와 일치할 때만 ray가 room을 떠날 수 있다. 따라서 concave room과 창 밖 wall은 차폐된다.
4. 각 opaque cuboid는 yaw 역회전 local frame에서 slab intersection으로 확인한다. window hit보다 가까운 cuboid가 있으면 가장 가까운 object를 `occluder_blocked` blocker로 반환한다.
5. 위 조건을 통과하면 `clear_path`이며 `window_id`, `window_hit_m`, ray direction, sample-to-window distance를 반환한다.

미노출 reason은 `sun_below_horizon`, `back_facing`, `no_window`, `window_missed`, `boundary_ambiguous`, `room_blocked`, `occluder_blocked` 중 하나다. `candidate_path`는 clear path일 때만 제공하고, `blocker`는 blocker가 확인된 경우에만 제공한다.

## 5 cm 타일과 시간별 집계

`predict_region_exposure()`는 선택된 region의 모든 5 cm 타일 중심점을 모든 timestamp에서 평가한다. 변의 길이가 5 cm의 배수가 아니면 마지막 행·열은 남은 폭을 사용하고, 그 부분 타일의 중심점을 사용한다. 타일은 row-major 순서이며 `sample_index`는 그 순서의 0-based 인덱스다. FR-01은 film cell을 계산하지 않는다.

- `timeline[].samples[]`: timestamp별 모든 sample의 상태, reason, candidate path, blocker
- `timeline[].exposed_sample_count`: `status="exposed"` sample 수
- `timeline[].status`: 하나라도 노출되면 `exposed`인 `any_sample` 집계
- `first_predicted_exposure_time`: timeline에서 처음 `exposed`인 **sampled timestamp**
- `exposed_at_start`: 첫 timestamp에 하나라도 노출되었는지
- `exposure_intervals[]`: 인접한 `exposed` grid entry를 합친 `[start_time, end_time]` 요약. grid 사이를 보간하지 않으므로 단일 노출 timestamp는 시작과 끝이 같다.

`first_predicted_exposure_time`은 연속시간의 정확한 최초 도달 시각이 아니다. timestamp 사이에서 발생하는 변화는 이 MVP가 보간하거나 refine하지 않는다.

## HTTP 결과와 오류

성공 응답은 `scene_id`, `scene_revision`, `timestamps`, `solar_positions`, `regions`, `assumptions`을 반환한다. 각 sample에는 `sample_index`, `point_m`, `status`, `reason`, `candidate_path`, `blocker`가 포함된다. 상세 request/response contract와 호출 예시는 [fr01_api.md](fr01_api.md)를 따른다.

- Scene 없음: `404`
- `expected_scene_revision` 불일치: `409`
- 잘못된 request, unknown/duplicate region, 저장된 invalid geometry, resource limit 초과: `422`

예측은 stored Scene JSON이나 revision을 수정하지 않는다.

## 구현 단계와 코드 경로

1. **입력 변환:** `backend/app/services/sunpath.py`가 검증·저장된 FR-06 Scene을 FR-01 DTO로 변환하고 요청의 시간·리소스 한계를 확인한다.
2. **타일 생성:** `engine/fr01/tiling.py`가 보호영역 꼭짓점에서 deterministic 5 cm 중심 타일을 row-major로 만든다.
3. **태양 위치:** `engine/fr01/solar.py`가 timezone-aware 요청 시각을 UTC grid로 변환하고 pvlib로 고도각·방위각과 ENU 방향 벡터를 계산한다.
4. **경로 판정:** `engine/fr01/predictor.py`와 `geometry.py`가 각 타일·시각마다 창 aperture, room boundary, cuboid occluder를 평가한다.
5. **요약·응답:** predictor가 timeline, 최초 노출 및 `exposure_intervals`를 만들고, API가 이 기하 결과와 FR-01.4 DNI 선택 결과를 응답에 조합한다.

## 서울시청 인근 mock validation

`tools/mock_validation/fixtures/valid/mock_scene_seoul_multi_sample_temporal.json`은 서울시청 인근 site(`37.5665`, `126.9780`, `Asia/Seoul`)의 합성 FR-06 Scene을 사용한다. runner는 temporary SQLite와 `TestClient(create_app(engine))`를 사용하므로 repository의 `sunsync.db`를 사용하지 않는다.

이 scenario는 보호영역 꼭짓점에서 생성한 5 cm 타일 중심을 사용한다. timestamp spacing, row-major `sample_index`, 타일별 window aperture 교점, 노출 타일 수 변화와 최초 sampled exposure를 검증한다. 타일 수와 노출 수는 보호영역 크기와 현재 타일링 규칙에 따라 달라지므로 문서에 고정 수치로 기록하지 않는다. runner는 응답을 이용해 expected 값을 다시 계산하지 않는다.

## 재현 명령

PowerShell에서 repository root와 활성화한 virtual environment 기준:

```powershell
$env:PYTHONPATH = "backend"
python tools/mock_validation/run_fr01_mock_validation.py
python tools/mock_validation/run_fr01_mock_validation.py --scenario mock_scene_seoul_multi_sample_temporal --verbose
python -m pytest -q
python -m ruff check backend/app engine/fr01 tests tools/mock_validation
python -m ruff format --check backend/app engine/fr01 tests tools/mock_validation
python -m mypy backend/app engine/fr01 tools/mock_validation
```

## MVP 범위 밖

- 연속적인 floor footprint, mesh 또는 정확한 노출 면적 계산
- 반사, 산란, 굴절, penumbra, 창틀·벽 두께·recess
- DNI·irradiance·weather prediction과 weather API — 이 MVP에서는 제외했으나 FR-01.4 확장으로 구현됐다. 상세 계약은 `fr01_4_open_meteo_dni_design.md`를 따른다.
- 실외 건물·수목·지형 차폐
- ML/DL, FR-02 control logic, SGF cell mapping
- prediction persistence, frontend visualization, background job
