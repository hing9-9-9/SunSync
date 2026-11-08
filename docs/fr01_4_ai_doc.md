# FR-01.4 KMA AI 실측 실험 이해 및 분석

> **문서 역할:** DSR·DNI·Open-Meteo·분위수 예측을 처음 이해하기 위한 개념 설명서다.
> 현재 구현 사양과 수치는 [`fr01_4_ai_model_implementation.md`](fr01_4_ai_model_implementation.md)와
> [`fr01_4_ai_kma_analysis.md`](fr01_4_ai_kma_analysis.md)를 우선한다.

## 현재 구현 요약 — 2026-11-08

초기 계획과 달리 현재 모델은 ASOS나 GK2A 영상 patch가 아니라, **GK2A 지점 조회 API의
10분 DSR·CA**와 당시 이용 가능한 Open-Meteo DNI를 사용한다.

```text
입력             현재 KMA DSR·CA + 당시 이용 가능한 Open-Meteo DNI + 태양 위치
예측             10·30·60분 뒤 DSR 환산 DNI의 P10·P50·P90
모델             horizon별 선형 분위수 회귀
개발 기간        2026-10-05 ~ 2026-11-01
상태             학습·Validation·동결·JSON 저장/로드 완료
Final test       2026-11-02 ~ 2026-11-05, 아직 미평가
위성 patch       미사용, cloud_patch_used=false
```

DSR 환산 DNI는 독립 DNI 센서의 실측값이 아니다. 따라서 현재 결과는 “실제 DNI 정확도
증명”이 아니라 동일한 proxy 기준에서 모델과 Open-Meteo를 비교한 결과다.

## 1. 이 문서의 목적

이 문서는 GitHub Issue `#86 [FR-01-AI]`에서 요구하는 작업을 처음 접한 개발자가 다음 질문에 답할 수 있도록 정리한 설명서다.

- KMA에서 정확히 어떤 데이터를 가져와야 하는가?
- Open-Meteo 데이터는 왜 함께 필요한가?
- DSR, GHI, DNI는 각각 무엇이며 왜 변환이 필요한가?
- `KmaQuantileNowcaster`는 무엇을 입력받아 무엇을 예측하는가?
- 어떤 실험을 해야 KMA AI가 도움이 되는지 판단할 수 있는가?
- 현재 코드가 어디까지 되어 있고 무엇이 아직 합성 데이터인가?

이 문서의 범위는 **실데이터 비교 실험과 그 해석**이다. 운영 환경에서 KMA를 계속 수집하는 worker나 프론트엔드 기능 확장은 이번 이슈의 필수 범위로 보지 않는다.

관련 자료:

- GitHub Issue: [#86 FR-01-AI](https://github.com/hing9-9-9/SunSync/issues/86)
- 기존 설계 및 합성 벤치마크: [`fr01_4_kma_ai_nowcast.md`](fr01_4_kma_ai_nowcast.md)
- 운영 Open-Meteo DNI 설계: [`fr01_4_open_meteo_dni_design.md`](fr01_4_open_meteo_dni_design.md)
- 구현 계획: [`fr01_4_ai_implement.md`](fr01_4_ai_implement.md)

---

## 2. 한 문장으로 설명하는 개발 목표

**Open-Meteo의 1시간 평균 DNI 예보만 사용했을 때 놓치는 짧은 구름 변화를, KMA의 더 짧은 간격 지상·위성 관측으로 보정하고, 미래 DNI를 하나의 숫자가 아니라 `q10`, `q50`, `q90` 범위로 예측한 뒤 실제 자료에서 오차가 줄었는지 검증한다.**

여기서 가장 중요한 구분은 다음과 같다.

- Open-Meteo는 앞으로의 일사량을 알려주는 **예보**다.
- KMA 지상·위성 자료는 현재 또는 직전의 대기 상태를 알려주는 **관측 또는 분석값**이다.
- 모델은 둘을 결합해 가까운 미래의 DNI를 보정하는 **초단기 예측기(nowcaster)**다.

즉, KMA 자료 자체를 예측 결과로 그대로 사용하는 작업이 아니다. Open-Meteo의 큰 흐름 위에 최근 구름 상태를 반영해 가까운 미래 예보를 수정하는 작업이다.

---

## 3. 왜 이 기능이 필요한가

Open-Meteo의 hourly DNI는 한 시간 단위의 지역 예보다. 맑거나 넓은 층운이 계속되는 날에는 유용하지만, 작은 적운이 빠르게 태양 앞을 지나는 경우에는 실제 직사광이 몇 분 사이에 크게 변할 수 있다.

예를 들어 Open-Meteo가 오후 1시부터 2시까지 DNI를 평균 `700 W/m²`로 예보했더라도 실제 현장에서는 다음처럼 움직일 수 있다.

```text
13:00  760 W/m²  맑음
13:10  120 W/m²  구름이 태양을 가림
13:20   40 W/m²  두꺼운 구름
13:30  680 W/m²  구름이 지나감
13:40  740 W/m²  맑음
```

스마트 필름은 이 짧은 변화에 영향을 받는다. 단일 1시간 평균만 사용하면 눈부심을 늦게 막거나, 반대로 필요 없는 차광을 오래 유지할 수 있다. KMA 관측은 최근 구름 변화와 일사량 급락을 알려주므로 Open-Meteo 예보를 가까운 미래에 맞게 보정하는 단서가 된다.

---

## 4. 먼저 알아야 할 일사량 용어

### 4.1 GHI 또는 DSR

- **GHI(Global Horizontal Irradiance)**는 수평면에 도달한 전체 단파복사량이다.
- **DSR(Downward Shortwave Radiation)**도 지면으로 내려오는 단파복사량을 뜻하며, 이 프로젝트에서는 GHI에 가까운 입력으로 다룬다.
- 직달 성분과 하늘에서 산란된 성분이 함께 포함될 수 있다.

구름이 태양을 가려도 하늘 전체에서 오는 산란광은 남기 때문에 GHI/DSR이 완전히 0으로 떨어지지 않을 수 있다.

### 4.2 DHI

- **DHI(Diffuse Horizontal Irradiance)**는 대기와 구름에서 산란되어 수평면에 도달한 빛이다.
- 태양 원반에서 직접 오는 빛은 포함하지 않는다.

### 4.3 DNI

- **DNI(Direct Normal Irradiance)**는 태양 광선에 수직인 면이 받는 직접복사량이다.
- 창문을 통과하는 직접 햇빛, 눈부심, 바닥과 가구에 도달하는 직사광을 계산할 때 필요한 값이다.
- SunSync의 FR-01.1 기하 계산과 결합되는 핵심 일사량이다.

세 값의 관계를 단순화하면 다음과 같다.

```text
GHI = DNI × cos(태양 천정각) + DHI
```

따라서 KMA에서 GHI/DSR을 받았다고 해서 그것을 DNI로 바로 사용할 수 없다. 태양 위치와 산란 비율을 이용한 변환이 필요하다. 현재 구현은 clear-sky 값, clearness index, Erbs diffuse decomposition을 이용해 DSR에서 DNI prior를 만든다.

주의할 점은 이렇게 계산한 DNI가 **독립적으로 측정한 실제 DNI와 동일한 정답값은 아니라는 것**이다. DSR에서 변환한 값을 다시 모델 평가의 정답으로 사용하면 모델 입력과 정답이 서로 종속되어 성능이 과대평가될 수 있다.

---

## 5. 가져와야 할 데이터

### 5.1 초기 검토한 KMA 지상 관측 데이터 — V1 미사용

한 지점에서 최소 3일, 권장 7일 이상 다음 자료를 같은 시간축으로 수집한다.

| 데이터 | 권장 CSV 필드 | 모델에서의 역할 |
|---|---|---|
| 관측 시각 | `timestamp_utc` | 모든 자료를 동일 시각에 정렬한다. |
| 관측소 | `station_id` | 데이터 출처와 재현성을 기록한다. |
| 위도·경도 | `latitude`, `longitude` | 태양 위치와 clear-sky 일사량 계산에 사용한다. |
| 일사 또는 하향단파복사 | `dsr_W_m2` | DSR→DNI 물리 변환의 핵심 입력이다. |
| 전운량 | `cloud_cover_pct` | 구름 투과도와 변동성을 계산한다. |
| 기온 | `temperature_c` | EDA 및 향후 feature 후보로 저장한다. 현재 모델에서는 아직 사용하지 않는다. |
| 상대습도 | `humidity_pct` | EDA 및 향후 feature 후보로 저장한다. 현재 모델에서는 아직 사용하지 않는다. |

KMA API 원본이 누적 일사량을 `MJ/m²`로 제공한다면 관측 간격을 이용해 평균 `W/m²`로 변환해야 한다.

```text
평균 W/m² = 구간 일사량(MJ/m²) × 1,000,000 ÷ 구간 초
```

예를 들어 10분 동안 `0.3 MJ/m²`가 누적됐다면 평균값은 약 `500 W/m²`다. 원본이 순간값인지 누적값인지 확인하지 않고 변환하면 안 된다.

KMA 공식 자료에서 ASOS는 일사·구름·기온·습도 등을 제공하지만 지점과 요소별 보유 기간 및 관측 간격이 다를 수 있다. 실제 개발에서는 선택한 관측소가 필요한 네 요소를 모두 제공하는지 먼저 작은 기간으로 확인한다.

- [KMA API Hub 지상관측 API](https://apihub.kma.go.kr/apiList.do)
- [KMA 기상자료개방포털 ASOS 설명](https://data.kma.go.kr/data/grnd/selectAsosRltmList.do?pgmNo=36&tabNo=4)

### 5.2 V1에 사용한 GK2A 지점 일사·구름 데이터

위성 자료는 지상 관측소 한 점만 보는 대신 건물 주변에 구름이 어느 방향으로 분포하는지 알려준다. 모델이 원하는 최종 형태는 원본 영상 전체가 아니라 다음과 같은 작은 patch 요약값이다.

| 데이터 | 모델 필드 | 의미 |
|---|---|---|
| patch 크기 | `patch_size_km` | 건물 주변 몇 km를 요약했는지 나타낸다. |
| 평균 구름 비율 | `mean_cloud_fraction` | 주변이 전체적으로 얼마나 흐린지 나타낸다. |
| 바람 상류 방향 구름 기울기 | `upwind_cloud_gradient` | 구름이 다가오는 방향에 더 짙은 구름이 있는지 나타낸다. |
| 구름 이동 동서 성분 | `motion_u_m_s` | 구름 이동 방향과 속도의 동서 성분이다. |
| 구름 이동 남북 성분 | `motion_v_m_s` | 구름 이동 방향과 속도의 남북 성분이다. |
| 구름 꼭대기 높이 | `cloud_top_height_m` | 선택 입력이며 구름 특성 분석에 사용한다. |

기존 합성 코드는 patch 값을 받을 수 있지만 실제 GK2A 지점 API로 공간 patch를 만드는
전처리는 구현하지 않았다. V1은 단일 좌표의 DSR·CA만 사용한다.

이번 구현에서는 KMA API Hub의 **천리안2A호 기상산출물(복사, 구름, 안개) 지점 조회서비스**를 사용한다. 이 서비스는 생산시간 간격을 10분 단위로 조회할 수 있고 다음 변수를 제공한다.

| `varn` | 의미 | 이번 실험에서의 용도 |
|---|---|---|
| `DSR` | 지면 하향단파복사 | DSR→proxy DNI 변환과 최근 일사 변화 feature |
| `CA` | 운량 | 구름 투과도와 구름 변동 구간 분류 |
| `CLL` | 구름층 고도 | 선택 feature 및 EDA |
| `CT` | 운형 | 선택 EDA |

사용 endpoint:

```text
https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph_sun_sat_txt
```

핵심 요청 인자는 `tm1`, `tm2`, `int=10`, `varn`, `lat`, `lon`, `authKey`다. `tm1`과 `tm2`는 UTC이며 10분 경계로 맞춘다. `DSR`과 `CA`를 각각 직접 조회한 뒤 실제 응답의 timestamp, 단위, 결측 코드, 최신 자료 지연 시간을 확인해 parser 계약을 확정한다.

30분 단위의 **천리안2A호 인공지능 기반 일사량 데이터 지점 조회서비스**는 이번 최소 간격 원본 수집에는 사용하지 않는다. 필요하면 나중에 별도의 baseline 또는 비교 자료로 추가한다.

- [KMA API Hub 산업특화 태양광 API](https://apihub.kma.go.kr/specialApiList.do)

현재 V1은 patch 없이 지점 DSR·운량으로 학습했고 모든 결과에 `cloud_patch_used=false`를
기록한다. 따라서 “위성 이동 보정 효과를 검증했다”고 주장하지 않는다.

### 5.3 Open-Meteo 예보 데이터

Open-Meteo에서는 같은 위치와 같은 검증 기간의 다음 값을 사용한다.

| 데이터 | 필드 예시 | 역할 |
|---|---|---|
| 예보 발행/수집 시각 | `open_meteo_issued_at` | 미래 정보를 사용하지 않았음을 확인한다. |
| 예보 유효 시각 | `timestamp_utc` | KMA와 정답값에 시간 정렬한다. |
| DNI | `open_meteo_dni_W_m2` | baseline이자 앙상블 입력이다. |
| 구름량 | `open_meteo_cloud_cover_pct` | EDA와 보조 비교에 사용한다. |

중요한 원칙은 **비교 baseline과 AI 모델 입력에 동일한 Open-Meteo 예보 snapshot을 사용해야 한다**는 것이다. 현재 합성 실험은 별도로 생성한 `om_sim`을 baseline 계산에 사용하므로 실데이터 실험에서는 이 부분을 바꿔야 한다.

사후 분석용 historical/reanalysis 값을 당시의 예보처럼 사용해서도 안 된다. 가능한 경우 `issued_at`과 `valid_time`을 함께 보존해 예측 시점에 실제로 알 수 있었던 값만 사용한다.

### 5.4 평가용 정답 데이터

RMSE와 MAE를 계산하려면 예측 대상과 같은 물리량인 `reference_dni_W_m2`가 필요하다. 우선순위는 다음과 같다.

1. 동일하거나 가까운 위치에서 계측한 독립적인 DNI
2. 검증 가능한 별도 관측망의 direct-normal/direct-beam 자료
3. 독립 DNI가 없을 때 DSR에서 물리식으로 변환한 proxy DNI

이번 실험은 3번인 **DSR 기반 proxy DNI**를 사용한다. 보고서와 산출물에 `reference_type=dsr_proxy`를 반드시 표시한다. 따라서 이 실험은 “실제 DNI 정확도 증명”이 아니라 “동일한 DSR 기반 proxy 기준에서 Open-Meteo와 KMA 보정 모델을 비교한 초기 검증”이다.

proxy DNI는 평가 대상 시각의 DSR, 태양 위치, clear-sky GHI/DNI와 Erbs diffuse decomposition으로 계산한다. 이때 다음 누수 방지 규칙을 지킨다.

```text
예측 기준 시각 t0
입력: timestamp ≤ t0인 KMA/GK2A 자료
예측 대상: t0 + lead time
정답: 예측 대상 시각의 DSR로 사후 생성한 proxy DNI
```

예측 대상 시각의 DSR을 모델 입력과 proxy 정답에 동시에 사용하면 안 된다. 예를 들어 12:30의 proxy DNI를 평가할 때 모델 입력은 12:20까지 알 수 있었던 자료만 사용한다. 최소 lead time은 원자료 간격과 같은 10분으로 두고, 10·30·60분 lead time을 나누어 평가한다.

정답값이 없으면 다음 지표는 계산할 수 없다.

- KMA AI RMSE·MAE
- Open-Meteo RMSE·MAE
- q10/q50/q90 Pinball Loss
- q10~q90 Prediction Interval Coverage Probability(PICP)

정답 없이 계산 가능한 `KMA AI와 Open-Meteo 예측값 간 차이`는 모델 간 차이일 뿐 정확도가 아니다.

---

## 6. 최종 CSV 계약

정규화한 실험 CSV는 한 행이 한 시각을 나타내도록 한다.

```csv
timestamp_utc,station_id,latitude,longitude,dsr_W_m2,cloud_cover_pct,temperature_c,humidity_pct,open_meteo_issued_at,open_meteo_dni_W_m2,reference_dni_W_m2,patch_size_km,mean_cloud_fraction,upwind_cloud_gradient,motion_u_m_s,motion_v_m_s,quality_flag
2026-11-01T03:00:00Z,108,37.5714,126.9658,612.4,30,16.8,48,2026-11-01T00:05:00Z,680.0,645.2,10,0.24,0.07,3.2,-1.1,ok
```

필수 컬럼:

- `timestamp_utc`
- `latitude`, `longitude`
- `dsr_W_m2`
- `cloud_cover_pct`
- `open_meteo_dni_W_m2`
- `reference_dni_W_m2`

위성 patch 컬럼은 첫 실측 실험에서는 nullable로 허용한다. 누락값을 0으로 바꾸면 “구름이 없음”과 “자료가 없음”을 구분할 수 없으므로 비어 있는 값과 실제 0을 구분해야 한다.

원본과 정규화 데이터에는 다음 메타데이터도 함께 저장한다.

- API/상품명과 endpoint
- 수집 시각
- 요청 기간과 지점
- 원본 단위와 변환식
- 시간대
- 결측률과 제외 행 수
- 정답 DNI의 출처 또는 proxy 생성 방법

---

## 7. 구축할 모델

### 7.1 모델의 입력과 출력

현재 모델은 `KmaQuantileNowcaster`이며 두 단계로 이해할 수 있다.

```text
KMA DSR + 태양 위치
        │
        ▼
Stage 1: 물리 변환
DSR → clearness index → diffuse 비율 → DNI prior
        │
        ├──────────── Open-Meteo DNI
        ├──────────── 지상 운량
        └──────────── GK2A cloud patch 이동 정보
                        │
                        ▼
Stage 2: Quantile 회귀
                        │
                        ▼
                 q10 / q50 / q90 DNI
```

모델의 의미는 다음과 같다.

- `q50`: 중앙 예측값이다. 일반적인 단일 DNI 예측으로 사용한다.
- `q10`: 실제 DNI가 이 값보다 낮을 가능성이 약 10%가 되도록 학습하는 하한 예측이다.
- `q90`: 실제 DNI가 이 값보다 낮을 가능성이 약 90%가 되도록 학습하는 상한 예측이다.
- `q90 - q10`: 예측 불확실성 범위다. 구름 변화가 심하면 넓어지는 것이 자연스럽다.

정상적인 출력은 항상 다음 조건을 만족해야 한다.

```text
0 ≤ q10 ≤ q50 ≤ q90 ≤ clear_sky_DNI × 1.05
```

밤에는 세 값 모두 0이어야 한다.

### 7.2 실제 feature

현재 quantile ensemble의 feature는 다음 다섯 개다.

```text
[절편, KMA 물리 DNI prior, Open-Meteo DNI prior, 구름 투과도, 구름 이동 감쇠]
```

- KMA 물리 DNI prior: DSR에서 Erbs 방식으로 변환한 DNI를 clear-sky DNI로 정규화한다.
- Open-Meteo DNI prior: Open-Meteo DNI를 clear-sky DNI로 정규화한다.
- 구름 투과도: `1 - cloud_cover` 형태로 사용한다.
- 구름 이동 감쇠: 위성 patch의 gradient, 이동 속도, 예측 lead time을 결합한다.

기온과 습도는 현재 `KmaPointObservation`에는 있지만 feature에 포함되지 않는다. 첫 실험에서는 EDA 변수로 보존하고, 검증 결과와 물리적 근거 없이 feature를 늘리지 않는다. 향후 ablation 실험에서 도움이 확인되면 추가한다.

### 7.3 학습 방법

각 quantile마다 pinball loss를 최소화하도록 선형 가중치를 학습한다. 무작위 train/test 분할은 시계열의 미래 정보를 과거 학습에 섞을 수 있으므로 사용하지 않는다.

권장 방식:

```text
초기 60% 시간 구간: 학습
다음 20% 시간 구간: 검증 및 임계값 확인
마지막 20% 시간 구간: 최종 테스트
```

데이터가 3일뿐이면 표본의 시간 상관성이 매우 커서 일반화 성능을 주장하기 어렵다. 3~7일은 파이프라인과 초기 EDA 검증에 사용하고, 모델 성능에 대한 강한 결론은 더 긴 기간과 다양한 계절 자료를 확보한 뒤 내린다.

### 7.4 예측 시 데이터 누수 방지

예측 시각 `t`의 결과는 반드시 `t` 이전에 알 수 있었던 정보만 사용해야 한다.

현재 코드는 요청에 들어온 KMA 관측 중 가장 마지막 값을 전체 예측에 사용한다. 실측 CSV 전체를 한 요청에 전달하면 테스트 구간 뒤쪽 관측값이 앞쪽 예측에 사용될 수 있다. 실험 runner는 다음과 같은 rolling-origin 방식으로 호출해야 한다.

```text
12:00 예측 → 12:00 이전 KMA 관측만 사용
12:10 예측 → 12:10 이전 KMA 관측만 사용
12:20 예측 → 12:20 이전 KMA 관측만 사용
```

Open-Meteo도 각 `valid_time`에 대해 예측 시점 전에 발행된 snapshot만 선택한다.

---

## 8. 수행할 실험

### 8.1 실험 A: 합성 회귀 실험

목적은 기존 알고리즘과 물리 제약이 코드 변경 뒤에도 유지되는지 확인하는 것이다.

시나리오:

- 맑음
- 급격한 구름 이동
- 흐림

이 실험은 모델의 실제 성능 증거가 아니다. 입력과 정답을 코드가 만든 합성값이므로 회귀 테스트와 동작 설명에 사용한다.

### 8.2 실험 B: 실측 전체 구간 비교

같은 timestamp에 대해 다음 두 결과를 비교한다.

```text
Baseline: Open-Meteo DNI
Candidate: KMA + Open-Meteo quantile model의 q50
Ground truth: reference DNI
```

계산 지표:

- Open-Meteo RMSE·MAE
- KMA AI q50 RMSE·MAE
- RMSE·MAE 개선율
- q10/q50/q90별 Pinball Loss
- q10~q90 PICP
- 평균 uncertainty spread
- 유효 표본 수와 결측률

### 8.3 실험 C: 기상 구간별 비교

전체 평균만 보면 구름이 급변할 때의 효과가 맑은 시간에 묻힐 수 있다. 결과를 보기 전에 고정한 규칙으로 구간을 나누고 같은 지표를 계산한다.

- 맑음: 높은 clearness index, 낮은 운량과 낮은 변동성
- 급격한 구름 이동: 짧은 시간 DSR 변화율 또는 cloud gradient가 큼
- 흐림: 낮은 clearness index와 높은 운량이 지속

3~7일 안에 세 조건이 모두 나타나지 않을 수 있다. 없는 조건을 억지로 만들거나 표본이 매우 적은 결과를 일반화하지 않는다. 합성 데이터에서는 세 조건을 항상 시험하고, 실측 데이터에서는 실제 존재하는 구간만 보고한다.

### 8.4 실험 D: 시간대별 비교

태양 고도가 낮은 아침·저녁에는 작은 GHI 오차가 DNI 변환에서 크게 증폭될 수 있다. 다음처럼 나누어 오차를 확인한다.

- 오전
- 태양 고도가 높은 정오 부근
- 오후
- 낮은 태양 고도 구간

밤 데이터는 DNI가 0이어서 전체 성능을 인위적으로 좋게 만들 수 있으므로 주간 지표와 별도로 보고한다.

### 8.5 실험 E: ablation

각 데이터가 실제로 도움이 됐는지 확인하려면 입력을 하나씩 제외한다.

1. Open-Meteo only
2. Open-Meteo + KMA DSR/운량
3. Open-Meteo + KMA DSR/운량 + GK2A patch

3번이 2번보다 좋아야 위성 patch의 가치를 주장할 수 있다. 위성 자료가 없으면 1번과 2번까지만 수행한다.

---

## 9. 결과 해석 기준

모델의 목적에 부합하는 결과는 다음과 같다.

- 전체 또는 구름 급변 구간에서 q50 RMSE·MAE가 Open-Meteo보다 감소한다.
- q10~q90의 nominal 80% 구간이 실제 DNI를 적절한 비율로 포함한다.
- 맑은 날에 불필요하게 uncertainty가 커지지 않는다.
- 구름 급변 구간에서는 uncertainty가 넓어져 위험을 표현한다.
- 밤과 물리 상한에서 비정상 값이 없다.

반대로 다음 결과는 실패 또는 추가 개선 신호다.

- 전체 평균은 좋아졌지만 구름 급변 구간에서 더 나빠진다.
- q50은 좋아졌지만 PICP가 지나치게 낮아 q10/q90이 과도하게 좁다.
- 항상 범위가 매우 넓어서 PICP만 높다.
- Open-Meteo 단독보다 좋아진 결과가 학습 구간에만 나타난다.
- 미래 관측이나 사후 재분석 자료를 사용했을 때만 좋아진다.

3~7일 결과는 “모델이 우월함을 증명”하는 최종 결론보다 다음처럼 표현하는 것이 타당하다.

> 재현 가능한 KMA 실측 비교 파이프라인을 구축했고, 수집 기간의 특정 기상 구간에서 KMA 보정의 초기 개선 가능성을 확인했다.

---

## 10. 구현 상태

현재 완료된 항목:

- KMA API 수집기, 정규화 CSV, durable queue와 중단 복구
- 28일 KMA·Open-Meteo 개발 dataset과 시간 누수 방지
- 10·30·60분 P10·P50·P90 선형 분위수 모델
- Validation 전체·시간대·태양 고도·구름 구간 평가
- 동결 모델 JSON, SHA-256 검증 loader와 저장·재로드 동일성 검사

남은 항목은 final test용 Open-Meteo archived run 준비, final test 1회 평가, 팀 검토 후
운영 API 연결이다. 실제 GK2A 영상 patch와 cloud motion은 V1 범위가 아니다.

<details>
<summary>초기 작성 당시 구현 분석 보기</summary>

### 초기 구현 분석

이미 구현된 항목:

- DSR→DNI 물리 변환
- q10/q50/q90 quantile ensemble
- 물리 범위와 quantile 단조성 보장
- Open-Meteo DB snapshot 조회와 fallback
- RMSE·MAE·Pinball Loss 계산 구조
- 합성 Clear/Intermittent/Overcast 벤치마크
- API, DB 저장, 프론트엔드 비교 차트
- 관련 테스트 12개 통과

아직 구현되지 않은 항목:

- KMA API 수집기
- 실제 GK2A patch 전처리
- 표준 CSV 계약과 검증기
- `--kma-csv` CLI
- 실측 feature/target dataset builder
- 시간순 train/validation/test 분리
- rolling-origin 예측
- 실제 Open-Meteo snapshot을 사용한 공정한 baseline 비교
- 실측 EDA 그래프와 결과 파일
- 모델 가중치 저장·재사용 방식

현재 `docs/fr01_4_kma_ai_nowcast.md`의 benchmark 표는 합성 결과다. 실측 결과와 섞이지 않도록 향후 문서에서 `Synthetic Benchmark`와 `Real-data Evaluation`을 별도 절로 유지한다.

</details>

---

## 11. 확정된 결정

### 결정 1: 평가용 DNI 정답 — 확정

- DSR 기반 proxy DNI를 사용한다.
- 모든 결과에 `reference_type=dsr_proxy`를 기록한다.
- 독립 실측 DNI 성능으로 표현하지 않는다.
- 예측 대상 시각의 DSR은 정답 생성에만 사용하고 입력 cutoff 이후 정보는 모델에 전달하지 않는다.

### 결정 2: GK2A 상품과 시간 간격 — 확정

- `nph_sun_sat_txt` 기상산출물 지점 조회서비스를 사용한다.
- `int=10`으로 API가 제공하는 최소 10분 간격을 요청한다.
- 필수 변수는 `DSR`, `CA`다.
- V1 필수 변수는 `DSR`, `CA`다. `CLL`, `CT`와 영상 patch는 후속 후보로 남긴다.
- API 키 발급·활용신청·직접 query와 응답 계약 확인을 완료했다.

### 결정 3: 확정된 개발 범위

좌표 `37.5714, 126.9658`의 GK2A DSR·CA 28일과 ECMWF IFS 0.25° archived run을
사용한다. 실제 존재하는 구간만 평가하며 patch·기온·습도는 V1 feature에 포함하지 않는다.

### 결정 4: 성공 기준

성공 기준은 “항상 더 정확함”이 아니라 다음으로 잡았다.

- 실데이터가 자동으로 수집·정규화된다.
- 재현 가능한 dataset·학습·평가 명령이 실행된다.
- 미래 데이터 누수 없이 지표와 JSON artifact가 만들어진다.
- 데이터와 정답의 한계가 문서화된다.

성능 개선 여부는 그 파이프라인에서 얻는 실험 결과이며, 미리 보장할 완료 조건이 아니다.
