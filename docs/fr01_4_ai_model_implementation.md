# FR-01.4 KMA AI DNI 선형 분위수 모델 구현·동결 사양

> **상태:** 2026-11-08에 Stage 1~6, V1 동결과 Final test 1회 평가를 완료했다.
> **분류:** PR·팀 공유용 기준 문서다. V1 재튜닝은 금지한다. 운영 연결 코드는 구현됐으며
> 기본 Shadow에서 Active로 전환하는 일과 서버 배포는 팀 검토 후 진행한다.

## 현재 모델 한눈에 보기

```text
모델 버전        fr01_kma_quantile_v1
모델 종류        horizon별 P10·P50·P90 선형 분위수 회귀
입력             현재 KMA DSR·CA, 당시 이용 가능한 Open-Meteo DNI, 태양 위치
출력             10·30·60분 뒤 DSR 환산 DNI의 P10·P50·P90
개발 데이터      2026-10-05 ~ 2026-11-01
운영 artifact    backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1/
Final test       2026-11-02 ~ 2026-11-05, 1회 평가 완료
```

## 1. 문서 목적

이 문서는 FR-01-4의 **모델 부분만** 다룬다. KMA API 수집기, durable queue, 원본 파일 보존과 일반 EDA는 다른 문서의 범위다.

구현할 V1 모델은 다음과 같다.

> KMA DSR로 만든 태양 위치 보정 DNI 기준값과 Open-Meteo DNI, KMA 운량을 입력으로 사용해 10분 뒤 DSR 환산 DNI의 P10·P50·P90을 예측하는 선형 분위수 모델

이 문서에서는 다음 용어를 사용한다.

- **태양 위치 보정 DNI 기준값**: 현재 시각의 KMA DSR을 태양 고도와 맑은 하늘 DNI를 이용해 DNI 형태로 환산한 값이다. 모델이 최종 예측을 만들 때 사용하는 출발점이며, 코드 field는 `KMA_DNI_prior`다.
- **10분 뒤 DSR 환산 DNI 정답값**: 예측 기준 시각에서 10분 뒤의 KMA DSR을 같은 방식으로 DNI 형태로 환산한 학습·평가용 정답이다. 코드 field는 `reference_dni_W_m2`, reference 종류는 `dsr_proxy`다.

첫 실험의 정답은 실제 계측 DNI가 아니라 **10분 뒤 DSR 환산 DNI 정답값**이다. 따라서 결과도 실제 DNI 정확도 증명이 아니라 DSR 환산값을 기준으로 한 초기 비교 결과로 해석한다.

---

## 2. 구현 범위와 제외 범위

### 이번 구현 범위

- DSR을 태양 위치 보정 DNI 기준값으로 변환하는 계산
- 시간 누수를 방지한 학습용 행 생성
- P10·P50·P90 선형 분위수 모델 학습
- 모델 가중치와 학습 metadata 저장·로드
- rolling-origin 방식의 test 예측
- 동일 Open-Meteo 값과의 성능 비교
- 전체·예측 시간별 성능 및 불확실성 지표 출력

### 이번 구현에서 제외

- Gradient Boosting을 주 모델로 교체
- 신경망 또는 영상 모델
- 실제 GK2A 영상 patch 추출과 cloud motion 계산
- 기온·습도 feature 추가
- 학습 결과를 운영 API에 자동 배포
- 적은 test 결과에 맞춘 반복 튜닝

Gradient Boosting은 V1 완료 후 동일 데이터 분할에서 비교할 수 있는 challenger 모델로만 검토한다.

---

## 3. 모델 구조

```text
KMA DSR + 태양 위치
        |
        v
Stage 1. 태양 위치 보정
DSR -> Erbs 분해 -> 태양 위치 보정 DNI 기준값
        |
        +---------------- Open-Meteo DNI
        +---------------- KMA CA
        +---------------- cloud advection(현재는 0)
        |
        v
Stage 2. 선형 분위수 회귀 3개
        |
        +---- P10: f10(x) = W10 x + b10
        +---- P50: f50(x) = W50 x + b50
        +---- P90: f90(x) = W90 x + b90
        |
        v
clear-sky DNI를 곱해 W/m²로 복원
```

각 모델은 DNI 자체가 아니라 맑은 하늘 대비 DNI 비율을 예측한다.

```text
k = DNI / clear_sky_DNI
DNI_q = clip(k_q, 0, 1.05) * clear_sky_DNI
```

야간 또는 `clear_sky_DNI <= 0`이면 P10·P50·P90은 모두 0으로 반환한다.

---

## 4. 학습 표본의 시간 정의

학습 데이터의 한 행은 다음 세 시각 정보를 가진다.

| 필드 | 의미 |
|---|---|
| `prediction_origin_utc` | 예측을 수행한다고 가정하는 시각 |
| `horizon_minutes` | 10, 30, 60분 |
| `valid_time_utc` | `prediction_origin + horizon` |

예를 들어 12:20에 12:30을 예측하면 다음 규칙을 적용한다.

```text
입력: 12:20까지 관측하거나 발행된 자료만 사용
정답: 12:30 DSR로 만든 DSR 환산 DNI 정답값
금지: 12:30 DSR·CA를 입력 feature에 사용
금지: 12:20 이후 발행된 Open-Meteo snapshot 사용
```

이 규칙을 dataset builder와 test에서 자동 검증한다.

---

## 5. 입력 field와 feature

### 5.1 원본 입력 field

| 필드 | 출처 | 역할 |
|---|---|---|
| `dsr_W_m2` | KMA GK2A | prediction origin 시점의 태양 위치 보정 DNI 기준값 생성 |
| `cloud_cover_pct` | KMA GK2A CA | 구름 투과율 계산 |
| `open_meteo_dni_W_m2` | Open-Meteo snapshot | 예보 기준값 및 baseline |
| `open_meteo_issued_at` | Open-Meteo snapshot | 시간 누수 확인 |
| `latitude`, `longitude` | site 설정 | 태양 위치와 clear-sky 값 계산 |
| `prediction_origin_utc` | dataset builder | 입력 cutoff |
| `valid_time_utc` | dataset builder | 예측 대상 시각 |
| `horizon_minutes` | dataset builder | lead time 계산 |

현재 수집 데이터에는 실제 위성 patch와 cloud motion이 없으므로 다음 상태를 명시한다.

```text
cloud_patch_used = false
advection_impact = 0.0
```

기온과 습도는 첫 모델의 feature로 사용하지 않는다.

### 5.2 태양 위치 보정 DNI 기준값

KMA DSR은 DNI가 아니므로 `pvlib.irradiance.erbs()`와 태양 천정각을 이용해 DNI 형태의 기준값으로 변환한다. Erbs의 clearness index는 clear-sky GHI가 아니라 대기권 밖 수평면 일사량을 기준으로 계산되며, 직접 구현하지 않고 pvlib 공식 구현을 사용한다.

```text
Erbs 결과 = pvlib.irradiance.erbs(DSR, solar_zenith, timestamp)
kt = Erbs 결과의 clearness index
DHI = Erbs 결과의 diffuse horizontal irradiance
KMA_DNI_prior = Erbs 결과의 DNI
```

태양 고도가 너무 낮으면 불안정한 나눗셈을 피하고, 결과는 다음 범위로 제한한다.

```text
0 <= KMA_DNI_prior <= clear_sky_DNI * 1.05
```

### 5.3 최종 feature vector

모델 입력은 정확히 5열로 고정한다.

| 순서 | feature | 계산 |
|---:|---|---|
| 0 | `intercept` | `1.0` |
| 1 | `direct_prior_factor` | KMA의 태양 위치 보정 DNI 기준값과 Open-Meteo 예보값의 lead-time 가중 결합 |
| 2 | `open_meteo_factor` | `open_meteo_dni / clear_sky_dni` |
| 3 | `cloud_transmittance` | `1 - cloud_cover_pct / 100` |
| 4 | `advection_impact` | 현재 `0.0`, patch 구현 후 계산 |

두 DNI 기준값을 결합하는 feature는 다음과 같다.

```text
k_erbs = KMA_DNI_prior / clear_sky_DNI
k_om = Open-Meteo_DNI / clear_sky_DNI
obs_weight = exp(-horizon_hours / 1.5)
direct_prior_factor = k_erbs * obs_weight + k_om * (1 - obs_weight)
```

최종 입력:

```text
X = [1.0, direct_prior_factor, k_om, cloud_transmittance, 0.0]
```

첫 구현에서는 intercept를 `X`의 첫 열에 포함하므로 별도 `b`를 두지 않는다. 따라서 코드상 계산은 `X @ W`지만 수학적으로는 `Wx + b`와 같다.

---

## 6. 10분 뒤 DSR 환산 DNI 정답값

예측 기준 시각에서 10분 뒤의 DSR로 `reference_dni_W_m2`를 생성하고 다음처럼 정규화한다. 이 값은 실제 DNI 센서 관측값이 아니라 모델 학습과 평가를 위한 DSR 환산값이다.

```text
reference_dni_factor = reference_dni_W_m2 / clear_sky_dni_W_m2
y = clip(reference_dni_factor, 0.0, 1.1)
reference_type = "dsr_proxy"
```

10분 뒤 DSR 환산 DNI 정답값을 만드는 데 사용한 미래 DSR은 입력 feature에 전달하지 않는다. `clear_sky_dni`가 0이거나 태양 고도가 기준 이하인 표본은 주간 모델 학습에서 제외하고 야간 규칙으로 처리한다.

---

## 7. 학습 방식

분위수 `q`마다 독립된 가중치 `Wq`를 학습한다.

```text
prediction_q = X @ Wq
q in {0.10, 0.50, 0.90}
```

손실 함수는 pinball loss다.

```text
error = y - prediction
loss_q = max(q * error, (q - 1) * error)
```

구현 원칙:

- P10·P50·P90 모델을 각각 학습한다.
- P10·P50·P90 모두 첫 구현과 같은 `L-BFGS-B`와 pinball loss를 사용한다.
- validation으로 데이터 계약과 모델 동작을 확인하되 final test 결과를 보고 가중치를 다시 튜닝하지 않는다.
- 최적화 실패 시 조용히 성공 처리하지 않고 해당 quantile과 원인을 기록한다.
- 최소 표본 수, 결측률과 feature 분산을 학습 전에 검사한다.
- 고정 seed와 동일 입력으로 동일 결과가 나와야 한다.

첫 4단계의 weight 범위는 다음과 같이 유지한다.

| Weight | 범위 | 의미 |
|---|---:|---|
| `intercept` | `-0.5 ~ 0.5` | 다른 입력과 무관하게 더하는 기본 보정값 |
| `direct_prior_factor` | `0.0 ~ 1.5` | KMA DSR 기반 DNI 기준값의 영향 |
| `open_meteo_factor` | `0.0 ~ 1.5` | Open-Meteo DNI 예보의 영향 |
| `cloud_transmittance` | `-0.5 ~ 0.5` | 현재 구름 투과율에 따른 보정 영향 |
| `advection_impact` | `0.0` 고정 | 구름 이동 영향이며 현재는 미구현이라 사용하지 않음 |

초기 10분 사전 점검의 P90 intercept는 `0.385`로 상한에 닿지 않았다. 이후 60분 모델은
상한 `0.5`에 도달해 Validation에서만 `±0.75` 후보와 한 번 비교했고, pinball loss와
구간 폭이 나빠 기존 `±0.5`를 유지했다. Diagnostic과 final test는 이 결정에 사용하지 않았다.

예측 후에는 다음 순서 제약을 적용한다.

```text
P10 = clip(raw_P10, 0, clear_sky_DNI * 1.05)
P50 = max(P10, clip(raw_P50, 0, clear_sky_DNI * 1.05))
P90 = max(P50, clip(raw_P90, 0, clear_sky_DNI * 1.05))
```

분위수 교차가 자주 발생하면 단순 후처리 결과만 보고하지 않고 원래 교차율도 진단 지표로 남긴다.

---

## 8. 데이터 분할

무작위 분할은 사용하지 않고 UTC 시간순으로 분리한다.

초기 14일 개발 데이터의 분할안:

```text
train      앞 24일
validation 다음 2일
diagnostic 마지막 2일
```

구체적인 기간은 다음과 같이 고정한다.

```text
train       2026-10-05 00:00Z ~ 2026-10-28 23:50Z
validation  2026-10-29 00:00Z ~ 2026-10-30 23:50Z
diagnostic  2026-10-31 00:00Z ~ 2026-11-01 23:50Z
final test  2026-11-02 00:00Z ~ 2026-11-05 23:50Z
```

`diagnostic` 구간은 첫 P50 파이프라인 실행에서 이미 결과를 확인했으므로 이제 최종 성능 주장에 사용하지 않는다. P10·P90 확장과 모델 구조 확정에는 train·validation만 사용한다.

### Split 경계 embargo 규칙

날짜만으로 행을 나누지 않고, 각 행의 **정답 시각(`valid_time`)이 다음 split 시작 전인지** 함께 검사한다.

```text
train 행:      prediction_origin < validation_start
                AND valid_time < validation_start

validation 행: validation_start <= prediction_origin < diagnostic_start
                AND valid_time < diagnostic_start

diagnostic 행: diagnostic_start <= prediction_origin < final_test_start
                AND valid_time < final_test_start
```

따라서 split 경계 직전에는 horizon 길이만큼 행을 사용하지 않는 embargo 구간이 생긴다. 10분 간격 자료에서 다음 split이 00:00에 시작한다면 마지막 허용 origin은 10분 horizon `23:40`, 30분 `23:20`, 60분 `22:50`이다. 구현에서는 각 split마다 `max(valid_time) < next_split_start`를 assertion으로 검증한다.

### 확정된 재학습·final test 절차

`diagnostic` 구간을 추가 학습 자료로 쓰는 것은 허용하되, 시점과 용도를 엄격히 제한한다.

```text
1. 10/05~10/28 train + 10/29~10/30 validation으로
   P10·P50·P90 feature, 물리 제약, loss, horizon을 확정한다.

2. validation 결과까지 확인한 뒤 모델 사양을 동결한다.
   10/31~11/01 diagnostic 결과를 근거로 사양을 바꾸지 않는다.

3. 동결된 사양 그대로 10/05~11/01의 28일 전체를 사용해
   P10·P50·P90 최종 가중치를 한 번 재학습한다.

4. 11/02~11/05 final test에는 재학습된 모델을 적용해
   한 번만 예측·평가한다. 이 결과를 본 뒤 모델을 수정하지 않는다.
```

따라서 `diagnostic`은 validation이나 성능 선택 기준으로 재사용하지 않으며, **사양 동결 뒤 최종 가중치 추정에만** 포함한다. 이 재학습 이후에는 validation·diagnostic 모두 독립 성능 지표가 아니라 개발 이력으로 보관한다.

최종 성능 수치는 동결된 `final test`에서 한 번만 계산한다. 데이터가 짧으므로 일반화나 계절 성능을 강하게 주장하지 않는다.

### Final test set 동결 규칙

`2026-11-02T00:00:00Z`부터 `2026-11-05T23:50:00Z`까지의 KMA GK2A DSR·CA 576행은 final test set으로 지정한다. 원본과 정규화 파일은 아래와 같다.

```text
data/fr01/archive/frozen_final_test/final_test_20261102_20261105/kma_gk2a.csv
data/fr01/archive/source_and_intermediate/manifests/20261102T000000Z_20261105T235000Z_collection.json
```

이 기간은 다음 작업에서 **엄격히 금지**한다.

- P10·P50·P90 가중치 학습
- feature 추가·삭제 또는 전처리 규칙 선택
- 모델 종류, hyperparameter, 물리 범위·보정 계수 선택
- horizon, 결측 처리, regime 기준 선택
- validation 지표 비교와 오류 사례를 근거로 한 코드 수정

허용되는 사용은 final 모델 사양이 동결된 뒤의 한 번의 예측·평가뿐이다. 이때 KMA DSR·CA는 prediction origin 시점의 입력으로, 10분 뒤 DSR은 **DSR 환산 DNI 정답값** 생성에만 사용한다. final test 결과를 확인한 뒤 모델을 수정하면 해당 test set은 더 이상 final test가 아니며, 새 holdout 기간을 추가로 확보해야 한다.

Open-Meteo final test 자료도 동일 기간의 archived run으로 별도 수집한다. run 초기화 시각과 보수적 `available_at` 규칙은 개발 데이터와 동일하게 적용하되, 해당 값 역시 학습·선택 과정에는 전달하지 않는다.

`train_fr01_kma_p50_first.py`는 이 기간과 겹치는 KMA CSV를 학습 입력으로 받으면 오류를 내고 종료한다. final test 평가용 코드는 별도 명령으로 구현하며, 이 training guard를 우회하지 않는다.

---

## 9. 구현 파일 계획

### 신규 파일

```text
backend/app/nowcast/kma_model_dataset.py
```

책임:

- 결합 CSV schema 검증
- prediction origin과 horizon별 학습 행 생성
- 태양 위치 보정 DNI 기준값과 5개 feature 생성
- 10분 뒤 DSR 환산 DNI 정답값 생성
- 누수 검사
- 시간순 split 생성

```text
tools/ai_experiments/train_fr01_kma_quantile_model.py
```

책임:

- dataset builder 호출
- P10·P50·P90 학습
- validation·test 예측
- baseline과 지표 계산
- 모델 및 결과 artifact 저장
- 터미널 요약 출력

### 수정 파일

```text
backend/app/nowcast/kma_ml.py
```

수정 범위:

- `QuantileGradientEnsemble`을 역할이 명확한 이름으로 정리하거나 호환 alias 유지
- `fit`, `predict`, `save`, `load` 책임 분리
- feature 이름과 순서를 상수로 고정
- 학습 실패와 model fitted 상태를 명시적으로 반환
- 원시 분위수와 제약 적용 후 분위수를 모두 검사 가능하게 구성

기존 API 예측 동작은 모델 실험이 검증되기 전까지 자동으로 교체하지 않는다.

---

## 10. 모델 artifact 계약

```text
artifacts/fr01/<run-id>/
  model.json
  run_metadata.json
  dataset_summary.json
  predictions.csv
  metrics_overall.json
  metrics_by_horizon.csv
```

`model.json` 필수 내용:

```json
{
  "model_type": "linear_quantile_regression",
  "model_version": "fr01-kma-quantile-v1",
  "quantiles": [0.1, 0.5, 0.9],
  "feature_names": [
    "intercept",
    "direct_prior_factor",
    "open_meteo_factor",
    "cloud_transmittance",
    "advection_impact"
  ],
  "weights": {
    "0.1": [],
    "0.5": [],
    "0.9": []
  },
  "reference_type": "dsr_proxy",
  "cloud_patch_used": false
}
```

`run_metadata.json`에는 입력 파일 hash, 데이터 기간, split 경계, 좌표, timezone, horizon, 태양 고도 기준, 코드 commit과 실행 명령을 기록한다.

---

## 11. 평가 방법

동일한 test timestamp와 동일한 Open-Meteo snapshot으로 비교한다.

```text
Baseline (비교 기준): Open-Meteo DNI
Candidate (평가 대상): 선형 분위수 모델 P50
Reference (채점 정답): 10분 뒤 DSR 환산 DNI 정답값 (`reference_type=dsr_proxy`)
```

필수 지표:

| 지표 | 목적 |
|---|---|
| MAE | 평균 절대 오차 비교 |
| RMSE | 큰 오차에 민감한 비교 |
| P10·P50·P90 Pinball Loss | 각 분위수 품질 |
| PICP | reference가 P10~P90 안에 들어간 비율 |
| Mean interval width | 불확실성 구간 폭 |
| Quantile crossing rate | 순서 보정 전 분위수 교차 진단 |
| Sample count / missing rate | 결과 신뢰도 확인 |

다음 단위로 나누어 출력한다.

- 전체 주간 표본
- horizon 10·30·60분별
- 시간대별
- 맑음·흐림·급변 구간별

성공 판단은 단순히 전체 RMSE 하나만 낮은 경우가 아니다.

- P50의 MAE 또는 RMSE가 동일 표본의 Open-Meteo baseline보다 개선되는가?
- P10~P90이 지나치게 넓지 않으면서 reference를 합리적으로 포함하는가?
- 특정 horizon이나 날씨 구간에서만 성능이 붕괴하지 않는가?
- test에서만 실패하고 train에서만 좋은 과적합 징후가 없는가?

---

## 12. 테스트 계획

### 단위 테스트

- DSR 물리 변환의 맑음·흐림·저태양고도 경계
- feature 순서와 계산값
- 미래 DSR 입력 금지
- 미래 Open-Meteo snapshot 선택 금지
- 결측 DSR·CA·Open-Meteo 처리
- P10 ≤ P50 ≤ P90 제약
- 야간 출력 0
- model 저장 후 load한 예측의 동일성

### 통합 테스트

- 작은 fixture CSV로 dataset 생성부터 학습·평가까지 실행
- 동일 명령 재실행 시 동일 weight와 metric 생성
- 실제 결합 CSV를 사용한 smoke test
- baseline과 candidate가 완전히 동일한 diagnostic row를 사용하는지 검증

---

## 13. 구현 순서

복잡한 기능을 한 번에 연결하지 않고 아래 6단계로 진행한다. 각 단계의 결과를 확인한 뒤 다음 단계로 넘어간다.

<details>
<summary>과거 Stage 1~3 구현 과정 보기</summary>

### 1단계 — 10분 horizon 학습 데이터 생성

먼저 가장 단순한 10분 후 예측만 만든다.

- [x] 입력 CSV의 field, 단위와 timezone을 확인했다. DSR은 공식 `W/m²` 정의를 확인했고 기존 CSV에는 생성 당시 단위 상태와 실행 가정을 함께 기록했다.
- [x] Open-Meteo `issued_at`, `available_at`, `valid_time`을 구분한다.
- [x] 각 `prediction_origin_utc`에서 10분 뒤 `valid_time_utc`를 만든다.
- [x] prediction origin까지 이용 가능한 KMA DSR·CA만 선택한다.
- [x] prediction origin 이전에 이용 가능해진 Open-Meteo snapshot만 선택한다.
- [x] 태양 위치 보정 DNI 기준값과 5개 feature를 만든다.
- [x] valid time의 DSR로 10분 뒤 DSR 환산 DNI 정답값을 만든다.
- [x] 생성된 학습 행을 사람이 열어볼 수 있는 CSV로 저장한다.

산출물 예시:

```text
data/fr01/model/p50_first/10min_dataset.csv
data/fr01/model/p50_first/10min_dataset_summary.json
```

### 2단계 — P50 선형 모델 하나 학습

불확실성 모델을 추가하기 전에 중앙값 모델 하나가 정상적으로 학습되는지 확인한다.

- [x] UTC 시간순으로 train·validation·diagnostic을 분리한다.
- [x] P50 pinball loss를 사용하는 `f50(x) = X @ W50`을 학습한다.
- [x] validation과 diagnostic의 P50 예측값을 CSV로 저장한다.
- [x] 동일 표본의 Open-Meteo baseline과 P50의 MAE·RMSE를 출력한다.
- [x] 학습된 weight와 feature 순서를 출력한다.

P50만 구현하는 이유는 feature와 정답이 잘못된 상태에서 P10·P90까지 동시에 디버깅하는 일을 피하기 위해서다.

### 3단계 — 시간 누수와 표본 직접 확인

모델 성능보다 먼저 데이터 계약을 검증한다.

- [x] 전체 기간에 분산된 표본을 최소 10개 출력한다.
- [x] `prediction_origin < valid_time`인지 확인한다.
- [x] 입력 KMA timestamp가 prediction origin 이후가 아닌지 확인한다.
- [x] Open-Meteo `available_at`이 prediction origin 이후가 아닌지 확인한다.
- [x] target DSR timestamp가 valid time과 일치하는지 확인한다.
- [x] 결측 제외 사유와 유효 표본 수를 확인한다.
- [x] 미래 데이터를 주입한 fixture에서 테스트가 실패하는지 확인한다.

이 단계가 통과하기 전에는 P10·P90이나 다른 horizon을 추가하지 않는다.

</details>

### 4단계 — P10·P90 추가

- [x] P10·P90 pinball-loss 모델을 추가한다.
- [x] P10·P50·P90의 원시 예측과 제약 적용 후 예측을 구분해 CSV에 저장한다.
- [x] `P50=max(P10, raw_P50)`, `P90=max(P50, raw_P90)` 후처리 함수를 구현하고 단위 테스트했다.
- [x] Pinball Loss, PICP, 평균 구간 폭과 분위수 교차율을 계산한다.

#### 4단계 실행 결과 — 2026-11-08

```text
실행 파일    tools/ai_experiments/train_fr01_kma_quantile_model.py
artifact     data/fr01/archive/previous_work/legacy_pre_20261005_extension/model/quantile_stage4/
학습 행      503
validation    97
diagnostic    97 (보고 전용)
final test    미사용

validation P50 MAE / RMSE       61.96 / 122.54 W/m²
validation Pinball P10/P50/P90  14.84 / 30.98 / 27.44 W/m²
validation PICP P10~P90          0.825
validation 평균 구간 폭          315.23 W/m²
validation 원시 분위수 교차       0 / 97
```

diagnostic에서는 원시 `P10 > P50` 교차가 1/97건 발생했고 `P50=max(P10, raw_P50)` 후처리로 교정했다. validation과 diagnostic을 합친 194개 최종 예측 행에서 순서 위반은 0건이다. diagnostic 결과는 모델이나 weight 범위를 변경하는 근거로 사용하지 않는다.

#### 학습 기간 확장 재학습 결과 — 2026-11-08

10/05~10/18을 train에만 추가하고 기존 모델 사양을 그대로 유지해 세 모델을 공통 초기값에서 처음부터 다시 학습했다. 이전 artifact는 `data/fr01/archive/previous_work/legacy_pre_20261005_extension/model/quantile_stage4/`에 보존했다.

```text
artifact                         data/fr01/model/quantile_stage4_train_20261005/
학습 행                          1,266
validation / diagnostic          97 / 97
final test                       미사용
동일 validation 행 비교          통과

validation P50 MAE / RMSE        62.29 / 122.23 W/m²
validation Pinball P10/P50/P90   12.55 / 31.14 / 26.85 W/m²
validation PICP P10~P90           0.825
validation 평균 구간 폭           272.72 W/m²
validation 원시 분위수 교차        0 / 97
```

이전 모델 대비 P10·P90 pinball loss는 각각 15.4%·2.2% 개선됐고 평균 구간 폭은 13.5% 감소했다. P50 MAE는 0.5% 악화, RMSE는 0.3% 개선되어 사실상 동일하다. calibration은 `P(y≤P10) 5.2%→6.2%`, `P(y≤P90) 85.6%→86.6%`로 목표 10%·90% 쪽으로 소폭 개선됐다. P10 zero clipping 39.2%는 남아 있다.

#### 10분 Stage 4 모델 사양 동결 — 2026-11-08

확장 train validation 결과를 채택해 현재 feature 5개, pvlib Erbs 변환, Open-Meteo `issued_at + 7시간`, `preceding_hour_mean` 정렬, weight 범위, L-BFGS-B pinball loss와 분위수 후처리 규칙을 동결한다. 동결 artifact는 `data/fr01/model/quantile_stage4_train_20261005/`다. Diagnostic과 final test 결과를 근거로 이 사양을 변경하지 않는다.

##### 동결 사양 요약

| 항목 | 확정 내용 |
|---|---|
| 상태 | `frozen_stage4_10min` |
| 예측 horizon | 10분 |
| 모델 종류 | P10·P50·P90을 각각 학습하는 선형 분위수 회귀 3개 |
| 계산 형태 | `raw_q = X @ W_q`; intercept는 `X`의 첫 열이므로 수학적으로 `Wx+b`와 같음 |
| 학습 입력 | `data/fr01/datasets/development_20261005_20261101/` |
| 동결 artifact | `data/fr01/model/quantile_stage4_train_20261005/` |
| 정답 | 예측 기준 시각에서 10분 뒤 KMA DSR을 pvlib Erbs로 변환한 DSR 환산 DNI |
| 정답 field | `reference_dni_W_m2`, 내부 종류 `reference_type=dsr_proxy` |
| optimizer·loss | `L-BFGS-B` + quantile별 pinball loss |
| 학습 시작 weight | P10·P50·P90 모두 `[0.0, 0.75, 0.20, 0.05, 0.0]` |
| 태양 고도 기준 | origin과 target 모두 `5° 이상`인 주간 표본 |
| Open-Meteo model | ECMWF IFS 0.25° single run |
| Open-Meteo 공개 시각 | `available_at = issued_at + 7시간` |
| Open-Meteo 시간 의미 | `preceding_hour_mean`; 선형 보간 없이 target이 속한 직전 1시간 평균 사용 |
| 결측 처리 | DSR·CA·target·이용 가능한 Open-Meteo가 없으면 학습 행에서 제외; 임의 보간 금지 |
| 위성 patch | 없음, `cloud_patch_used=false`, `advection_impact=0.0` |
| Final test | `2026-11-02~11-05`, 미사용 상태로 archive 동결 후 1회 평가 완료 |

동결 feature 순서와 의미:

| 순서 | feature | 의미 |
|---:|---|---|
| 0 | `intercept` | 항상 `1.0`, 선형식의 절편 역할 |
| 1 | `direct_prior_factor` | 현재 KMA DSR 환산 DNI와 Open-Meteo DNI를 horizon 가중치로 결합한 기본 예상 비율 |
| 2 | `open_meteo_factor` | `open_meteo_dni / target_clear_sky_dni` |
| 3 | `cloud_transmittance` | `1 - KMA_CA / 100` |
| 4 | `advection_impact` | 현재 `0.0`; 실제 위성 patch 도입 전에는 영향 없음 |

Weight 허용 범위:

| weight | 범위 |
|---|---:|
| intercept | `-0.5 ~ 0.5` |
| direct prior | `0.0 ~ 1.5` |
| Open-Meteo | `0.0 ~ 1.5` |
| cloud transmittance | `-0.5 ~ 0.5` |
| advection | `0.0` 고정 |

동결된 학습 weight는 feature 표의 순서와 정확히 대응한다.

| Quantile | intercept | direct prior | Open-Meteo | cloud | advection |
|---|---:|---:|---:|---:|---:|
| P10 | -0.13177931 | 0.58297387 | 0.00000000 | 0.38425539 | 0.00000000 |
| P50 | -0.00540647 | 1.00847785 | 0.00000000 | 0.01583867 | 0.00000000 |
| P90 | 0.36392468 | 0.75668485 | 0.00000000 | -0.07916727 | 0.00000000 |

물리 범위와 분위수 순서 후처리:

```text
P10 = clip(raw_P10, 0, 1.05)
P50 = max(P10, clip(raw_P50, 0, 1.05))
P90 = max(P50, clip(raw_P90, 0, 1.05))
최종 DNI = 보정된 factor × target 시각 clear-sky DNI
```

데이터 분할과 embargo:

| 구간 | 기간 | 유효 행 | 사용 목적 |
|---|---|---:|---|
| train | 2026-10-05~10-28 | 1,266 | weight 학습 |
| validation | 2026-10-29~10-30 | 97 | 사양 선택과 동결 판단 |
| diagnostic | 2026-10-31~11-01 | 97 | 보고 전용, 사양 변경 금지 |
| final test | 2026-11-02~11-05 | horizon별 180·168·151 | 동결 모델의 마지막 1회 평가 완료 |

모든 split은 `valid_time < next_split_start`를 만족해야 한다. 10분 모델에서는 다음 split 시작이 00:00일 때 이전 split의 23:50 origin을 제외하고 23:40까지만 허용한다.

동결에 사용한 validation 결과:

| 지표 | 결과 |
|---|---:|
| P50 MAE / RMSE | 62.29 / 122.23 W/m² |
| Pinball P10 / P50 / P90 | 12.55 / 31.14 / 26.85 W/m² |
| P10~P90 포함률 | 82.5% |
| 평균 구간 폭 | 272.72 W/m² |
| `P(y≤P10) / P(y≤P50) / P(y≤P90)` | 6.2% / 56.7% / 86.6% |
| 원시 분위수 교차 | 0 / 97 |

동결 이후 금지 사항:

- diagnostic 또는 final test 결과를 보고 feature, weight 범위, 전처리나 후처리를 변경하지 않는다.
- 동결된 10분 artifact를 30·60분 모델 학습 결과로 덮어쓰지 않는다.
- 10분 사양을 바꾸려면 새 버전과 새 holdout 계획을 별도로 만든다.

### 5단계 — 30·60분 horizon 추가

#### 무엇을 구현하는가

현재 동결 모델은 시각 `t`에서 **10분 뒤(`t+10`) DSR 환산 DNI**만 예측한다. Stage 5에서는 같은 시각 `t`의 입력만 사용해 다음 정답을 각각 예측한다.

```text
30분 모델  → t+30분의 DSR 환산 DNI P10·P50·P90
60분 모델  → t+60분의 DSR 환산 DNI P10·P50·P90
```

첫 구현은 horizon별 독립 모델을 사용한다. 하나의 모델에 서로 다른 horizon을 섞지 않고, 30·60분마다 P10·P50·P90 세 모델을 따로 학습한다. 따라서 Stage 5에서 새로 학습하는 선형 모델은 총 6개다. 10분 동결 모델은 비교 기준으로 그대로 보존한다.

#### 왜 필요한가

- 서비스는 10분뿐 아니라 30분·60분 뒤의 일사량과 불확실성도 필요하다.
- 시간이 멀어질수록 현재 KMA 관측의 영향은 약해지고 Open-Meteo 예보의 상대적 중요성은 커질 수 있다.
- 구름 변화 때문에 예측 거리가 길수록 오차와 P10~P90 폭이 커지는 것이 자연스럽다. 이를 실제 데이터로 확인해야 한다.
- Open-Meteo hourly 값만으로는 10·30분 단위의 국지적인 변화를 충분히 설명하기 어렵다. KMA 최근 관측을 결합한 모델이 어느 horizon까지 도움이 되는지 측정해야 한다.
- 10분에서 좋았던 weight를 30·60분 예측에 그대로 사용하는 것은 타당하지 않으므로 horizon별로 다시 학습한다.

#### 데이터 행 생성 방법

예측 기준 시각을 `t`라고 할 때 입력은 항상 `t`까지 이용 가능한 정보만 사용한다. 정답 DSR만 해당 미래 시각에서 가져온다.

| Horizon | 입력 cutoff | 정답 시각 | 다음 split이 00:00일 때 마지막 허용 origin |
|---:|---|---|---|
| 30분 | `t` | `t+30분` | 23:20 |
| 60분 | `t` | `t+60분` | 22:50 |

예를 들어 12:00에 60분 뒤를 예측하면 12:00까지의 KMA·Open-Meteo만 입력으로 사용하고, 13:00 DSR은 정답 생성에만 사용한다. 12:10 이후 KMA나 나중에 공개된 Open-Meteo run이 입력에 들어가면 누수다.

#### 10분 동결 사양에서 유지할 것과 달라지는 것

| 유지 | Horizon별로 달라짐 |
|---|---|
| feature 5개와 순서 | 정답 DSR 시각 |
| pvlib Erbs 변환 | `obs_weight = exp(-horizon_hours / 1.5)` |
| Open-Meteo `+7시간` 공개 규칙 | split 경계 embargo 길이 |
| `preceding_hour_mean` 정렬 | 학습된 P10·P50·P90 weight |
| L-BFGS-B, pinball loss, weight 범위 | 유효 표본 수와 결측 수 |
| 분위수 순서와 물리 범위 후처리 | validation 성능과 예측 구간 폭 |

#### 구현 순서

- [x] dataset builder가 `horizon_minutes`를 받아 30·60분 행을 만들도록 일반화했다.
- [x] horizon별 target timestamp와 split embargo assertion을 추가했다.
- [x] 미래 KMA·미래 Open-Meteo를 차단하는 누수 테스트를 horizon별로 작성했다.
- [x] 30분 모델을 학습하고 유효 표본 수와 validation 결과를 확인했다.
- [x] 같은 구조로 60분 모델을 학습했다.
- [x] horizon마다 별도 artifact 디렉터리에 P10·P50·P90 결과를 저장했다.
- [x] 동일 validation 행에서 각 모델 P50과 Open-Meteo baseline을 비교했다.
- [x] horizon별 MAE·RMSE·Bias·Pinball Loss·PICP·평균 구간 폭·calibration을 출력했다.
- [x] 각 horizon의 유효 표본 수와 결측 제외 사유를 저장했다.

#### Stage 5 구현 결과 — 2026-11-08

```text
30분 artifact  data/fr01/model/stage5/horizon_30min/
60분 artifact  data/fr01/model/stage5/horizon_60min/
비교 artifact  data/fr01/model/stage5/horizon_comparison.json

30분 train / validation / diagnostic  1,216 / 91 / 91
60분 train / validation / diagnostic  1,141 / 82 / 82
세 horizon 공통 validation origin         78
누수 검사                                PASS
final test                               미사용

30분 P50 MAE / RMSE       83.62 / 133.15 W/m²
30분 Open-Meteo           159.20 / 205.58 W/m²
30분 PICP / 평균 폭        0.813 / 381.67 W/m²

60분 P50 MAE / RMSE       117.43 / 181.84 W/m²
60분 Open-Meteo           157.34 / 205.94 W/m²
60분 PICP / 평균 폭        0.817 / 452.96 W/m²
```

30분과 60분 모두 Open-Meteo baseline보다 P50 MAE·RMSE가 낮았다. Horizon이 길어질수록 오차와 불확실성 폭이 증가하고 baseline 대비 개선 폭은 감소했다.

60분 P90 intercept 범위는 validation만 사용해 기존 `±0.5`와 확장 `±0.75`를 한 번 비교했다. 확장 후보는 P90 누적 비율이 `87.8% → 89.0%`로 목표 90%에 가까워졌지만, 선택 기준인 P90 pinball loss가 `42.14 → 42.51`로 나빠지고 평균 예측 구간 폭도 `452.96 → 493.81 W/m²`로 커졌다. 따라서 기존 `±0.5`를 채택했다. 비교 중 diagnostic 평가는 실행하지 않았고 final test도 사용하지 않았다. 확장 후보는 `data/fr01/model/stage5/validation_experiments/horizon_60min_p90_intercept_075/`에 기각 후보로 보존한다.

2026-11-08에 10·30·60분 사양을 모두 동결했다. 동결 목록은 `data/fr01/model/frozen_model_manifest.json`에 기록하며, 이후 diagnostic 또는 final test 결과를 보고 feature·가중치 범위·후처리를 다시 조정하지 않는다.

#### Stage 5 완료 판단

- [x] 30·60분 모두 누수 검사와 분위수 순서 검사를 통과했다.
- horizon별로 Open-Meteo 대비 개선 여부를 별도로 판단한다. 10분 성능을 장기 horizon에 그대로 주장하지 않는다.
- 표본이 부족한 horizon은 억지로 채택하지 않고 `insufficient_sample`로 표시한다.
- [x] 모델 선택에는 train·validation만 사용했다. Diagnostic은 보고 전용이며 final test는 사용하지 않은 채 10·30·60분 사양을 동결했다.

### 6단계 — 저장·로드와 전체 평가 추가

Stage 6 운영 모델 위치는 `backend/app/nowcast/model_artifacts/`로 확정했다. 이 경로에는
검증된 작은 JSON 모델만 Git으로 관리하고, 학습 CSV·예측 CSV·기각 후보 등 실험 근거는
`data/fr01/model/`에 유지한다. 상세 구조, 필수 계약, 구현 순서와 완료 조건은
`backend/app/nowcast/model_artifacts/README.md`에 기록했다.

- [x] P10·P50·P90 weight와 feature 계약을 horizon별 `model.json`에 저장했다.
- [x] 저장한 모델을 다시 load했을 때 factor와 DNI 예측값이 동일한지 검증했다.
- [x] 전체·한국 시간대·태양 고도·구름 구간별 Validation 평가 artifact를 생성했다.
- [x] 실행 명령, 입력 SHA-256, split 경계와 코드 commit을 metadata에 저장했다.
- [x] 터미널에 핵심 결과와 artifact 경로를 출력했다.
- [x] 팀 승인 후 읽기 전용 운영 추론 worker에 동결 artifact 로딩을 연결했다.

#### Stage 6 구현 결과 — 2026-11-08

```text
운영 모델      backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1/
loader         backend/app/nowcast/kma_model_artifact.py
패키징 도구    tools/ai_experiments/package_fr01_kma_models.py
평가 도구      tools/ai_experiments/evaluate_fr01_kma_models.py
평가 결과      data/fr01/model/stage6/validation_segment_evaluation.json

10·30·60분 schema/hash 검증     PASS
저장·재로드 factor 최대 오차     0.0 (허용 1e-10)
저장·재로드 DNI 최대 오차        0.0 W/m² (허용 1e-6)
공통 Validation origin           78
Diagnostic 평가                  미실행
Final test                       당시 미사용 (Stage 6 이후 1회 평가 완료)
```

전체 Validation 지표는 Stage 5 결과와 일치했다. 구간 평가에는 태양 고도 45° 이상 표본이 없고 부분 흐림 표본도 horizon별 7~8개뿐이므로, 해당 구간 수치를 일반화 성능으로 해석하지 않는다. 운영 API 연결은 Stage 6 범위에서 제외하며 팀 검토 후 별도로 수행한다.

### 첫 구현 범위 — 과거 Stage 1~3 기록

<details>
<summary>초기 10분 P50 수직 흐름 기록 보기</summary>

첫 번째 구현에서는 **1~3단계까지만** 완료한다.

```text
입력: 결합 CSV
horizon: 10분만
모델: P50 선형 분위수 모델 1개
평가: Open-Meteo 대비 MAE·RMSE
검증: 시간 누수 검사 + 실제 학습 행 직접 확인
출력: 학습 dataset CSV, 예측 CSV, dataset 요약, 터미널 결과
```

첫 구현의 목적은 높은 성능을 만드는 것이 아니라 다음 수직 흐름이 정확하게 작동함을 증명하는 것이다.

```text
결합 CSV
 -> 누수 없는 10분 학습 행
 -> 물리 feature 생성
 -> P50 학습
 -> diagnostic 예측
 -> Open-Meteo와 동일 표본 비교
```

첫 구현 완료 조건:

- [x] 10분 dataset의 기간 분산 표본을 사람이 확인할 수 있다.
- [x] 자동 누수 검사가 모두 통과한다.
- [x] P50 weight가 실제 train 구간으로 학습된다.
- [x] validation과 diagnostic은 첫 P50 학습에 사용되지 않는다.
- [x] 동일 diagnostic row에서 P50과 Open-Meteo MAE·RMSE가 출력된다.
- [x] 명령을 다시 실행해 같은 결과를 재현할 수 있다.

첫 구현에는 P10·P90, 30·60분 horizon, model 저장·로드, 운영 API 연결을 포함하지 않는다. 이 기능들은 10분 P50 데이터 계약이 검증된 뒤 추가한다.

### 첫 구현 재검증 결과 — 2026-11-07

```text
KMA 원본 행                2,016
유효 10분 학습 행            699
train / validation / diagnostic  503 / 97 / 97
누수 위반                       0

diagnostic P50 MAE / RMSE          39.17 / 123.84 W/m²
diagnostic Open-Meteo MAE / RMSE   79.13 / 128.30 W/m²
```

Open-Meteo는 ECMWF IFS 0.25° single run을 6시간 간격으로 복원했다. API의 run 시각은 초기화 시각이지 공개 시각이 아니므로 `available_at`은 최소 `issued_at + 7시간`으로 보정한다. hourly DNI는 `preceding_hour_mean`이므로 선형 보간하지 않고, 10분 valid time이 속한 직전 1시간 구간의 평균값으로 매핑한다. 위 재검증 수치는 pvlib 공식 Erbs 변환, 7시간 공개 지연과 split embargo를 적용한 결과다.

첫 구현 당시 결과의 제한:

- [국가기상위성센터 공식 산출물 정의](https://api.nmsc.kma.go.kr/homepage/html/base/cmm/selectPage.do?page=static.convergence.utilizationProducts)에서 GK2A DSR의 범위와 단위를 `0~1500 W/m²`로 확인했다. 기존 CSV는 확인 전 생성되어 metadata가 `unknown,false`이므로 재검증 명령에서만 `--assume-dsr-w-m2`를 사용했고, 이후 수집본은 `W/m2,true`로 기록한다.
- 정답은 실제 계측 DNI가 아니라 10분 뒤 DSR 환산 DNI 정답값이다.
- Open-Meteo의 과거 실제 공개 시각이 아니라 7시간 공개 지연을 적용한 보수적 복원 시각이다.
- 당시 14일의 짧은 기간 결과였으며, 현재 동결 모델은 28일 개발 데이터로 다시 학습했다. 그래도 한 계절·한 지점 자료이므로 계절·지역 일반화 성능을 의미하지 않는다.

과거 P50 artifact는 `data/fr01/archive/previous_work/legacy_pre_20261005_extension/model/` 아래에 보존했다. 현재 동결 모델은 `data/fr01/model/quantile_stage4_train_20261005/`이며 diagnostic을 학습과 사양 선택에서 제외하고, pvlib Erbs·Open-Meteo 구간 정렬·7시간 공개 지연·split embargo를 적용했다.

</details>

---

## 14. 모델 구현 완료 조건

- [x] 동결 모델의 final test 평가를 전용 명령으로 한 번 실행하고 결과를 보존했다.
- [x] 모든 학습 행에 prediction origin, valid time, horizon이 기록된다.
- [x] 미래 KMA 관측과 미래 발행 Open-Meteo snapshot이 입력에 포함되지 않는다.
- [x] P10·P50·P90 weight가 운영 artifact로 저장되고 재사용된다.
- [x] 저장 전후 모델 예측값이 동일하다.
- [x] Open-Meteo baseline과 모델을 동일 test 표본에서 비교했다.
- [x] Validation 전체·구간·horizon별 성능과 유효 표본 수가 출력된다.
- [x] 결과와 artifact에 `reference_type=dsr_proxy`, `cloud_patch_used=false`가 표시된다.
- [x] 결과 해석에 28일 개발 자료와 DSR 환산 DNI 정답값의 한계가 명시된다.

Final-test 구현 완료 조건을 모두 충족했다. 결과를 근거로 V1 feature, weight, 전처리,
후처리를 변경하지 않는다.

### Final-test 실행 준비 결과 — 2026-11-08

| 항목 | 확정 내용 |
|---|---|
| KMA 입력 | 2026-11-02~11-05, UTC 10분 간격 576행 |
| Open-Meteo 입력 | ECMWF IFS 0.25° archived run 18개, 504행 |
| 공개 지연 | 모든 run에 `available_at = issued_at + 7시간` 적용 |
| 시간 커버리지 | 10분 575행, 30분 573행, 60분 570행 |
| 완전성 기록 | `data/fr01/archive/frozen_final_test/final_test_20261102_20261105/input_readiness_manifest.json` |
| 평가기 | `tools/ai_experiments/evaluate_fr01_kma_final_test.py` |
| 실행 안전장치 | 명시적 `--confirm-one-time-final-evaluation`, 기존 결과 경로 덮어쓰기 금지 |
| 준비 검사 당시 상태 | `ready_for_one_time_evaluation`; 이후 1회 평가 완료 |

준비 검사는 KMA·Open-Meteo·모델 artifact hash, KMA 시각축, Open-Meteo run 간격,
공개 지연과 horizon별 시간 커버리지만 확인한다. 평가기는 동결 JSON을 로드해 예측만 하며
학습기나 optimizer를 호출하지 않는다. 작은 fixture로 정상 입력, hash 변조 거부,
명시적 실행 확인과 학습 경로 부재를 검증했다.

### Final-test 1회 실행 결과 — 2026-11-08

동결 artifact와 입력 hash를 확인한 후 학습 기능이 없는 평가기로 10·30·60분을 한 번
평가했다. 결과는 `data/fr01/model/final_test_20261102_20261105/`에 저장했다.

| Horizon | 유효 행 | 모델 P50 MAE | Open-Meteo MAE | 모델 P50 RMSE | Open-Meteo RMSE |
|---:|---:|---:|---:|---:|---:|
| 10분 | 180 | 73.16 | 167.19 | 131.98 | 214.63 |
| 30분 | 168 | 122.30 | 167.16 | 183.05 | 216.10 |
| 60분 | 151 | 177.54 | 173.25 | 231.59 | 224.98 |

단위는 `W/m²`다. 10분과 30분은 모델 P50이 Open-Meteo보다 낮은 MAE·RMSE를 보였고,
60분은 두 지표 모두 Open-Meteo보다 높았다. 이 결과는 V1 수정이나 재학습에 사용하지
않는다. 모델 manifest SHA-256은 실행 전후
`1893afa4f6ab948ee169967594e9a5792b83b0f9e899d0d645bdd8f43e7e330a`로 동일하다.

결과 상태는 `completed_no_retuning_allowed`이며 세부 분위수·calibration 해석은
`fr01_4_ai_kma_analysis.md`에 기록한다.

---

## 15. 운영 API 연결 확정 정책 — 2026-11-08

이 절은 구현 전 확정한 운영 계약이다. 모델 artifact 자체는 변경하지 않는다.

### 15.1 Horizon별 외부 제공

| Horizon | 사용자에게 제공할 기본값 | V1 AI 결과 처리 | 근거 |
|---:|---|---|---|
| 10분 | V1 P10·P50·P90 | 정식 제공 | Final test에서 Open-Meteo보다 우수 |
| 30분 | V1 P10·P50·P90 | 정식 제공 | Final test에서 Open-Meteo보다 우수 |
| 60분 | Open-Meteo DNI | 내부 Shadow 기록만 수행 | Final test에서 V1이 Open-Meteo보다 소폭 열세 |

60분 V1 artifact는 삭제하거나 수정하지 않는다. 운영 요청마다 가능하면 같은 입력으로
60분 AI 예측도 계산해 내부 관찰 로그에만 저장하고, 사용자 응답의 기본 60분 값에는
Open-Meteo를 사용한다. Shadow 결과는 V1 재튜닝 근거로 사용하지 않으며 후속 V2 검토
자료로만 사용한다.

### 15.2 결측·오래된 입력 fallback

```text
KMA DSR·CA 정상이고 관측 나이 <= 20분
  -> 10·30분 V1 AI 분위수 제공

KMA DSR 또는 CA 결측, 파싱 실패, 관측 나이 > 20분
  -> AI 실행 결과를 사용자에게 제공하지 않음
  -> 해당 horizon의 Open-Meteo DNI를 fallback으로 제공

Open-Meteo도 없거나 사용 가능한 run이 아님
  -> unavailable 반환; 0·직전 값·clear-sky 값으로 임의 대체하지 않음
```

Fallback 응답은 Open-Meteo point forecast이므로 AI 분위수인 것처럼 표현하지 않는다.
공통 `dni_W_m2`에는 선택된 값(AI이면 P50, fallback이면 Open-Meteo)을 넣고,
AI가 아닐 때 `p10_dni_W_m2`, `p50_dni_W_m2`, `p90_dni_W_m2`는 `null`로 둔다.

### 15.3 데이터 조회 방식

- 사용자 API 요청 중 KMA/Open-Meteo 외부 API를 직접 호출하지 않는다.
- 백그라운드 worker가 자료를 미리 수집·검증·저장한다.
- 운영 API는 저장된 최신 유효 snapshot만 읽어 추론한다.
- Open-Meteo는 `available_at <= prediction_origin`인 run만 선택한다.
- worker 지연과 외부 API 장애는 응답의 `fallback_reason` 또는 `unavailable_reason`으로 남긴다.

### 15.4 응답 추적 정보

각 예측에는 최소한 아래 필드를 포함한다. 실제 API schema 구현 시 이름을 바꾸더라도
같은 의미와 추적 가능성을 유지한다.

| 필드 | 의미 |
|---|---|
| `model_version` | `fr01_kma_quantile_v1`; Open-Meteo-only 응답이면 `null` 허용 |
| `prediction_source` | `ai_model`, `open_meteo_default`, `open_meteo_fallback`, `unavailable` |
| `fallback_used`, `fallback_reason` | fallback 여부와 KMA 결측·stale 등의 원인 |
| `prediction_origin`, `valid_time`, `horizon_minutes` | 예측 기준·대상 시각 |
| `kma_observed_at` | 실제 사용한 KMA 관측 시각 |
| `open_meteo_issued_at`, `open_meteo_available_at` | 사용한 run과 누수 검증 정보 |
| `dni_W_m2` | 사용자에게 선택해 제공하는 대표 DNI |
| `p10/p50/p90_dni_W_m2` | AI 정식 예측일 때만 값 제공 |
| `quality_flags` | 결측·stale·Shadow 등 상태 목록 |

### 15.5 배포 순서와 완료 조건

1. 추론 adapter와 위 응답 계약을 구현하고 정상·야간·결측·stale·미래 run 차단 테스트를 작성한다.
2. 사용자 응답을 바꾸지 않는 Shadow 운영으로 입력 지연, fallback률, 오류율을 확인한다.
3. 팀 검토 후 10·30분 AI와 60분 Open-Meteo 기본값을 사용자 응답에 연결한다.
4. 배포 후에도 학습 기능은 운영 API에 포함하지 않고 동결 artifact hash를 시작 시 검증한다.

운영 연결 완료 조건은 미래 정보 누수 0건, 모델 hash 검증 통과, fallback 원인 추적 가능,
외부 API 장애 시 요청 처리 안정성 확인이다. Shadow 관찰 기간과 허용 오류율·fallback률은
배포 환경의 수집 주기와 트래픽을 확인한 뒤 팀 검토에서 수치로 확정한다.

### 15.6 운영 연결 구현 결과 — 2026-11-08

- `kma_worker`와 `open_meteo_worker`가 site별 snapshot을 저장한다. Open-Meteo 모델은
  학습 계약과 같은 `ecmwf_ifs025`로 고정했다.
- `operational_nowcast_worker`가 동결 artifact의 hash를 검증한 뒤 10·30·60분 결과를
  `ai_nowcast_run`, `ai_nowcast_point`에 저장한다. worker에는 학습 기능이 없다.
- 기본 `FR01_AI_ROLLOUT_MODE=shadow`에서는 사용자 값은 Open-Meteo이고 AI 분위수는 내부
  shadow 필드에만 남는다. `active` 전환 후 10·30분만 AI, 60분은 Open-Meteo를 제공한다.
- `GET /api/scenes/{scene_id}/ai-nowcast/latest`는 저장된 최신 유효 결과만 반환한다.
  요청 경로에서는 모델 실행과 외부 provider 호출을 하지 않는다.
- KMA 관측이 20분보다 오래되거나 필수값이 없으면 Open-Meteo로 fallback한다.
  Open-Meteo도 없으면 `unavailable`이며 임의의 clear-sky·직전값을 만들지 않는다.
- `/nowcast-comparison` GET은 한시적 deprecated alias이고, 외부 입력을 받던 POST는 제거했다.
- 정식 전환은 shadow 운영 지표를 팀이 검토한 뒤 환경 변수만 `active`로 변경한다.

## 16. 후속 개선 후보

V1 결과가 확보된 뒤 한 번에 하나씩 비교한다.

1. `[완료]` Stage 5의 horizon별 독립 선형 분위수 모델 학습
2. DSR·CA lag와 변화율 feature 추가
3. 실제 GK2A patch와 cloud motion feature 추가
4. 기온·습도 feature ablation
5. Quantile Gradient Boosting challenger 추가
6. 실제 pyrheliometer DNI로 재학습·재평가

각 개선은 동일 test 구간을 반복 튜닝하는 방식이 아니라 새로운 기간의 holdout 데이터 또는 사전에 고정한 검증 절차로 비교한다.
