# FR-01.4 KMA 데이터 분석 기록

> **문서 역할:** 실제 데이터 품질, Stage 4~6과 Final test 결과를 기록하는 기준 문서다.
> 계획보다 확인된 수치를 우선한다.
> **분류:** PR·팀 공유용 평가 근거 문서다.

## 현재 분석 결론 — 2026-11-08

- 28일 개발 데이터로 10·30·60분 모델을 학습·동결했다.
- Validation P50 MAE는 각각 `62.29 / 83.62 / 117.43 W/m²`다.
- 같은 표본의 Open-Meteo보다 세 horizon 모두 낮은 MAE·RMSE를 보였다.
- horizon이 길수록 오차와 P10~P90 폭이 증가했다.
- 저장·재로드 예측 오차는 0이며 운영 JSON 검증을 통과했다.
- Final test에서 10·30분 P50은 Open-Meteo보다 좋았고 60분은 약간 나빴다.
- 결과는 한 지점·한 계절·DSR 환산 DNI 기준이므로 실제 DNI 일반화 성능이 아니다.
- Final test 결과와 관계없이 동결 V1은 수정하지 않는다.

## 1. 문서 목적

이 문서는 KMA GK2A 개발 데이터 확보 이후의 **데이터 품질 분석, Phase 3 데이터 결합, 모델 실험 결과**를 기록한다.

- 현재 구현 계약과 남은 작업은 `docs/fr01_4_ai_model_implementation.md`에서 관리한다.
- 이 문서에는 실제 산출물에서 확인한 사실과 실험 결과만 기록한다.
- 예상, 가정, 미확정 해석은 결과와 구분해 표시한다.
- 분석을 다시 실행할 수 있도록 입력 파일, 실행 명령, 코드 버전과 생성 시각을 남긴다.

## 2. 분석 대상

### 2.1 확정 수집 범위

모델 데이터는 **개발용 28일**과 별도로 동결한 **final test용 4일**로 구분한다.

| 항목 | 개발 데이터 | Final test 데이터 |
|---|---|---|
| KMA 자료 | GK2A 지점 자료 `DSR`, `CA` | GK2A 지점 자료 `DSR`, `CA` |
| 좌표 | 위도 `37.5714`, 경도 `126.9658` | 동일 |
| 시작 | `2026-10-05T00:00:00Z` | `2026-11-02T00:00:00Z` |
| 종료 | `2026-11-01T23:50:00Z` | `2026-11-05T23:50:00Z` |
| 간격 | 10분 | 10분 |
| 기간 | 연속 28일 | 연속 4일 |
| 기대 timestamp | 4,032개 (`28일 × 144개`) | 576개 (`4일 × 144개`) |
| 용도 | 모델 개발·validation | 동결된 최종 평가 전용 |
| 시간 기준 | UTC | UTC |

Final test 기간은 feature, 전처리, 가중치, 물리 제약, 결측 처리 또는 모델 종류를 선택하는 데 사용하지 않는다.

### 2.2 입력 산출물

수집 완료 후 실제 경로와 hash를 기록한다.

| 구분 | 파일 또는 디렉터리 | 확인 |
|---|---|---|
| 현재 확정 KMA | `data/fr01/datasets/development_20261005_20261101/kma_gk2a.csv` | [x] 4,032행 |
| 현재 확정 Open-Meteo | `data/fr01/datasets/development_20261005_20261101/open_meteo_runs.csv` | [x] 3,192행 |
| 수집 원본·중간 CSV·manifest | `data/fr01/archive/source_and_intermediate/` | [x] 보존 |
| 이전 개발 CSV·모델 | `data/fr01/archive/previous_work/` | [x] 보존 |
| Final test KMA | `data/fr01/archive/frozen_final_test/final_test_20261102_20261105/kma_gk2a.csv` | [x] 576행 |
| Final test manifest | `data/fr01/archive/source_and_intermediate/manifests/20261102T000000Z_20261105T235000Z_collection.json` | [x] |
| Open-Meteo 개발 snapshot | ECMWF IFS 0.25° single run, 6시간 간격 복원 | [x] |
| Open-Meteo final test snapshot | `data/fr01/archive/frozen_final_test/final_test_20261102_20261105/open_meteo_runs.csv` | [x] 18 run·504행 |
| Final-test 입력 완전성 | 같은 폴더의 `input_readiness_manifest.json` | [x] 성능 미확인 상태로 통과 |

### 2.3 분석 재현 정보

| 항목 | 기록값 |
|---|---|
| 분석 실행일 | `2026-11-06 KST` |
| Git commit | `c3615d3` |
| 분석 코드 | `tools/ai_experiments/analyze_fr01_kma_data.py` |
| 입력 SHA-256 | `28fb1796e5eb036397fabcf69845967e35ce546f0d3a32c9e57cd797c52d9a49` |
| 결과 | 터미널 출력, 아래 요약에 기록 |

## 3. 수집 완료 확인

아래 3장은 최초 14일(`2026-10-19~11-01`) 수집 직후 수행한 EDA 기록이다. 이후
`2026-10-05~10-18`을 train에 추가했으며 최종 모델 입력은 28일 4,032행이다.

### 3.1 파일 및 행 완전성

| 검사 항목 | 기대값 | 실제값 | 판정 |
|---|---:|---:|---|
| 전체 timestamp | 2,016 | 2,016 | 통과 |
| UTC 하루별 timestamp | 144 | 144 | 통과 |
| DSR raw chunk | 84 | 84 | 통과 |
| CA raw chunk | 84 | 84 | 통과 |
| 중복 timestamp | 0 | 0 | 통과 |
| 누락·10분 간격 위반 | 0 | 0 | 통과 |
| 범위 밖 timestamp | 0 | 0 | 통과 |

UTC 14일 범위는 완전하다. KST로 표시하면 UTC 오프셋 때문에 첫날과 마지막 날이 부분 날짜가 되어 총 15개 local date로 보이는 것이 정상이다.

### 3.2 결측 분석

`nan`, `6553.5`, `-9` 계열 등 provider 결측은 null로 분석하며 실제 `0.0`과 구분한다.

| 변수 | 전체 행 | 유효값 | null | null 비율 | 실제 0.0 |
|---|---:|---:|---:|---:|---:|
| DSR | 2,016 | 735 | 1,281 | 63.5% | 17 |
| CA fraction | 2,016 | 1,998 | 18 | 0.9% | 811 |

DSR 결측은 매일 야간에 반복되는 구조적 패턴이 대부분이지만 태양 고도 기준 주간 결측도 173개다. CA는 provider 결측 14개와 범위 밖 원문 `1.1` 네 건이 정규화 null이다. 결측을 0 또는 보간값으로 대체하지 않고 Phase 3 평가 가능 표본에서 제외한다.

### 3.3 값 범위와 시간 패턴

| 검사 항목 | 결과 |
|---|---|
| DSR 최소·최대·평균·중앙값 | `0.0 / 609.2 / 362.020 / 373.9` |
| CA raw 최소·최대·평균·중앙값 | `0.0 / 1.1 / 0.470 / 0.5` |
| CA `0~1` 범위 위반 | 4개, 모두 raw `1.1` |
| DSR 음수 또는 sentinel 잔존 | 0개 |
| 야간 DSR 비영(非零) 값 | 0개 |
| 주간 DSR 결측 | 173개 |
| 주간 유효 DSR·CA 상관 | Pearson `-0.657` |
| DSR 단위 확인 상태 | 국가기상위성센터 공식 정의에서 `0~1500 W/m²` 확인 |

시간축 계약은 통과했다. CA가 높을수록 DSR이 낮아지는 방향은 논리적이다. 다만 기존 개발 CSV는 단위 확인 전에 생성되어 metadata가 `unknown,false`이므로 실험 실행 시 `--assume-dsr-w-m2`를 명시했다. 이후 수집본은 `W/m2,true`로 기록한다. 단위가 확인됐더라도 DSR을 DNI로 바꾸는 과정과 주간 결측 때문에 독립적인 실측 DNI 정확성을 의미하지는 않는다.

## 4. Phase 3 — Open-Meteo 및 DSR 환산 DNI 결합 분석

### 4.1 목표와 데이터 선택

KMA 데이터 자체의 품질 검증은 끝났으므로, 같은 좌표·기간에 Open-Meteo 과거 forecast를 결합해 학습·평가용 시계열을 만든다.

- 과거 관측/reanalysis를 Open-Meteo baseline으로 사용하지 않는다.
- 최초 실험은 Open-Meteo **Single Runs API**의 ECMWF IFS 0.25° single run을 6시간 간격으로 복원해 당시 forecast를 재구성했다.
- 동일한 model과 forecast horizon을 전 구간에 적용했다. Best Match가 날짜마다 다른 model로 바뀌게 두지 않았다.
- 요청 변수는 최소 `direct_normal_irradiance_instant`, `cloud_cover`다.
- `run_initialization_time`, `available_at`, `valid_time`, model, 좌표를 보존한다. 실제 과거 공개 시각 대신 보수적으로 `available_at = issued_at + 7시간`을 적용한다.
- 선택한 run이 prediction origin보다 미래이거나 당시 이용 가능하지 않았다면 해당 sample을 제외한다.

Open-Meteo는 [Single Runs API](https://open-meteo.com/en/docs/single-runs-api)로 archived individual run을 제공하며 과거 발행 시점 재현에 적합하다. Historical Forecast의 stitched series는 데이터 탐색용으로만 사용하고 공정한 baseline 평가에는 사용하지 않는다.

### 4.2 DSR 환산 DNI 생성

태양 위치를 계산한 뒤 `pvlib.irradiance.erbs()` 공식 구현으로 DSR을 DNI 형태의 기준값으로 변환한다.

1. DSR·태양 고도·clear-sky GHI/DNI가 모두 유효한 행만 선택한다.
2. 낮은 태양 고도에서 DNI가 불안정해지므로 기본 평가 기준은 `solar_elevation_deg >= 5`로 둔다.
3. 공식 단위가 `W/m²`인 DSR을 GHI 성격의 값으로 사용하고 pvlib Erbs 분해로 DSR 환산 DNI를 계산한다.
4. 출력에 `reference_type=dsr_proxy`, `dsr_unit_assumption=W/m²`, 변환 version을 기록한다.
5. DSR 결측 647개, CA 결측 8개, CA 이상값 한 건은 보간하거나 0으로 채우지 않는다.
6. reference가 없는 행은 feature 시계열에는 남길 수 있지만 metric·target에서는 제외한다.

DSR 공식 단위는 확인했지만, 변환 결과는 독립적인 DNI 센서 실측값이 아니다. 따라서 결과는 **DSR 환산 기준값에 대한 상대 비교**로 해석한다.

### 4.3 시간 정렬과 결합 규칙

- 모든 저장·join key는 UTC다. KST는 표시용 컬럼으로만 둔다.
- KMA는 10분, Open-Meteo는 기본 1시간이므로 원본 해상도를 metadata에 보존한다.
- Open-Meteo hourly DNI는 `preceding_hour_mean` 의미이므로 선형 보간하지 않는다. 10분 valid time이 속한 직전 1시간 구간의 평균값으로 매핑한다.
- 서로 다른 run 또는 사후 관측값을 섞지 않고 `alignment_method`를 기록한다.
- baseline과 AI feature는 반드시 동일한 Open-Meteo run/value를 사용한다.
- join 전후 행 수와 `missing_dsr`, `low_sun`, `missing_open_meteo`, `run_not_available`, `ca_invalid` 제외 사유를 집계한다.

결합 CSV 권장 컬럼:

```text
timestamp_utc, prediction_origin_utc, forecast_horizon_minutes
dsr_raw, ca_raw_provider, ca_fraction, solar_elevation_deg
open_meteo_model, open_meteo_run_utc, open_meteo_available_at_utc
open_meteo_valid_time_utc, open_meteo_dni_W_m2, open_meteo_cloud_cover_pct
reference_dni_W_m2, reference_type, dsr_unit_assumption
alignment_method, quality_flag, exclusion_reason
```

### 4.4 Phase 3 완료 조건

- [x] explicit model/run으로 개발 기간 Open-Meteo 자료를 복원했다.
- [x] `issued_at + 7시간` 공개 지연을 적용하고 prediction origin 이후에 이용 가능해진 run을 입력에서 제외했다.
- [x] 표본 timestamp와 자동 테스트로 KMA, 태양 위치, Open-Meteo run, DSR→DNI 계약을 확인했다.
- [x] 결합 전후 행 수와 제외 사유를 horizon별 artifact에 저장했다.
- [x] 유효 행은 10분 1,462개, 30분 1,404개, 60분 1,317개로 확인했다.

### 4.5 결합 결과 기록 양식

이 양식은 구현 전 기록용으로 남긴다. 실제 행 수·제외 사유는 horizon별
`quantile_model_summary.json`과 Stage 6 평가 JSON이 기준이다.

<details>
<summary>초기 빈 기록 양식 보기</summary>

| 항목 | 결합 전 | 결합 후 | 제외 수 | 제외 사유 |
|---|---:|---:|---:|---|
| KMA 행 | 미확인 | 미확인 | 미확인 | 미작성 |
| Open-Meteo 행 | 미확인 | 미확인 | 미확인 | 미작성 |
| 10분 뒤 DSR 환산 DNI 정답값 | 미확인 | 미확인 | 미확인 | 미작성 |

수동 검증 timestamp 10개:

| timestamp UTC | KMA DSR | KMA CA | Open-Meteo DNI | 선택 snapshot | 판정 |
|---|---:|---:|---:|---|---|
| 미작성 |  |  |  |  |  |

</details>

### 4.6 결합 분석 결론

- 시간 정렬: `preceding_hour_mean`, 선형 보간 없음, `issued_at + 7시간` 적용
- 미래 정보 누수: horizon별 검사 통과
- 평가 가능 표본: 10분 1,462개, 30분 1,404개, 60분 1,317개
- 정답값 한계: 독립 DNI 실측이 아닌 미래 KMA DSR의 pvlib Erbs 변환값
- Phase 4~6: 완료, 10·30·60분 모델 동결

## 5. Phase 4 — 실험 dataset과 CLI

목표는 Phase 3 결합 결과를 동일한 명령과 seed로 재현 가능한 실험 입력으로 만드는 것이다.

### 5.1 확정된 모델 데이터 계약

10분 모델은 예측 기준 시각에 이미 알 수 있는 정보만 입력으로 사용한다.

| 구분 | 내용 |
|---|---|
| 원본 입력 | 현재 시각 KMA DSR·CA, 당시 이용 가능한 Open-Meteo DNI, 좌표·시각·horizon |
| 모델 입력 5열 | intercept, 두 DNI 기준값의 결합, Open-Meteo 비율, 구름 투과율, advection 영향 |
| advection | 위성 patch가 없어 현재 항상 `0.0` |
| 정답 | 10분 뒤 KMA DSR을 pvlib Erbs로 변환한 `10분 뒤 DSR 환산 DNI` |
| 금지 정보 | 10분 뒤 DSR·CA 및 prediction origin 이후 이용 가능해진 Open-Meteo run |
| 첫 horizon | 10분 |

정답 생성에 10분 뒤 DSR을 사용하지만 이는 채점용일 뿐 모델 입력에는 들어가지 않는다. 정답 field는 `reference_dni_W_m2`, 내부 reference 종류는 기존 코드 계약인 `dsr_proxy`를 유지한다.

### 5.2 확정된 시간 분할

| 구간 | UTC 기간 | 역할 | 10분 모델 유효 행 |
|---|---|---|---:|
| train | 2026-10-05 00:00 ~ 2026-10-28 23:50 | weight 학습 | 1,266 |
| validation | 2026-10-29 00:00 ~ 2026-10-30 23:50 | 모델 사양 선택·검증 | 97 |
| diagnostic | 2026-10-31 00:00 ~ 2026-11-01 23:50 | 과거 보고 전용, 사양 변경 근거로 사용 금지 | 97 |
| final test | 2026-11-02 00:00 ~ 2026-11-05 23:50 | 동결 모델의 최종 평가 1회 | 평가 전 미확정 |

10분 horizon에서는 다음 split이 00:00에 시작할 때 이전 split의 마지막 허용 prediction origin이 23:40이다. 23:50 origin의 정답 시각은 다음 날짜 00:00이므로 제외한다. 이 규칙으로 입력 행과 미래 정답이 서로 다른 split에 걸치는 누수를 막는다.

### 5.3 구현 결과

- 기존 통합 실험 CLI 대신 dataset builder와 horizon별 학습 CLI를 분리해 구현했다.
- schema·단위·timestamp·중복·누수 검사를 통과하지 못하면 학습 전에 종료한다.
- 개발용 28일은 UTC 날짜 기준 `24일 train / 2일 validation / 2일 diagnostic`으로 고정했다. 10분 모델의 실제 유효 행은 `1,266 / 97 / 97`이다.
- `2026-11-02~2026-11-05`의 4일은 개발 데이터와 분리된 final test다. 원시 timestamp는 576개이며 유효 평가 행 수는 final 평가 때 별도로 기록한다.
- split은 날짜뿐 아니라 `prediction_origin`과 정답 시각 `valid_time`을 함께 검사한다. 정답 시각이 다음 split에 걸치지 않도록 horizon 길이만큼 embargo를 적용한다.
- 학습 feature는 현재 DSR·CA, solar geometry, 동일 Open-Meteo run 값으로 구성한다.
- DSR·CA null과 CA 범위 밖 값은 임의 clipping·보간하지 않고 모델 행에서 제외한다.
- Git commit, 입력 SHA-256, split과 source run은 summary와 운영 model metadata에 저장한다.

완료 조건:

- [x] 기존 synthetic 회귀 테스트는 유지하되 현재 실데이터 CLI와 결과를 분리했다.
- [x] real mode가 결합 CSV를 읽어 10분 P10·P50·P90 예측까지 실행된다.
- [x] 같은 입력·seed로 split과 feature가 동일하게 생성된다.

## 6. Phase 5 — 학습·rolling-origin 예측

- 각 prediction origin에서 그 시점까지 이용 가능한 KMA 관측과 Open-Meteo run만 사용한다.
- baseline은 선택된 `open_meteo_dni_W_m2`, candidate는 KMA AI q10/q50/q90이다.
- baseline과 candidate는 동일 timestamp·동일 reference subset에서 비교한다.
- 모델 사양 선택은 train과 validation만 사용한다. diagnostic 결과는 이미 확인했으므로 모델이나 weight 범위를 변경하는 근거로 사용하지 않는다.
- 10/05~11/01 자료로 최종 가중치를 처음부터 재학습하고 동결했다. 11/02~11/05 final test는 한 번만 평가하고 결과를 보고 모델을 수정하지 않는다.
- horizon별 표본이 부족하면 합치지 말고 `insufficient_sample`로 표시한다.
- weight·feature version·학습 표본 범위와 수를 저장하고 재로드 예측 일치를 검증한다.

누수 방지 테스트:

- [x] 미래 KMA 관측을 넣으면 실패한다.
- [x] 미래 Open-Meteo run을 선택하면 실패한다.
- [x] final test 기간과 겹치는 KMA CSV가 fitting 입력이면 학습 코드가 실패한다.
- [x] train/validation/diagnostic 경계에서 정답 시각까지 겹치지 않도록 embargo를 검증한다.

## 7. Phase 6 — 지표·EDA·판정

동일 평가 행에서 다음을 계산한다.

- Open-Meteo와 q50: MAE, RMSE, Bias
- q10/q50/q90: Pinball Loss
- q10~q90: PICP와 평균 spread
- 전체·horizon·시간대·태양 고도·CA regime별 `sample_count`
- DSR/CA/Open-Meteo/reference 결측률과 제외 사유

필수 산출물:

```text
run_metadata.json, dataset_summary.json, model_weights.json
predictions.csv, metrics_overall.csv, metrics_by_horizon.csv
metrics_by_hour.csv, metrics_by_cloud_regime.csv
timeseries_comparison.png, residual_by_hour.png, quantile_calibration.png
```

판정 원칙:

- 표본 수가 작은 구간은 성능 우열을 단정하지 않는다.
- DSR 환산 DNI가 독립적인 실측값이 아니라는 한계를 모든 표와 그래프에 표시한다.
- 전체 평균 개선만으로 채택하지 않고 Bias·구간별 퇴행·PICP를 함께 본다.
- 성능 개선이 없어도 재현 가능한 결과와 원인 분석이 있으면 실험은 완료로 본다.

## 8. Phase 3~6 전체 흐름

```text
[KMA EDA 완료]
  2,016 timestamps / DSR valid 735 / CA valid 1,998
        │
        ▼
Phase 3A  Open-Meteo archived run 수집
  ECMWF IFS 0.25° single run + issued_at 7시간 뒤 공개 규칙
        │
        ├─ 당시 이용 불가 run → 제외 사유 기록
        ▼
Phase 3B  DSR → DSR 환산 DNI
  solar elevation + clear sky + pvlib Erbs, low-sun/결측 제외
        │
        ▼
Phase 3C  UTC 결합
  KMA 10분 + 동일 Open-Meteo run + 10분 뒤 DSR 환산 DNI 정답값
        │
        ▼
Phase 4  실험 dataset/CLI
  schema 검증 → 10일 train / 2일 validation / 2일 diagnostic
  각 split 경계에 horizon만큼 embargo 적용
        │
        ▼
Phase 5  rolling-origin 학습·예측
  Open-Meteo baseline vs KMA AI q10/q50/q90, 누수 차단
        │
        ▼
Phase 6  동일 표본 평가
  MAE/RMSE/Bias + Pinball/PICP + 구간별 sample_count
        │
        ▼
[모델 사양 동결]
  필요 시 10/05~11/01 전체로 최종 weight 1회 재학습
        │
        ▼
[11/02~11/05 final test 1회 평가]
  결과 확인 후 모델 수정 금지
        │
        ▼
[분석 문서와 artifact에 결과·한계·후속 수집 결정 기록]
```

## 9. Stage 4 P10·P50·P90 validation 결과

### 9.1 한 줄 결론

**P50은 프로토타입 단계에서 충분히 유망하고 P10~P90 구간도 전체적으로 잘 작동한다.** 다만 P10과 P90 각각의 보정은 아직 완벽하지 않아 운영 가능한 최종 모델이라고 판단하기에는 이르다.

이 평가는 학습에 사용하지 않은 validation 97행으로 수행했다. 동결한 2026-11-02~2026-11-05 final test 데이터는 사용하지 않았다.

### 9.2 분위수별 보정 상태

| 평가 항목 | 결과 | 이상적인 값 | 해석 |
|---|---:|---:|---|
| 실제값 ≤ P10 | 5.2% | 10% | P10이 너무 낮고 보수적 |
| 실제값 ≤ P50 | 56.7% | 50% | P50이 약간 높은 편 |
| 실제값 ≤ P90 | 85.6% | 90% | P90이 고일사량을 다소 낮게 예측 |
| P10~P90 포함률 | 82.5% | 80% | 전체 불확실성 구간은 양호 |
| 분위수 교차 | 0건 / 97건 | 0건 | 정상 |
| 평균 구간 폭 | 315.2 W/m² | 기준 미정 | 다소 넓을 가능성이 있음 |

P10, P50, P90은 각각 “낮은 경우”, “가운데 경우”, “높은 경우”의 10분 뒤 DSR 환산 DNI를 예측한 값이다. 예를 들어 P10~P90 구간은 실제 정답값 약 80%가 들어오도록 만드는 것이 목표다.

### 9.3 P50 성능

P50은 같은 validation 행에서 Open-Meteo 기준 예측보다 상당히 좋은 결과를 보였다.

| 모델 | MAE | RMSE | Bias |
|---|---:|---:|---:|
| Open-Meteo | 163.94 W/m² | 208.71 W/m² | -35.05 W/m² |
| P50 모델 | 61.96 W/m² | 122.54 W/m² | -1.47 W/m² |
| 개선율 | **62.2%** | **41.3%** | 편향 대폭 감소 |

- `MAE`: 평균적으로 정답에서 얼마나 떨어졌는지를 나타낸다. 낮을수록 좋다.
- `RMSE`: 큰 오차에 더 큰 벌점을 주는 오차다. 낮을수록 좋다.
- `Bias`: 계속 높게 또는 낮게 예측하는 경향이다. 0에 가까울수록 좋다.

따라서 **P50은 현재 validation 데이터에서 충분히 유망하다.** 평균적으로 한쪽 방향으로 치우치는 정도도 거의 없다.

### 9.4 P10·P90 해석

P10~P90 구간은 실제값의 82.5%를 포함했다. 목표인 80%와 가까워서 **불확실성 구간 전체의 성능은 합격 수준**이다. 또한 P10이 P50보다 높아지는 등의 분위수 순서 오류는 한 건도 없었다.

다만 양쪽 경계가 균형 있게 맞은 것은 아니다.

- 실제값이 P10보다 낮았던 비율은 5.2%로 목표 10%보다 작다. P10이 필요 이상으로 낮다는 뜻이다.
- P10의 39.2%가 후처리 과정에서 `0`이 되었다. 야간·저일사량 영향도 있겠지만 하한이 지나치게 보수적인 원인일 수 있다.
- 실제값이 P90 이하였던 비율은 85.6%로 목표 90%보다 작다. 높은 일사량을 일부 낮게 예측했다는 뜻이다.
- 따라서 **너무 낮은 P10이 다소 낮은 P90을 보상하면서 전체 포함률 82.5%가 나온 상태**다.

즉, 전체 포함률만 보고 P10과 P90이 각각 모두 정확하게 학습됐다고 판단하면 안 된다. 평균 구간 폭도 315.2 W/m²로 넓은 편일 수 있지만, 아직 비교 대상과 합격 기준을 정하지 않았으므로 구간 폭의 적정성은 확정하지 않는다.

### 9.5 초기 14일 Stage 4 판정과 한계

| 대상 | 현재 판정 |
|---|---|
| P50 | 프로토타입 학습 성공, 다음 단계 진행 가능 |
| P10~P90 전체 구간 | 기능과 전체 포함률 양호 |
| P10 | 지나치게 낮고 0에 붙는 비율이 높아 추가 점검 필요 |
| P90 | 높은 실제값을 목표보다 자주 놓쳐 추가 점검 필요 |
| 당시 V1 final-test 성능 | 미평가 (현재 결과는 11장 참고) |

Validation은 97행뿐이며 단일 지역·짧은 기간의 데이터다. 또한 여기서 정답으로 사용한 `10분 뒤 DSR 환산 DNI`는 독립적인 실측 DNI가 아니라 KMA DSR을 물리식으로 변환한 값이다. 따라서 실제 DNI에 대한 정확도와 다른 계절·지역에서의 일반화 성능까지 입증된 것은 아니다.

현재 결과는 **“Stage 4 구현은 성공했고 P50은 유망하지만, P10·P90은 calibration(각 분위수가 목표 비율에 맞는지) 점검이 필요한 상태”**로 판정한다. P10이 0이 된 행과 실제값이 P90을 넘은 행은 validation 안에서 원인을 확인하되, final test는 모델과 판단 기준을 확정한 뒤 마지막 평가에만 사용한다.

분석 산출물:

- `data/fr01/archive/previous_work/legacy_pre_20261005_extension/model/quantile_stage4/quantile_model_summary.json`
- `data/fr01/archive/previous_work/legacy_pre_20261005_extension/model/quantile_stage4/validation_quantile_predictions.csv`

### 9.6 학습 기간 확장 후 재학습 비교 — 2026-11-08

10/05~10/18 데이터를 train에만 추가하고 P10·P50·P90 가중치를 공통 초기값에서 다시 학습했다. feature, weight 범위, optimizer, validation 기간과 후처리 규칙은 변경하지 않았다. 이전 모델과 새 모델은 동일한 10/29~10/30 validation 97행에서 비교했으며 final test는 사용하지 않았다.

| 항목 | 이전 모델<br>train 10/19~10/28 | 새 모델<br>train 10/05~10/28 | 변화 |
|---|---:|---:|---:|
| train 유효 행 | 503 | 1,266 | +763행 |
| P50 MAE | 61.96 | 62.29 W/m² | 0.5% 악화, 사실상 동일 |
| P50 RMSE | 122.54 | 122.23 W/m² | 0.3% 개선 |
| P10 Pinball Loss | 14.84 | 12.55 W/m² | **15.4% 개선** |
| P50 Pinball Loss | 30.98 | 31.14 W/m² | 0.5% 악화 |
| P90 Pinball Loss | 27.44 | 26.85 W/m² | **2.2% 개선** |
| P10~P90 포함률 | 82.5% | 82.5% | 동일 |
| 평균 구간 폭 | 315.23 | 272.72 W/m² | **13.5% 감소** |
| 원시 분위수 교차 | 0/97 | 0/97 | 정상 유지 |

분위수 calibration 비교:

| 평가 항목 | 이상적 비율 | 이전 | 새 모델 | 해석 |
|---|---:|---:|---:|---|
| 실제값 ≤ P10 | 10% | 5.2% | 6.2% | 목표 쪽으로 개선됐지만 P10은 아직 낮음 |
| 실제값 ≤ P50 | 50% | 56.7% | 56.7% | 변화 없음, P50이 약간 높은 편 |
| 실제값 ≤ P90 | 90% | 85.6% | 86.6% | 목표 쪽으로 개선됐지만 상단을 아직 일부 놓침 |
| P10이 0인 비율 | 낮을수록 유리 | 39.2% | 39.2% | 개선 없음 |
| P90이 상한 1.05인 비율 | 참고값 | 6.2% | 6.2% | 변화 없음 |

판정: 새 모델은 P50 정확도를 사실상 유지하면서 P10·P90 loss를 줄이고 같은 82.5% 포함률을 더 좁은 구간으로 달성했다. 따라서 **이전 모델보다 효율적인 불확실성 구간을 만든 개선 모델**로 본다. 다만 P10 6.2%와 P90 86.6%는 이상적인 10%·90%에 아직 미치지 못하며, P10의 0 clipping 39.2%도 남았다. 표본이 97행뿐이므로 추가 보정계수를 반복 튜닝하기보다 현재 결과를 Stage 4 후보로 유지하는 편이 안전하다.

결정: 2026-11-08에 위 결과를 채택해 **10분 Stage 4 모델 사양을 동결**했다. Diagnostic과 final test를 보고 feature, 전처리, weight 범위 또는 후처리 규칙을 다시 조정하지 않는다.

새 산출물:

- `data/fr01/model/quantile_stage4_train_20261005/quantile_model_summary.json`
- `data/fr01/model/quantile_stage4_train_20261005/validation_quantile_predictions.csv`
- `data/fr01/model/quantile_stage4_train_20261005/validation_comparison.json`

### 9.7 Stage 5 — 10·30·60분 horizon 비교 계획 및 결과 기록

30분·60분 구현 결과를 동결된 10분 모델과 함께 기록한다. 세 모델 모두 같은 개발 dataset과 split 계약을 사용했으며 final test는 사용하지 않았다. Diagnostic 결과는 artifact에 보고용으로만 보존하고 아래 판정에는 validation만 사용했다.

#### 비교 목적

- 예측 거리가 10분에서 30분·60분으로 늘어날 때 오차가 얼마나 증가하는지 확인한다.
- 최근 KMA DSR·CA를 결합한 모델이 각 horizon에서 Open-Meteo보다 나은지 확인한다.
- 예측 거리가 길어질수록 P10~P90 구간이 합리적으로 넓어지는지 확인한다.
- P10·P50·P90 calibration이 horizon별 목표 비율 `10%·50%·90%`에 가까운지 확인한다.

#### Horizon과 모델 구조

| 항목 | 10분 | 30분 | 60분 |
|---|---|---|---|
| 상태 | 동결 | 동결 | 동결 |
| prediction origin | `t` | `t` | `t` |
| 정답 시각 | `t+10분` | `t+30분` | `t+60분` |
| 모델 수 | P10·P50·P90 3개 | P10·P50·P90 3개 | P10·P50·P90 3개 |
| 모델 종류 | 선형 분위수 회귀 | 선형 분위수 회귀 | 선형 분위수 회귀 |
| optimizer | L-BFGS-B | L-BFGS-B | L-BFGS-B |
| loss | quantile별 pinball loss | 동일 | 동일 |
| 다음 split 00:00 기준 마지막 origin | 23:40 | 23:20 | 22:50 |
| artifact | `model/quantile_stage4_train_20261005` | `model/stage5/horizon_30min` | `model/stage5/horizon_60min` |

30분과 60분은 10분 weight를 복사하지 않고 공통 초기값에서 각각 새로 학습한다. 각 horizon은 독립적인 P10·P50·P90 weight를 갖는다.

#### 입력 feature 비교

세 horizon 모두 feature의 이름·순서·단위는 동일하게 유지한다.

| 순서 | Feature | 10·30·60분 공통 의미 | Horizon별 차이 |
|---:|---|---|---|
| 0 | `intercept` | 항상 `1.0` | 없음 |
| 1 | `direct_prior_factor` | 현재 KMA DSR 환산 DNI와 Open-Meteo DNI의 결합 | `obs_weight=exp(-horizon_hours/1.5)`가 달라짐 |
| 2 | `open_meteo_factor` | Open-Meteo DNI / target clear-sky DNI | target 시각이 달라짐 |
| 3 | `cloud_transmittance` | `1 - 현재 KMA CA/100` | 없음 |
| 4 | `advection_impact` | 현재 `0.0` | 없음 |

입력은 항상 prediction origin `t`까지 알 수 있는 값만 사용한다. `t+30`, `t+60`의 DSR은 각 모델의 정답 생성에만 사용하며 feature에는 넣지 않는다. Open-Meteo는 `available_at <= t`인 run만 허용한다.

#### 모델 weight 기록

Feature 순서는 `intercept / direct prior / Open-Meteo / cloud / advection`이다.

| Horizon | Quantile | intercept | direct prior | Open-Meteo | cloud | advection |
|---:|---|---:|---:|---:|---:|---:|
| 10분 | P10 | -0.13177931 | 0.58297387 | 0.00000000 | 0.38425539 | 0.00000000 |
| 10분 | P50 | -0.00540647 | 1.00847785 | 0.00000000 | 0.01583867 | 0.00000000 |
| 10분 | P90 | 0.36392468 | 0.75668485 | 0.00000000 | -0.07916727 | 0.00000000 |
| 30분 | P10 | -0.12971104 | 0.27737787 | 0.12187937 | 0.46312192 | 0.00000000 |
| 30분 | P50 | -0.00219378 | 0.89721496 | 0.01722439 | 0.12037785 | 0.00000000 |
| 30분 | P90 | 0.46923841 | 0.70963653 | 0.00000000 | -0.08975113 | 0.00000000 |
| 60분 | P10 | -0.17684178 | 0.00000000 | 0.50844659 | 0.34669073 | 0.00000000 |
| 60분 | P50 | -0.01639051 | 0.82448128 | 0.00000000 | 0.21407203 | 0.00000000 |
| 60분 | P90 | 0.50000000 | 0.69752941 | 0.00000000 | -0.05148021 | 0.00000000 |

60분 P10의 direct prior가 하한 `0.0`, 60분 P90 intercept가 상한 `0.5`에 도달했다. P90 intercept만 `±0.75`로 넓힌 후보를 validation으로 한 번 비교했으며, 결과는 아래와 같다.

| 60분 P90 intercept 범위 | P90 weight | P90 Pinball | `P(y≤P90)` | P10~P90 포함률 | 평균 구간 폭 | 판정 |
|---|---:|---:|---:|---:|---:|---|
| `±0.5` | 0.5000 | 42.14 | 87.8% | 81.7% | 452.96 W/m² | 채택 |
| `±0.75` | 0.5920 | 42.51 | 89.0% | 82.9% | 493.81 W/m² | 기각 |

확장 후보는 P90 calibration 하나는 좋아졌지만 pinball loss가 커지고 구간도 약 40.86 W/m² 넓어졌다. 따라서 전체적인 확률 예측 품질과 구간 효율을 기준으로 기존 `±0.5`를 유지했다. 이 비교에서는 diagnostic 계산을 건너뛰었고 final test도 사용하지 않았다.

#### 평가 표본 규칙

두 종류의 결과를 구분해 기록한다.

1. **Horizon별 유효 표본 평가**: 각 horizon에서 실제로 사용할 수 있는 모든 validation 행을 평가한다. 운영 관점의 실제 성능을 보여준다.
2. **공통 표본 비교**: 10·30·60분 모두 정답과 입력이 존재하는 validation origin의 교집합에서 세 모델을 비교한다. 결측 행 차이 때문에 특정 horizon이 유리해지는 것을 막는다.

각 horizon 안에서 모델과 Open-Meteo baseline은 반드시 완전히 동일한 행을 사용한다.

#### 표본 수와 결측 기록

| Horizon | 전체 생성 행 | Train | Validation | Diagnostic | Validation 공통 표본 | 주요 제외 사유 |
|---:|---:|---:|---:|---:|---:|---|
| 10분 | 1,462 | 1,266 | 97 | 97 | 78 | DSR·target DSR 결측 |
| 30분 | 1,404 | 1,216 | 91 | 91 | 78 | target DSR 결측 112, 마지막 target timestamp 3개 없음 |
| 60분 | 1,317 | 1,141 | 82 | 82 | 78 | target DSR 결측 196, 마지막 target timestamp 6개 없음 |

#### P50과 Open-Meteo 비교

아래 표는 우선 horizon별 전체 유효 validation 행을 기준으로 작성한다. 공통 표본 결과는 별도 표나 artifact에 함께 저장한다.

| Horizon | 표본 수 | 모델 P50 MAE | Open-Meteo MAE | MAE 개선율 | 모델 P50 RMSE | Open-Meteo RMSE | RMSE 개선율 | P50 Bias |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10분 | 97 | 62.29 | 163.94 | 62.0% | 122.23 | 208.71 | 41.4% | -0.42 W/m² |
| 30분 | 91 | 83.62 | 159.20 | 47.5% | 133.15 | 205.58 | 35.2% | +2.93 W/m² |
| 60분 | 82 | 117.43 | 157.34 | 25.4% | 181.84 | 205.94 | 11.7% | -6.43 W/m² |

MAE·RMSE는 낮을수록 좋다. Bias는 0에 가까울수록 좋으며, 음수면 평균적으로 낮게 예측하고 양수면 높게 예측한다.

세 horizon 모두 존재하는 공통 validation origin 78개에서의 비교:

| Horizon | 모델 P50 MAE | Open-Meteo MAE | MAE 개선율 | 모델 P50 RMSE | Open-Meteo RMSE | RMSE 개선율 |
|---:|---:|---:|---:|---:|---:|---:|
| 10분 | 52.20 | 146.03 | 64.3% | 101.22 | 192.97 | 47.5% |
| 30분 | 76.83 | 143.04 | 46.3% | 124.69 | 187.55 | 33.5% |
| 60분 | 103.57 | 149.01 | 30.5% | 163.51 | 197.87 | 17.4% |

공통 표본에서도 horizon이 길어질수록 모델 오차가 증가하고 Open-Meteo 대비 개선 폭이 감소하는 동일한 경향이 확인됐다. 따라서 단순히 horizon별 결측 차이 때문에 생긴 결과는 아니다.

#### 분위수와 calibration 비교

| Horizon | P10 Pinball | P50 Pinball | P90 Pinball | `P(y≤P10)` | `P(y≤P50)` | `P(y≤P90)` | P10~P90 포함률 | 평균 구간 폭 | P10=0 비율 | 원시 교차 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10분 | 12.55 | 31.14 | 26.85 | 6.2% | 56.7% | 86.6% | 82.5% | 272.72 W/m² | 39.2% | 0/97 |
| 30분 | 17.37 | 41.81 | 32.86 | 6.6% | 69.2% | 85.7% | 81.3% | 381.67 W/m² | 44.0% | 0/91 |
| 60분 | 22.53 | 58.71 | 42.14 | 7.3% | 61.0% | 87.8% | 81.7% | 452.96 W/m² | 46.3% | 0/82 |

Calibration 목표는 `P(y≤P10)≈10%`, `P(y≤P50)≈50%`, `P(y≤P90)≈90%`, P10~P90 포함률 약 `80%`다. 포함률만 맞고 양 끝 비율이 틀릴 수 있으므로 네 값을 함께 해석한다.

#### 간단한 해석

- 중앙 예측: 전체 validation P50 MAE는 `62.29 → 83.62 → 117.43 W/m²`, RMSE는 `122.23 → 133.15 → 181.84 W/m²`로 horizon이 길어질수록 증가했다.
- Baseline 비교: 30분과 60분 모두 Open-Meteo보다 낮은 MAE·RMSE를 보였다. 다만 MAE 개선율은 `62.0% → 47.5% → 25.4%`로 감소해 최근 KMA 관측의 이점이 시간과 함께 약해지는 방향이 확인됐다.
- 불확실성: 평균 P10~P90 폭은 `272.72 → 381.67 → 452.96 W/m²`로 증가했다. 먼 미래의 불확실성이 커지는 논리적인 방향이다.
- Calibration: 포함률은 세 horizon 모두 약 `81~83%`로 목표 80%와 가깝다. 그러나 30분 P50 누적 비율은 69.2%로 중앙값 목표 50%보다 높아 분포상 P50이 높은 편이다. 60분은 61.0%로 여전히 높지만 30분보다 목표에 가깝다.
- P10: 실제값이 P10 이하인 비율은 30분 6.6%, 60분 7.3%로 목표 10%보다 작다. P10 zero 비율도 44.0%·46.3%로 높아 하단이 여전히 보수적이다.
- P90: 누적 비율은 30분 85.7%, 60분 87.8%로 목표 90%에 근접했다. 60분 P90 intercept 범위 확장 비교까지 마쳤으며 validation의 pinball loss와 구간 폭을 근거로 기존 상한 0.5를 유지했다.
- 표본 한계: validation은 30분 91행, 60분 82행으로 작다. 현재 결과는 Stage 5 구현 성공과 초기 유효성을 보여주지만 강한 일반화 성능 주장은 할 수 없다.
- 현재 판정: 30분은 Open-Meteo 대비 개선이 크고 구간 포함률도 양호하다. 60분은 RMSE 개선이 11.7%로 작다는 한계가 있지만 validation 기반 경계 비교까지 끝냈다. 2026-11-08에 10·30·60분 사양을 모두 동결했다.

#### Stage 5 산출물

```text
data/fr01/model/stage5/
  horizon_30min/
    dataset.csv
    validation_predictions.csv
    diagnostic_predictions.csv
    quantile_model_summary.json
  horizon_60min/
    dataset.csv
    validation_predictions.csv
    diagnostic_predictions.csv
    quantile_model_summary.json
  horizon_comparison.json
```

비교 결과는 `data/fr01/model/stage5/horizon_comparison.json`에 재현 가능하게 저장했다. 이 절에는 validation 결과만 기록했다. Diagnostic은 보고 전용으로 별도 artifact에 보존하고 모델 채택·튜닝 근거로 사용하지 않는다.

### 9.8 Stage 6 — 동결 모델 저장·로드와 Validation 구간 평가

동결된 10·30·60분 weight를 backend 운영 artifact로 저장하고 다시 로드했을 때 같은 예측이 나오는지 검증했다. 모델 선택은 이미 끝났으므로 이 단계에서는 weight나 feature를 다시 조정하지 않았다.

```text
모델 버전       fr01_kma_quantile_v1
운영 경로       backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1/
평가 결과       data/fr01/model/stage6/validation_segment_evaluation.json
factor 최대 오차 0.0 (허용 1e-10)
DNI 최대 오차    0.0 W/m² (허용 1e-6)
Diagnostic       평가하지 않음
Final test       사용하지 않음
```

Loader는 manifest SHA-256, schema·model version, 10·30·60분 horizon, feature 이름과 순서, weight 개수·범위·유한값, 전처리·후처리 계약을 검사한다. 파일이 바뀌거나 계약이 맞지 않으면 예측하지 않고 오류로 중단한다.

Validation을 한국 시간대, 태양 고도, 구름 구간으로 나눠 확인했다. 전체 결과는 Stage 5와 동일했고 세 horizon 공통 origin은 78개였다. 공통적인 경향은 다음과 같다.

- 오후 구간은 horizon이 길어질수록 P50 MAE가 `126.94 → 144.07 → 206.68 W/m²`로 증가했다.
- 태양 고도 20~45° 구간의 P50 MAE는 `59.71 → 72.05 → 95.44 W/m²`였다.
- 낮은 태양 고도 5~20°의 60분 P50 MAE는 `295.79 W/m²`로 컸지만 표본은 9개뿐이다.
- 태양 고도 45° 이상 표본은 0개였다. 서울의 10월 말 Validation 기간 특성으로 이 구간 성능은 평가할 수 없다.
- 부분 흐림 20~70% 표본은 horizon별 7~8개뿐이다. 해당 MAE와 포함률은 참고값이며 일반화 성능으로 주장하지 않는다.

이 평가는 문제 구간을 찾기 위한 분석이며 모델을 다시 튜닝하는 근거로 사용하지 않는다. 다음 단계의 final test는 동결된 artifact를 변경하지 않은 상태에서 한 번만 수행한다.

## 10. 모델 실험 기록 양식

실험마다 아래 형식을 복사해 사용한다. baseline과 새 모델은 반드시 같은 평가 구간과 표본을 사용한다.

<details>
<summary>새 실험용 빈 양식 보기</summary>

### 실험 ID: 미작성

| 항목 | 내용 |
|---|---|
| 목적 | 미작성 |
| 실행 일시 | 미작성 |
| Git commit | 미작성 |
| 입력 기간 | 미작성 |
| 학습/검증/평가 분할 | 미작성 |
| baseline | 미작성 |
| 모델·파라미터 | 미작성 |
| feature | 미작성 |
| target | `10분 뒤 DSR 환산 DNI` (`reference_type=dsr_proxy`) |
| random seed | 미작성 |
| 산출물 경로 | 미작성 |

#### 전체 지표

| 모델 | MAE | RMSE | Bias | 표본 수 |
|---|---:|---:|---:|---:|
| baseline | 미확인 | 미확인 | 미확인 | 미확인 |
| candidate | 미확인 | 미확인 | 미확인 | 미확인 |

#### 구간별 지표

맑음/부분 구름/흐림, 예측 horizon, 시간대 등 실제 분석에 사용한 구간 정의를 함께 기록한다.

| 구간 | 모델 | MAE | RMSE | Bias | 표본 수 |
|---|---|---:|---:|---:|---:|
| 미작성 | baseline |  |  |  |  |
| 미작성 | candidate |  |  |  |  |

#### 해석과 결정

- 관찰된 결과: 미작성
- 실패 사례: 미작성
- 데이터 또는 방법상의 한계: 미작성
- 다음 실험에서 바꿀 한 가지: 미작성
- 채택 여부와 근거: 미작성

</details>

## 11. 동결 V1 Final test 결과 — 2026-11-08

### 11.1 실행 계약

- 기간: `2026-11-02T00:00:00Z ~ 2026-11-05T23:50:00Z`
- 모델: `fr01_kma_quantile_v1`, horizon 10·30·60분
- 입력: 동결 KMA 576행, Open-Meteo archived run 18개·504행
- 평가 방식: 학습·최적화 기능이 없는 전용 평가기로 1회 실행
- 결과 위치: `data/fr01/model/final_test_20261102_20261105/`
- 결과 상태: `completed_no_retuning_allowed`
- 모델 변경: 없음. Final test 결과를 이용한 V1 수정·재학습·재선택 금지

입력과 모델 hash는 준비 manifest와 일치했다. 모델 manifest SHA-256은 실행 전후
`1893afa4f6ab948ee169967594e9a5792b83b0f9e899d0d645bdd8f43e7e330a`로 동일하다.

### 11.2 P50과 Open-Meteo 비교

모델과 Open-Meteo는 각 horizon에서 완전히 동일한 유효 행으로 비교했다.

| Horizon | 유효 행 | 모델 MAE | OM MAE | MAE 개선율 | 모델 RMSE | OM RMSE | RMSE 개선율 | 모델 Bias |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10분 | 180 | 73.16 | 167.19 | 56.2% | 131.98 | 214.63 | 38.5% | -2.57 |
| 30분 | 168 | 122.30 | 167.16 | 26.8% | 183.05 | 216.10 | 15.3% | -23.04 |
| 60분 | 151 | 177.54 | 173.25 | -2.5% | 231.59 | 224.98 | -2.9% | -59.62 |

오차와 Bias의 단위는 `W/m²`다. 양의 개선율은 모델이 Open-Meteo보다 좋다는 뜻이다.

- **10분:** MAE·RMSE 모두 크게 개선됐고 Bias도 `-2.57 W/m²`로 작았다.
- **30분:** Open-Meteo보다 좋지만 10분보다 개선 폭이 줄고 과소 예측 경향이 커졌다.
- **60분:** MAE·RMSE 모두 Open-Meteo보다 조금 나빴고 `-59.62 W/m²`의 과소 예측을 보였다.
- 따라서 V1의 실질적인 강점은 10분, 다음은 30분이며 60분 우위는 확인되지 않았다.

### 11.3 P10·P50·P90과 예측 구간

`누적 비율`은 실제 정답이 해당 분위수 예측값 이하였던 비율이다. 이상적인 값은
P10 `10%`, P50 `50%`, P90 `90%`다. P10~P90 구간의 명목 포함률은 `80%`다.

| Horizon | P10 누적 비율 | P50 누적 비율 | P90 누적 비율 | P10~P90 포함률 | 평균 구간 폭 |
|---:|---:|---:|---:|---:|---:|
| 10분 | 3.9% | 46.1% | 85.0% | 81.1% | 244.41 W/m² |
| 30분 | 4.8% | 45.2% | 78.6% | 73.8% | 361.07 W/m² |
| 60분 | 5.3% | 39.1% | 76.8% | 71.5% | 453.31 W/m² |

- 10분 구간 포함률은 명목 80%와 가깝지만 P10은 낮고 P90도 다소 낮아 양쪽 분위수의
  위치가 완벽하게 보정된 것은 아니다.
- 30분과 60분은 P90 누적 비율과 전체 포함률이 낮다. 특히 높은 일사량 정답을 상단
  경계가 충분히 덮지 못하는 경우가 늘어난다.
- horizon이 길수록 평균 구간 폭이 커지는 것은 미래 불확실성이 증가하는 논리적인
  방향이다. 다만 폭이 커져도 30·60분 포함률이 목표에 못 미쳤다.

### 11.4 유효 행과 한계

시간상 평가 가능한 origin은 10·30·60분 각각 `575 / 573 / 570`개였지만 실제 모델
평가 행은 `180 / 168 / 151`개다. 가장 큰 제외 원인은 prediction origin의 KMA DSR
결측으로, 주로 야간 및 관측 결측을 임의 보간하거나 0으로 채우지 않은 결과다. Target
DSR 결측과 기간 끝을 넘어가는 target도 제외했다.

이 결과는 서울 한 지점, 4일, DSR에서 환산한 DNI 정답값에 한정된다. 실제 DNI 센서에
대한 성능이나 다른 계절·지역의 일반화 성능을 의미하지 않는다. 특히 60분 결과가
나빴더라도 이 Final test를 보고 V1을 다시 맞추지 않는다. 후속 개선은 새 모델 버전과
새로운 holdout 기간으로 검증해야 한다.

## 12. 최종 결론 및 후속 과제

Phase 3~6에서 확인한 결론은 다음과 같다.

- **데이터 품질:** UTC 10분 축은 완전하지만 DSR은 야간과 일부 주간에 구조적 결측이 있다. 결측을 보간하거나 0으로 채우지 않았다.
- **정답값:** DSR 환산 DNI는 초기 비교에는 사용할 수 있지만 독립 DNI 센서의 실제값은 아니다.
- **Baseline 비교:** Validation에서는 세 horizon 모두 Open-Meteo보다 좋았다. Final test에서는 10·30분만 우위를 유지했고 60분은 MAE·RMSE가 각각 2.5%·2.9% 나빴다.
- **불확실성:** horizon이 길어질수록 오차와 평균 구간 폭이 커지는 논리적인 방향을 보였다. 일부 calibration과 작은 구간 표본은 한계로 남는다.
- **운영 적용 범위:** 동결 모델의 운영 worker·저장소·읽기 전용 API 연결까지 완료했다. 기본값은 Shadow이며 Active 전환과 배포는 팀 검토 대상이다.
- **Final test:** KMA 576행과 Open-Meteo archived run 18개·504행의 hash·시간 계약을 확인한 후 전용 평가기로 한 번 실행했다.
- **모델 동결:** 결과와 관계없이 V1을 수정하지 않는다. 60분 개선은 새 버전과 새 holdout으로만 검증한다.
- **추가 데이터:** 계절·지역 일반화를 주장하려면 이후 다른 계절과 위치 데이터가 더 필요하다.
- **다음 작업:** 배포 담당자가 Shadow 지표를 확인한 뒤 Active 전환 여부를 결정한다. 후속 모델 실험은 새로운 holdout을 먼저 확보한다.
