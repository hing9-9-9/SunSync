# SunSync FR-01 구현 분석: 햇빛 경로 및 직접일사량 예측

> 분석 기준: 현재 작업 트리의 FR-01.1~FR-01.4 **Stage 1** 구현 코드. 이 문서는 구현 분석이며, 일반 이론을 구현 사실로 확장하지 않는다. FR-01.4 Stage 2 다중 source 미래 DNI 예측 엔진은 아직 구현되지 않았으며, 설계 계약은 [`FR-01.4_contract_v1.md`](FR-01.4_contract_v1.md)를 따른다.

## 1. 전체 계산 흐름

```text
요청 시각·위치
  → pvlib로 태양 고도각/진북 방위각 계산
  → ENU 단위 태양방향 벡터 s 생성
  → 보호면 표본점마다 s 방향으로 광선 추적
  → 창문 면·실 경계·실내 직육면체 장애물 판정
  → Scene.weather → Open-Meteo → pvlib clear-sky 순으로 DNI 선택
  → clear path일 때만 DNI × max(0, n·s) 계산
```

API 계산 진입점은 `backend/app/services/sunpath.py`의 `predict_scene()`이다. 이 함수는 태양 위치, DNI, 기하학적 노출을 계산한 후 각 sample의 직접일사량을 응답에 넣는다.

## 2. 단계별 수식과 코드 근거

### 2.1 태양 위치: pvlib에 위임

현재 구현은 태양 위치 천문식을 직접 구현하지 않고 `pvlib.solarposition.get_solarposition()`에 위임한다.

\[
(\alpha,A)=\operatorname{pvlibSolarPosition}(t,\phi,\lambda,h,p)
\]

| 변수 | 의미 | 단위 |
|---|---|---|
| \(\alpha\) | 태양 고도각, 수평선 기준 위쪽 각도 | ° |
| \(A\) | 진북 기준 방위각, 북쪽 0°, 시계방향 증가 | ° |
| \(t\) | UTC로 정규화한 timezone-aware 시각 | - |
| \(\phi,\lambda\) | 현장 위도·경도 | ° |
| \(h\) | 해발고도, 코드에서는 0으로 고정 | m |
| \(p\) | 기압, 코드에서는 101325로 고정 | Pa |

코드 근거:

- `engine/fr01/solar.py` — `calculate_solar_positions()`
- `get_solarposition(..., altitude=0, pressure=101325, method="nrel_numpy")`
- 결과 DataFrame의 `elevation`, `azimuth` 열을 사용한다.

따라서 SunSync가 직접 구현한 부분은 천문 태양위치 모델이 아니라, pvlib 결과를 실내 ENU 기하 계산에 연결하는 부분이다. 코드가 `apparent_elevation`이 아닌 `elevation`을 읽으므로 기하학적 고도각을 사용한다.

### 2.2 고도각·방위각에서 ENU 태양 방향 벡터로 변환

\[
\mathbf{s}=
\begin{bmatrix}
s_E\\s_N\\s_U
\end{bmatrix}
=
\begin{bmatrix}
\cos\alpha\sin A\\
\cos\alpha\cos A\\
\sin\alpha
\end{bmatrix}
\]

| 변수 | 의미 | 단위 |
|---|---|---|
| \(\mathbf{s}\) | 표본점에서 태양을 향하는 단위 방향벡터 | 무차원 |
| \(s_E,s_N,s_U\) | East, North, Up 성분 | 무차원 |
| \(\alpha,A\) | pvlib가 계산한 고도각·진북 방위각 | ° 입력 후 rad 변환 |

ENU 좌표계는 `x=동쪽`, `y=북쪽`, `z=위쪽`인 오른손 좌표계이다. `sun_direction_enu()`는 길이가 1에서 `1e-12` 이상 벗어나면 실패시키고, `SolarPosition`도 다시 단위벡터 검증을 한다.

코드 근거:

- `engine/fr01/solar.py` — `sun_direction_enu()`
- `engine/fr01/models.py` — `SolarPosition`

서울 mock fixture의 \(\alpha=43.10955^\circ\), \(A=184.22020^\circ\)를 넣으면 다음과 같다.

\[
\mathbf{s}=(-0.053724,-0.728069,0.683395)
\]

즉 태양은 약간 서쪽, 주로 남쪽, 위쪽에 있다.

### 2.3 표본점 광선과 창문 교차

보호면 표본점 \(\mathbf{p}\)에서 태양으로 향하는 반직선은 다음과 같다.

\[
\mathbf{r}(d)=\mathbf{p}+d\mathbf{s},\qquad d>0
\]

창문 평면 위 점 \(\mathbf{q}\), 창의 실내방향 법선 \(\mathbf{n}_w\)에 대한 교차 거리는 다음과 같다.

\[
d_w=
\frac{(\mathbf{q}-\mathbf{p})\cdot\mathbf{n}_w}
{\mathbf{s}\cdot\mathbf{n}_w}
\]

교차점은 \(\mathbf{h}=\mathbf{p}+d_w\mathbf{s}\)이다. 실제 코드는 다음도 만족해야 유효한 창문 hit로 본다.

\[
\mathbf{n}_w\cdot\mathbf{s}<-10^{-9},\qquad d_w>10^{-6}\ \mathrm{m}
\]

창문의 국소 좌표 \((u,v)\)도 테두리를 제외한 직사각형 내부여야 한다.

\[
10^{-6}<u<W-10^{-6},\qquad10^{-6}<v<H-10^{-6}
\]

코드 근거:

- `engine/fr01/geometry.py` — `intersect_window()`
- 같은 파일의 `_window_dimensions()` 및 `dot()`, `add()`, `scale()`

#### 실제 수치 예시

fixture의 \(\mathbf{p}=(5,1,1)\,\mathrm{m}\), 남쪽 창 평면 \(y=0\)에서,

\[
d_w=\frac{-1}{-0.728069}=1.373496\ \mathrm{m}
\]

\[
\mathbf{h}=(5,1,1)+1.373496\mathbf{s}
=(4.926210,0,1.938641)\ \mathrm{m}
\]

이는 `tools/mock_validation/fixtures/valid/mock_scene_clear_path.json`의 검증 교차점 `(4.92621, 0, 1.93863)` m와 일치한다.

### 2.4 실 경계·장애물 판정

창문 교차만으로는 노출을 확정하지 않는다. 구현은 광선이 가장 먼저 만나는 실 경계(벽·바닥·천장)를 계산하고, 창문 hit가 바로 그 최초 경계에 있어야 한다고 요구한다. 이어 표본점과 창문 사이에 더 가까운 불투명 Cuboid가 있으면 차단한다.

\[
V_i=
\mathbf{1}\left[
\begin{array}{l}
\alpha>0,\quad\mathbf{n}_i\cdot\mathbf{s}>10^{-9}\\
d_w=d_{\mathrm{room-first}}\\
\nexists o:\ 0<d_o<d_w-10^{-6}
\end{array}
\right]
\]

| 변수 | 의미 | 단위 |
|---|---|---|
| \(V_i\) | sample \(i\)의 직접광 경로 가시성 | 0 또는 1 |
| \(\mathbf{n}_i\) | 보호면 수광 방향 단위 법선 | 무차원 |
| \(d_w\) | 창문 교차 거리 | m |
| \(d_{\mathrm{room-first}}\) | 광선이 처음 만나는 실 경계 거리 | m |
| \(d_o\) | 장애물 교차 거리 | m |

코드 근거:

- `engine/fr01/predictor.py` — `evaluate_sample()`
- `engine/fr01/geometry.py` — `first_room_boundary()`
- `engine/fr01/geometry.py` — `intersect_cuboid()`

Cuboid는 해당 yaw만큼 로컬 좌표계로 회전한 뒤 x/y/z 세 축의 진입·이탈 구간을 겹치는 slab 방식으로 교차한다. 차단 조건은 장애물 hit가 창문 hit보다 `1e-6 m` 이상 앞에 있는 경우다.

### 2.5 DNI 선택 우선순위

\[
DNI(t)=
\begin{cases}
DNI_{\mathrm{Scene.weather}}(t),&\text{유효·신선·범위 포함}\\
DNI_{\mathrm{Open-Meteo}}(t),&\text{그 외 Open-Meteo 범위 포함}\\
DNI_{\mathrm{pvlib\ clear-sky}}(t),&\text{그 외}\\
\mathrm{unknown},&\text{clear-sky 계산도 실패}
\end{cases}
\]

코드 근거:

- `backend/app/services/sunpath.py` — `_scene_dni()`
- 같은 파일 — `_open_meteo_snapshot()`
- 같은 파일 — `_clear_sky_dni()`
- 같은 파일 — `_select_dni()`

`Scene.weather`는 `W/m2`, `scene.site`, `max_age_s`, 요청 시각 coverage 등 검증을 통과해야 선택된다. `0`은 유효한 물리값이며, `null`은 미상이다. Open-Meteo 요청에는 `direct_normal_irradiance,cloud_cover`가 포함되지만, 현재 `cloud_cover`는 응답 유효성 검사에만 쓰이며 DNI 보정식에는 쓰이지 않는다. clear-sky DNI는 `pvlib.location.Location(...).get_clearsky()["dni"]`에 위임한다.

### 2.6 보호면 직접일사량

\[
I_{\mathrm{direct},i}=
\begin{cases}
DNI\cdot\max(0,\mathbf{n}_i\cdot\mathbf{s}),&V_i=1,\ DNI\neq\mathrm{unknown}\\
0,&V_i=0\\
\mathrm{unknown},&V_i=1,\ DNI=\mathrm{unknown}
\end{cases}
\]

| 변수 | 의미 | 단위 |
|---|---|---|
| \(I_{\mathrm{direct},i}\) | sample 직접일사량 | W/m² |
| \(DNI\) | 직달 법선 일사량 | W/m² |
| \(\mathbf{n}_i\) | 보호면 단위 법선 | 무차원 |
| \(\mathbf{s}\) | 태양 방향 단위벡터 | 무차원 |
| \(\mathbf{n}_i\cdot\mathbf{s}\) | 입사각 코사인 | 무차원 |

코드 근거:

- `engine/fr01/predictor.py` — `direct_irradiance_W_m2()`
- `backend/app/services/sunpath.py` — `predict_scene()` 내부에서 sample 응답을 만들 때 호출

#### 실제 수치 예시

테스트의 남향 보호면과 고도 45° 태양을 사용하면,

\[
\mathbf{n}=(0,-1,0),\quad
\mathbf{s}=\left(0,-\frac1{\sqrt2},\frac1{\sqrt2}\right),\quad
DNI=500\ \mathrm{W/m^2}
\]

\[
\mathbf{n}\cdot\mathbf{s}=\frac1{\sqrt2}\approx0.7071
\]

\[
I_{\mathrm{direct}}=500\times0.7071\approx353.55\ \mathrm{W/m^2}
\]

이는 `tests/test_fr01_prediction.py`에 있는 실제 회귀 테스트 기대값이다.

### 2.7 보호면 법선의 출처와 정규화

보호면 법선 \(\mathbf{n}\)은 꼭짓점으로부터 계산하지 않는다. FR-06 Scene의 `protected_regions[].receiving_normal`을 backend adapter가 읽어 `ProtectedRegion.receiving_normal`으로 전달한다.

- `backend/app/services/sunpath.py` — `adapt_scene()`
- `engine/fr01/models.py` — `ProtectedRegion.__post_init__()`

이 값은 자동 정규화되지 않는다. 입력 길이가 1에서 `1e-6` 이상 벗어나면 오류를 내므로, Scene이 이미 단위 법선을 제공해야 한다. 창문의 `inward_normal`, `u_axis`, `v_axis`도 같은 규칙이다.

## 3. 고려·제외 요소

| 요소 | 현재 반영 여부 | 실제 구현 |
|---|---|---|
| 태양 고도·방위 | 반영 | pvlib `nrel_numpy` |
| 시간대·UTC 정규화 | 반영 | aware timestamp를 UTC로 변환 |
| 창문 개구부 통과 | 반영 | 평면 교차 + 내부 직사각형 검사 |
| 실 벽·바닥·천장 | 반영 | 최초 경계가 창문인지 검사 |
| 실내 불투명 Cuboid | 반영 | 창문보다 앞선 교차면 차단 |
| DNI | 반영 | Scene.weather → Open-Meteo → pvlib clear-sky |
| 보호면 입사각 | 반영 | \(\max(0,\mathbf{n}\cdot\mathbf{s})\) |
| DHI/확산일사 | 제외 | 입력·계산·합산 없음 |
| GHI | 제외 | 계산식에 없음 |
| 유리 투과율 | 제외 | Scene 값은 있지만 FR-01 식에 곱하지 않음 |
| 필름 투과율·불투명도 | 제외 | `initial_film_direct_transmittance`도 계산식에 사용하지 않음 |
| 반사·굴절 | 제외 | service assumptions에 명시 |
| 외부 차폐 | 제외 | service assumptions에 명시 |
| penumbra/부분 그림자 | 제외 | 광선 경로는 이진 노출/차단 |
| 보호면 전체 연속 면적 | 제외 | 지정 sample point만 판정 |
| FR-02 셀 제어 | 제외 | FR-01이 제어 계산을 하지 않음 |
| FR-01.4 Stage 2 다중 source 미래 DNI 예측 | 미구현 | 현 구현은 Stage 1 단일 DNI 선택만 수행하며, Stage 2 계약은 `FR-01.4_contract_v1.md`에 정의됨 |

응답 스키마도 `exposure_basis="geometric_direct_path"`, `optics_applied=false`로 고정된다.

## 4. 발표용 핵심 수식

### 슬라이드 1 — 태양을 실내 좌표계로 가져오는 식

\[
\boxed{\mathbf{s}=\left(\cos\alpha\sin A,\;\cos\alpha\cos A,\;\sin\alpha\right)_{\mathrm{ENU}}}
\]

pvlib가 시각과 위치로 태양 고도각·방위각을 계산하고, SunSync는 이를 실내 ENU 좌표계의 단위 방향벡터로 바꾼다. 따라서 실제 방의 창·보호면·장애물 좌표와 태양 방향을 같은 좌표계에서 비교할 수 있다.

- \(\alpha\): 태양 고도각
- \(A\): 진북 기준 태양 방위각
- \(\mathbf{s}\): 표본점에서 태양을 향하는 ENU 단위벡터

선택적 차광과의 연결: 창문과 보호면의 방향이 달라도 공통 ENU 좌표계에서 햇빛이 어느 방향으로 들어오는지 일관되게 판정한다.

발표 대본: “SunSync는 태양의 고도와 방위각을 단순 표시값으로 쓰지 않습니다. 이를 동·북·상 ENU 좌표계의 단위벡터로 변환해 실제 실내 모델 안으로 가져옵니다. 그래서 창문, 보호면, 장애물의 위치와 태양광 방향을 하나의 3차원 기준에서 계산할 수 있습니다.”

### 슬라이드 2 — 햇빛 경로 판정식

\[
\boxed{
V_i=\mathbf{1}\!\left[
\alpha>0\land\mathbf{n}_i\cdot\mathbf{s}>0\land
d_w=d_{\mathrm{first\ boundary}}\land
\nexists o:\ 0<d_o<d_w
\right]}
\]

태양이 떠 있고 보호면 앞쪽에서 비추며, 광선이 실제 창문 개구부를 통해 나가고, 그 전에 장애물에 막히지 않을 때만 \(V_i=1\)이다. 이는 단순 창 방향 비교가 아니라 각 보호면 표본점에서 실행되는 3차원 ray tracing이다.

- \(V_i\): sample \(i\)의 노출 여부
- \(\mathbf{n}_i\): 보호면 수광 법선
- \(d_w\): 창문 교차 거리
- \(d_o\): 장애물 교차 거리

선택적 차광과의 연결: 어느 보호면의 어느 지점이 실제로 직사광선에 노출되는지를 식별하므로 필요한 구역만 차광 대상으로 만들 수 있다.

발표 대본: “두 번째 단계는 햇빛이 실제로 실내까지 도달하는지 확인하는 과정입니다. 보호면의 각 표본점에서 태양 쪽으로 광선을 쏘고, 그 광선이 창문을 통과하는지, 벽이나 장애물에 먼저 닿지는 않는지를 검사합니다. 따라서 SunSync는 햇빛이 들어올 가능성이 아니라 실제 3차원 경로를 기준으로 노출을 판단합니다.”

### 슬라이드 3 — 최종 직접일사량 식

\[
\boxed{I_{\mathrm{direct},i}=V_i\cdot DNI\cdot\max\!\left(0,\mathbf{n}_i\cdot\mathbf{s}\right)}
\]

직사광선 경로가 열려 있어도, 보호면이 태양을 정면으로 향할수록 더 큰 에너지를 받는다. DNI는 현장 weather, Open-Meteo, clear-sky 순으로 신뢰 가능한 값을 선택한다.

- \(I_{\mathrm{direct},i}\): 표본점 직접일사량 \([\mathrm{W/m^2}]\)
- \(DNI\): 직달 법선 일사량 \([\mathrm{W/m^2}]\)
- \(\mathbf{n}_i\cdot\mathbf{s}\): 입사각 코사인
- \(V_i\): 창문 통과·비차단 여부

선택적 차광과의 연결: 단순히 햇빛이 들어오는지뿐 아니라 얼마나 강하게 들어오는지를 수치화하므로 차광 필요도와 강도를 구분할 입력이 된다. 단, 현재 FR-01은 유리·필름 투과율을 적용하지 않으므로 이 값은 보호면에 도달한 기하학적 직달 성분이다.

발표 대본: “최종적으로 SunSync는 햇빛 경로가 열린 경우에만 DNI를 보호면 방향으로 투영합니다. 같은 DNI라도 햇빛이 보호면을 정면으로 비출수록 값이 커지고, 비스듬하면 작아집니다. 이 수치가 향후 선택적 차광에서 어느 영역을 얼마나 차단할지 결정하는 핵심 입력이 됩니다.”
