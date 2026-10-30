# FR-01 MVP — 구현 가이드 (3단계)

> **목적:** FR-01을 단계별로 구현·검토하기 위한 체크리스트다. 설계 변경 이력 문서가 아니다.
>
> **설계 기준:** `docs/fr01_design.md`

## 목표 동작과 경계

요청 시간 범위의 태양 고도·방위각을 계산하고, 불투명 `Occluder` 또는 `Room` 경계와 먼저 교차하지 않으면서 `Window`를 통과해 선택된 FR-06 `ProtectedRegion.sample_points_m`에 직사광선이 도달하는지 판정한다. sample별 경로, region별 시간선, 최초 **표본 시각** 노출을 반환한다.

이 MVP 가이드의 제외 범위는 방 전체 노출 지도, 반사·굴절, 실외 차폐, ML/DL, FR-02 필름 제어, 프런트엔드, 예측 결과 영속화다.

> **FR-01.4 범위 변경:** 일사량/DNI·날씨·구름은 이후 FR-01.4 확장으로 포함됐다. 해당 구현은 이 가이드가 아니라 [`fr01_4_contract_v1.md`](fr01_4_contract_v1.md) 및 [현행 Open-Meteo DNI 설계](fr01_4_open_meteo_dni_design.md)를 따른다. 이전 다중 source 설계는 [폐기 문서](deprecated_fr01_4_nowcast_implementation_guide.md)에 보존한다.

**불변 조건:** FR-06의 오른손 ENU(`x` East, `y` North, `z` Up, 단위 m), 기하 규칙, Scene schema는 변경하지 않는다. `exposed`는 측정된 조도가 아닌 *기하학적 직사광선 경로*를 의미한다. 표면 전체 또는 연속 시간상 최초 접촉을 정확히 계산했다고 주장하지 않는다.

---

## 1단계 — 순수 Python 모델과 태양 계산

**목표:** FR-06을 병합하지 않고 테스트 가능한 backend 독립 기반을 만든다.

### 구현 대상

- `engine/fr01/__init__.py`, `models.py`, `solar.py`
- `tests/test_fr01_solar.py`
- `engine.fr01` import에 필요한 최소 패키징·테스트 설정

### 구현 지침

1. `docs/fr01_design.md` 3~7절과 기존 `engine/` 관례를 확인한다. `backend/app/schemas/scene.py`의 FR-06 계약은 참고만 하고 수정하지 않는다.
2. site, room, window, cuboid, 보호 영역, prediction option, 태양 위치, sample/path/blocker, region/timeline 결과용 typed 순수 Python DTO를 정의한다. `engine/`은 `backend`, FastAPI, SQLModel, `app`을 import하지 않으며 단위를 문서화한다.
3. timezone-aware timestamp 검증과 결정론적 UTC grid를 구현한다. 기본값은 120분/300초이며 양 끝을 포함하고, 비정렬 종료 시각은 한 번 추가한다. 기본 결과는 **25 timestamps**다.
4. `pvlib.solarposition.get_solarposition(..., method="nrel_numpy", altitude=0, pressure=101325)`로 기하학적 `elevation`, `azimuth`를 계산한다. apparent elevation은 사용하지 않는다.
5. 고도 `h`, 방위각 `a`(북=0°, 시계방향)를 태양 방향 ENU 단위 벡터 `s = (cos(h) sin(a), cos(h) cos(a), sin(h))`로 변환한다. 입사광 방향은 `-s`다.
6. 유한 수, 범위, timezone offset, prediction 한도를 검증한다. 조용한 clipping은 하지 않는다.

### 테스트·종료 조건

- 태양 벡터의 방위별 방향·길이·야간/지평선 사례
- offset-aware timestamp, 다른 offset의 같은 instant, 날짜 경계
- 기본 25 timestamps, 비정렬 종료 시각, 잘못된 interval
- 허용 오차를 명시한 독립 공개/reference solar-position fixture

태양 모듈과 테스트가 통과하고 FR-06 schema 변경이 없어야 종료한다.

---

## 2단계 — 3D ray casting과 시간 예측기

**목표:** 선택된 sample point가 창을 통한 막힘 없는 직사광선 경로를 갖는지 계산하고 시간에 따라 집계한다.

### 구현 대상

- `engine/fr01/geometry.py`, `predictor.py`
- `tests/test_fr01_geometry.py`, `test_fr01_prediction.py`
- 필요한 경우에만 `models.py` 확장

### 구현 지침

1. 1단계 DTO를 사용해 sample point `p`에서 태양 방향 `s`를 향하는 reverse ray `r(t)=p+t*s`, `t>=0`을 구현한다.
2. `elevation <= 0`과 back-facing surface(`dot(receiving_normal, s) <= angular_epsilon`)를 거부한다.
3. 사각 창 plane과 교차시키고 창 고유 `u_axis`/`v_axis` frame에서 hit를 검사한다. ray는 창을 통해 room 밖으로 나가야 하며(`dot(inward_normal, s) < 0`), edge/corner tolerance는 보수적으로 처리한다.
4. floor polygon edge를 `height_m`까지 압출해 벽을 만들고 floor·ceiling도 검사한다. 오목 room은 모든 경계를 검사하며 창의 사각 aperture만 외부 통과를 허용한다.
5. cuboid는 local yaw frame에서 slab intersection으로 처리하고, parallel/grazing/nearest blocker를 다룬다.
6. host cuboid face의 zero-distance outward contact만 무시한다. cuboid 내부 sample은 거부하며 공중의 `ProtectedRegion`은 불투명 물체가 아니다.
7. 한 sample이라도 노출이면 region은 `exposed`다. clear path에는 window와 hit/direction을, 그 외에는 가능한 reason/blocker를 포함한다.
8. `first_predicted_exposure_time`은 첫 `exposed=true` 표본 시각이며, 첫 시각부터 노출이면 `exposed_at_start=true`다.
9. engine에 HTTP/DB, 방 전체 irradiance grid, 필름 제어를 추가하지 않는다.

### 테스트·종료 조건

남향·회전 창, miss/parallel/boundary ray, 오목 room, wall/floor/ceiling, cuboid yaw/host self-intersection, 법선·기울어진 face, 여러 timeline을 분석 fixture로 검증한다. 반환 좌표, window ID, sample index, 안정 tie-break도 확인한다. geometry/predictor 테스트 통과 및 HTTP/DB 의존성 부재가 종료 조건이다.

---

## 3단계 — FR-06 adapter, API 및 통합 검증

**목표:** FR-06 Scene 계약을 변경하지 않고 FastAPI backend로 prediction을 제공한다.

### 구현 대상

- `backend/app/schemas/sunpath.py`, `services/sunpath.py`, `api/sunpath.py`
- route 등록, `tests/test_sunpath_api.py`, 최소 사용 문서·예제

### 구현 지침

1. `POST /api/scenes/{scene_id}/sunpath-predictions`를 동기식·결과 비영속 endpoint로 구현한다. timezone-aware `start_time`은 필수이고 `duration_minutes=120`, `interval_seconds=300`, `protected_region_ids`, `expected_scene_revision`은 선택이다.
2. 하나의 `SceneRecord` snapshot을 `Scene.model_validate_json()`으로 parse하고 ENU geometry를 engine DTO로 adapt한다. engine에서 FR-06 backend type을 import하지 않는다.
3. 계산 전 Scene readiness와 resource limit를 검증한다. Scene 없음은 404, 잘못된 요청/geometry는 422, revision 불일치는 409이며 internal error를 `not_exposed`로 오표기하지 않는다.
4. scene/revision, timestamps, solar positions, region timeline, sample status/reason, path/window hit, blocker, 최초 sampled exposure, assumptions을 반환한다.
5. FR-06 endpoint/model/schema `0.2.0`과 저장 Scene JSON을 보존한다. optional scenario/weather/DNI를 필수로 만들지 않고 optics와 FR-02 policy를 변경하지 않는다.
6. FR-06 synthetic fixture 및 분석 Scene으로 격리 DB API test를 작성하고 prediction이 Scene/revision을 변경하지 않음을 검증한다.

### 테스트·종료 조건

API 200/404/409/422, unknown/duplicate region ID, naive timestamp, oversized request, optional scenario/weather가 없는 Scene, UTC timestamp, DB 무변경을 확인한다. 기존 FR-06 regression 및 `pytest`, `ruff`, `mypy`(필요시 frontend check)가 통과해야 한다.

---

## 모든 단계의 작업 규칙

1. 한 번에 한 단계만 구현하고 테스트·검토 후 다음 단계로 진행한다.
2. 기존 branch와 팀원 파일을 보존하며 합의 없이 merge/reset/overwrite하지 않는다.
3. 이 가이드와 실제 FR-06 코드가 충돌하면 contract 변경 전에 차이를 식별하고 합의한다.
4. 작고 테스트 가능한 함수와 결정론적 분석 fixture를 우선하며, 필요한 설계 이탈은 설명한다.
