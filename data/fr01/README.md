# FR-01 기상·일사량 실험 데이터

FR-01 KMA AI nowcast의 확정 실험 입력, 원본 수집 응답, 모델 평가 결과를 보관한다.
모든 시각은 UTC이며, 일사량 단위는 별도 표기가 없으면 `W/m²`이다.

## 먼저 읽을 파일

| 목적 | 시작 파일 | 설명 |
| --- | --- | --- |
| 확정 모델 사양 확인 | `model/frozen_model_manifest.json` | 10·30·60분 horizon별 동결 모델, feature 순서와 선택 상태 |
| 개발 데이터 사용 | `datasets/development_20261005_20261101/dataset_manifest.json` | 기간, SHA-256, train/validation/diagnostic 분할 규칙 |
| 최종 평가 입력 확인 | `archive/frozen_final_test/final_test_20261102_20261105/input_readiness_manifest.json` | final test의 KMA·Open-Meteo 입력, 무결성 hash, 사용 규칙 |
| 최종 평가 결과 확인 | `model/final_test_20261102_20261105/final_test_metrics.json` | horizon별 P10/P50/P90 성능 지표 |

## 디렉터리 구조와 용도

```text
datasets/
  development_20261005_20261101/  # 모델 사양을 정하는 데 사용한 확정 개발 입력
archive/
  source_and_intermediate/         # KMA·Open-Meteo 원본, 변환 CSV, 수집 manifest
  frozen_final_test/               # 모델 동결 뒤 한 번만 평가한 분리 final test 입력
  previous_work/                   # 이전/폐기 실험; Git 미추적
model/
  frozen_model_manifest.json       # 최종 채택 모델의 진입점
  quantile_stage4_train_20261005/  # 10분 동결 모델 결과
  stage5/horizon_30min/            # 30분 동결 모델 결과
  stage5/horizon_60min/            # 60분 동결 모델 결과
  final_test_20261102_20261105/    # 분리 final test 예측과 metric
```

## 입력 데이터 읽기

### KMA GK2A

`kma_gk2a.csv`는 10분 간격 관측/위성 기반 입력이다.

- `timestamp_utc`: 관측 시각
- `dsr_raw`: direct solar radiation proxy
- `ca_raw_provider`, `ca_fraction`, `cloud_cover_pct`: 구름 관련 값
- `quality_flag`: 정규화·품질 확인 결과

개발용 정규화 입력은 `datasets/development_20261005_20261101/kma_gk2a.csv`에 있다. 원문 응답과 공급자별 변환본은 `archive/source_and_intermediate/raw/kma_gk2a/` 및 `normalized/kma_gk2a/`에서, 각 수집 단위의 경로와 SHA-256은 같은 폴더의 `manifests/` JSON에서 확인한다.

### Open-Meteo

`open_meteo_runs.csv`는 단일 시점 예보가 아니라, 발행된 forecast snapshot의 valid time별 DNI다.

- `snapshot_id`: 예보 실행(run) 식별자
- `issued_at_utc`: 예보 발행 시각
- `snapshot_available_at_utc`: 시스템에서 사용 가능하다고 간주한 시각 (`issued_at_utc + 7시간`)
- `valid_time_utc`: 예보 대상 시각
- `open_meteo_dni_W_m2`: DNI
- `temporal_meaning`: `preceding_hour_mean` — 해당 시각 직전 한 시간 평균

모델링 시에는 대상 시각과 snapshot 발행/가용 시각을 함께 고려해야 한다. 단순히 같은 `valid_time_utc`의 행 하나를 임의로 선택하면 미래 정보를 섞을 수 있다.

## 개발·진단·최종평가 경계

`datasets/development_20261005_20261101/dataset_manifest.json`의 split contract가 개발 데이터의 기준이다.

- Train: 2026-10-05 ~ 2026-10-28 UTC
- Validation: 2026-10-29 ~ 2026-10-30 UTC
- Diagnostic: 2026-10-31 ~ 2026-11-01 UTC — 보고 전용이며 모델 선택 근거로 사용하지 않음
- Final test: 2026-11-02 ~ 2026-11-05 UTC — fitting, feature/preprocessing/model 선택, tuning에 사용 금지

Final test에 필요한 Open-Meteo run은 `archive/frozen_final_test/.../open_meteo_runs.csv`와 `open_meteo_collection.json`에 있다. `dataset_manifest.json`보다 `input_readiness_manifest.json`을 final test의 입력·무결성 기준으로 사용한다.

## 모델 결과 읽기

각 horizon 폴더의 `quantile_model_summary.json`은 모델 종류, feature, split, 학습·검증 지표와 채택 근거를 담는다. `validation_quantile_predictions.csv` 및 final-test의 `horizon_*_predictions.csv`는 아래와 같이 읽는다.

- `prediction_origin_utc`: 예측을 생성한 기준 시각
- `valid_time_utc`: 예측 대상 시각
- `horizon_minutes`: 10, 30 또는 60분
- `reference_dni_W_m2`: 비교 기준 DSR proxy
- `p10_dni_W_m2`, `p50_dni_W_m2`, `p90_dni_W_m2`: 예측 분위수
- `open_meteo_dni_W_m2`: 비교용 Open-Meteo DNI

`final_test_metrics.json`의 `p50`은 중앙 예측 성능, `pinball_loss_W_m2`와 `empirical_cdf_at_quantile`은 분위수 품질, `picp_p10_p90`과 `mean_interval_width_W_m2`는 P10–P90 예측구간을 뜻한다.

## Git 공유 규칙

현재 확정 실험의 원본 수집 자료, 정규화 입력, manifest, 최종 모델 및 평가 결과는 Git으로 공유한다. 다음은 의도적으로 Git에서 제외한다.

- `archive/previous_work/`: 이전 또는 폐기 실험
- `model/stage5/validation_experiments/`: 기각된 후보
- `model/**/diagnostic_*_predictions.csv`: 모델 선택에 사용하지 않는 진단 출력

세부 실험 방법과 운영 모델 계약은 [`docs/fr01_4_README.md`](../../docs/fr01_4_README.md)를 기준으로 한다.
