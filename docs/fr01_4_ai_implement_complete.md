# FR-01.4 KMA AI 수집·모델 구현 인계

> **문서 역할:** 팀원이 수집기와 현재 V1 모델 상태를 빠르게 인계받기 위한 요약이다.
> 초기 7일 수집 수치는 이력으로 보존하며, 현재 모델은 28일 개발 dataset을 사용한다.

## 현재 인계 요약 — 2026-11-08

| 범위 | 상태 | 근거 |
|---|---|---|
| KMA 수집기·정규화·durable queue | 완료 | Phase 0~2와 실제 7일 backfill |
| 28일 KMA·Open-Meteo 개발 dataset | 완료 | `data/fr01/datasets/development_20261005_20261101/` |
| 10·30·60분 P10·P50·P90 모델 | 완료·동결 | `fr01_kma_quantile_v1` |
| Validation·구간별 평가 | 완료 | Stage 6 평가 JSON |
| 운영 JSON 저장·loader | 완료 | `backend/app/nowcast/model_artifacts/` |
| Final test | 미평가 | Open-Meteo final-test run 준비 필요 |
| 운영 API 연결 | 미구현 | final test와 팀 검토 후 진행 |

현재 모델의 정확한 계약과 결과는 각각 `fr01_4_ai_model_implementation.md`,
`fr01_4_ai_kma_analysis.md`를 따른다.

## 1. 목표와 범위

KMA GK2A `DSR`·`CA` 실데이터와 Open-Meteo 예보를 결합해 기존 synthetic KMA AI nowcast를 실제 데이터로 검증한다.

```text
KMA DSR·CA + Open-Meteo snapshot
  → UTC 정렬 및 DSR-based proxy DNI 생성
  → 시간순 학습·rolling-origin 예측
  → Open-Meteo baseline과 KMA AI q10/q50/q90 비교
  → 지표·EDA·재현 artifact 저장
```

이번 범위:

- KMA API 확인, 수집기, durable queue, raw/CSV/manifest 저장
- Open-Meteo·DSR proxy 결합과 미래 정보 누수 방지
- real-data CLI, 학습·예측, 지표·EDA, 테스트·문서화

제외 또는 후속 범위:

- DSR을 독립적인 실측 DNI로 간주하는 평가
- 공식 단위가 확인되지 않은 값을 확정 물리량으로 표현
- FastAPI 요청 중 KMA API 호출
- Redis/Celery 같은 분산 queue와 배포 scheduler 구성

이 문서는 완료 구현과 남은 Phase를 팀에 인계하기 위한 요약이다. 상세한 초기 계획과 이해 기록은 `docs/fr01_4_ai_implement.md`, 실제 데이터 분석과 실험 결과는 `docs/fr01_4_ai_kma_analysis.md`에서 관리한다.

## 2. 확정 계약

### 데이터와 시간

| 항목 | 계약 |
|---|---|
| endpoint | `https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph_sun_sat_txt` |
| 변수 | `DSR`(지면 하향단파복사), `CA`(운량) |
| 좌표 | `37.5714`, `126.9658` |
| 간격·시간대 | 10분, UTC |
| 확정 수집 범위 | `2026-10-26T00:00:00Z` ~ `2026-11-01T23:50:00Z` |
| 기대 행 | 1,008개(7일 × 144개) |
| reference | DSR 기반 proxy DNI, `reference_type=dsr_proxy` |
| DSR 단위 | 응답에 없음. 확인 전 `unknown`, `unit_confirmed=false` |
| CA 의미상 범위 | fraction `0~1`; 범위 밖 원문은 보존하되 모델 입력에서는 null |

API 키는 repository root `.env`의 `KMA_API_KEY`에서만 읽는다. `.env`와 `data/fr01/`은 Git에서 제외하며 키, `authKey`, 전체 요청 URL을 로그·예외·artifact·객체 표현에 남기지 않는다.

### 저장 구조

```text
data/fr01/
  raw/kma_gk2a/        # provider 원문 TXT, chunk별 저장
  normalized/          # 사람이 열 수 있는 UTF-8 CSV
  manifests/           # collection provenance와 queue journal
  discarded/           # 분석에서 제외한 probe 데이터
artifacts/fr01/         # Phase 4 이후 실험 결과
```

정규화 CSV 핵심 컬럼:

```text
timestamp_utc
requested_latitude, requested_longitude
returned_latitude, returned_longitude
dsr_raw
ca_raw_provider, ca_fraction, cloud_cover_pct
dsr_unit, unit_confirmed
quality_flag
```

규칙:

- `nan`, `6553.5`, `-9` 계열과 빈 문자열은 null이며 실제 `0.0`과 구분한다.
- timestamp 행은 결측이어도 삭제하지 않는다.
- CA `0~1`은 percent로 변환한다. 범위 밖 숫자는 `ca_raw_provider`에 보존하고 fraction·percent는 null, 품질은 `ca_out_of_range`로 기록하며 clipping하지 않는다.
- raw TXT, normalized CSV, JSON은 원자적으로 저장하고 raw SHA-256을 manifest에 기록한다.

## 3. Phase 0~2 완료 요약

### Phase 0 — API와 파이프라인 확인 `[완료]`

- [x] KMA API 키와 endpoint가 실제 `200 OK`로 동작한다.
- [x] 응답이 `TMFC | VAR | LON | LAT | timestamp...` 형태의 wide-format TXT임을 확인했다.
- [x] UTC 10분 간격, 요청 좌표와 반환 좌표, DSR·CA 값을 직접 확인했다.
- [x] API를 사용자 요청 시 호출하지 않는 사전 수집형 snapshot 구조를 확정했다.
- [x] backfill CLI와 `run_once()` worker가 같은 adapter/parser를 사용한다.
- [x] 초기 4시간·24시간 probe 데이터는 `data/fr01/discarded/20261105_probe/`로 분리했다.

### Phase 1 — 데이터 계약과 디렉터리 `[완료]`

- [x] raw TXT, normalized CSV, collection manifest, queue journal 경로를 확정했다.
- [x] UTC timestamp, 좌표, nullable, 실제 0과 결측, CA 원문/정규화 컬럼을 정의했다.
- [x] 잘못된 timestamp·DSR 음수·불연속 구간·raw hash 불일치를 거절한다.
- [x] API 키가 파일·로그·Git·객체 repr에 노출되지 않게 했다.

### Phase 2 — 수집기·durable queue·7일 수집 `[완료]`

구현 파일:

```text
backend/app/nowcast/kma_adapter.py
backend/app/nowcast/kma_dataset.py
backend/app/nowcast/kma_collection_queue.py
backend/app/nowcast/kma_worker.py
tools/ai_experiments/collect_fr01_kma_data.py
tests/test_fr01_kma_dataset.py
```

완료 기능:

- [x] timeout 90초, timeout·연결 실패·5xx 지수 backoff 재시도
- [x] 최대 24개 시점 단위 chunk 분할과 전역 동시 요청 기본값 3
- [x] DSR·CA 독립 수집 후 timestamp outer join
- [x] 성공 chunk 즉시 raw 저장, hash 검증, atomic journal 갱신
- [x] `pending/running/succeeded/failed`, 중단 복구, `--resume`, `--retry-failed`, `--prepare-only`
- [x] 모든 chunk 성공 후에만 normalized CSV와 collection manifest 생성
- [x] parser, 결측·CA 이상값, queue resume, artifact, worker 단위 테스트

실제 수집 결과:

| 항목 | 결과 |
|---|---:|
| queue | 84/84 succeeded(DSR 42 + CA 42) |
| CSV | 1,008행, 최초·최종 timestamp 일치 |
| DSR 유효값 | 361 |
| CA 유효값 | 1,000 |
| 품질 `ok` | 361 |
| `partial` | 639 |
| `missing` | 7 |
| `ca_out_of_range` | 1 |

주요 산출물:

```text
data/fr01/normalized/kma_gk2a/20261026T000000Z_20261101T235000Z_kma_gk2a.csv
data/fr01/manifests/20261026T000000Z_20261101T235000Z_collection.json
data/fr01/manifests/20261026T000000Z_20261101T235000Z_queue.json
```

### 수집 중 발견한 문제와 처리

| 문제 | 처리 |
|---|---|
| 요청당 최대 24개 시점 | 7일을 변수별 42개 chunk로 분할 |
| wide-format TXT | 원문 보존 후 timestamp별 long-format CSV로 변환 |
| 여러 결측 토큰과 실제 `0.0` | 결측만 null로 변환하고 행·실제 0은 유지 |
| 미래 시각도 결측 열로 반환 | 완전히 과거인 범위만 분석 자료로 사용 |
| DSR·CA 별도 응답 | timestamp outer join, 한쪽 결측은 null 유지 |
| 느린 응답·일시 장애 | timeout, retry, 성공 chunk 즉시 저장, resume |
| CA `1.1` 한 건 | raw `1.1` 보존, fraction·percent null, `ca_out_of_range` |
| DSR 단위 미표기 | `unknown`, `unit_confirmed=false`로 명시 |

수집 재현 명령:

```powershell
.\.venv\Scripts\python.exe tools\ai_experiments\collect_fr01_kma_data.py `
  --latitude 37.5714 --longitude 126.9658 `
  --start 2026-10-26T00:00:00Z --end 2026-11-01T23:50:00Z `
  --interval-minutes 10 --variables DSR CA `
  --output-dir data\fr01 --max-concurrency 3
```

Phase 0~2 완료 당시 DSR 단위는 미확정이었다. 이후 국가기상위성센터 공식 정의에서
`0~1500 W/m²`를 확인했다. 기존 CSV metadata는 생성 당시 상태를 보존하고 학습 명령에
단위 가정을 명시했다. 야간 중심 결측은 0으로 채우지 않고 제외 사유로 기록한다.

<details>
<summary>Phase 0~2 완료 당시 남은 Phase 3~8 계획 보기</summary>

## 4. Phase 3 — Open-Meteo와 proxy DNI 결합

- [ ] 같은 좌표·기간의 Open-Meteo DNI snapshot을 준비한다.
- [ ] `issued_at`과 `valid_time`을 구분하고 예측 시각에 이용 가능했던 snapshot만 고른다.
- [ ] 출처별 시간 정렬 허용오차를 명시하며 무조건 nearest/미래방향 보간하지 않는다.
- [ ] DSR proxy target을 같은 UTC timestamp에 결합하고 `reference_type=dsr_proxy`를 기록한다.
- [ ] 입력 cutoff 이후 DSR은 target에만 사용한다.
- [ ] join 전후 행 수, 제외 사유, 결측률을 기록한다.
- [ ] 임의 10개 timestamp를 수동 검증한다.

완료 조건:

- [ ] `issued_at > prediction_time`인 Open-Meteo snapshot이 없다.
- [ ] reference가 없는 행은 metric에서만 제외하며 필요하면 예측 결과에는 남긴다.
- [ ] 결합 결과와 한계를 `fr01_4_ai_kma_analysis.md`에 기록한다.

## 5. Phase 4 — real-data 실험 CLI

대상: `tools/ai_experiments/run_fr01_kma_nowcast_experiment.py`, 필요 시 `backend/app/nowcast/kma_experiment.py`.

```powershell
.\.venv\Scripts\python.exe tools\ai_experiments\run_fr01_kma_nowcast_experiment.py `
  --mode both --kma-csv data\fr01\normalized\kma_openmeteo.csv `
  --output-dir artifacts\fr01 --site-lat 37.5714 --site-lon 126.9658 `
  --timezone Asia/Seoul --seed 42
```

- [ ] `--mode synthetic|real|both`, `--kma-csv`, output·좌표·timezone·seed를 지원한다.
- [ ] 선택 옵션 `--train-end`, `--validation-end`, `--min-solar-elevation`, `--cloud-patch-enabled`를 지원한다.
- [ ] synthetic 기존 결과를 유지하고 real CSV schema를 검증한다.
- [ ] 실행 설정·Git commit·seed를 metadata에 기록하고 오류는 non-zero로 종료한다.
- [ ] `--help`, synthetic smoke, real end-to-end가 통과한다.

## 6. Phase 5 — 학습·예측 및 누수 방지

- [ ] 학습·추론이 공유하는 단일 feature 생성 함수를 만든다.
- [ ] 시간순 `train 60% / validation 20% / test 20%`와 최소 유효 표본 검사를 적용한다.
- [ ] prediction 이전 관측만 사용하는 rolling-origin 평가를 구현한다.
- [ ] baseline과 ensemble에 같은 Open-Meteo 값을 사용한다.
- [ ] quantile weight·학습 metadata 저장/재로드 결과를 검증한다.
- [ ] `timestamp > prediction_time` KMA, `issued_at > prediction_time` Open-Meteo, test target의 fitting 사용을 금지한다.
- [ ] test 결과를 본 뒤 regime 임계값을 바꾸지 않는다.

선택적인 cloud patch는 단일 지점 결과에 억지로 적용하지 않는다. 필요할 때 주변 격자 해상도·호출량을 먼저 검증하고, patch 반경·평균 운량·공간 gradient·연속 프레임 이동 벡터를 metadata와 feature에 추가한다. 미사용 시 `cloud_patch_used=false`로 기록한다.

## 7. Phase 6 — 지표와 EDA

계산 지표:

- [ ] Open-Meteo와 KMA AI q50의 RMSE, MAE, Bias, 개선율
- [ ] q10/q50/q90 Pinball Loss, q10~q90 PICP, 평균·중앙 spread
- [ ] 유효 표본 수와 결측률

주간/야간을 분리하고 전체 주간, 시간대, 태양 고도, 맑음, 흐림, 급격한 구름 이동, patch 사용 여부별 결과를 낸다. 모든 표에 `sample_count`를 포함하고 부족한 표본은 `insufficient_sample`로 표시한다.

```text
artifacts/fr01/<run-id>/
  run_metadata.json, dataset_summary.json, model_weights.json
  predictions.csv, metrics_overall.csv, metrics_by_regime.csv, metrics_by_hour.csv
  timeseries_comparison.png, residual_by_hour.png
  residual_by_cloud_variability.png, quantile_calibration.png
```

그래프에는 reference/Open-Meteo/q50, q10~q90 band, 결측 단절, 단위·timezone, `DSR proxy` 표기를 포함한다. metrics 재계산 일치와 synthetic/real run-id 분리를 검증한다.

## 8. Phase 7 — 테스트

- [x] KMA parser, 결측·CA 이상값, 24개 chunk, durable queue resume, artifact, worker
- [ ] 공식 단위 확인 후 필요한 단위 변환
- [ ] 중복 timestamp, schema, 시간 정렬, feature, regime, 지표, weight save/load
- [ ] synthetic·real CLI smoke와 기존 `tests/test_fr01_kma_ai_nowcast.py` 회귀
- [ ] 미래 KMA·proxy target·Open-Meteo 누수 탐지
- [ ] q10 ≤ q50 ≤ q90, 물리 상한, artifact 생성

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_fr01_kma_ai_nowcast.py `
  tests\test_fr01_kma_dataset.py `
  tests\test_fr01_kma_experiment.py
```

## 9. Phase 8 — 문서와 Issue

- [ ] `docs/fr01_4_kma_ai_nowcast.md`에서 기존 결과를 Synthetic Benchmark로 분리한다.
- [ ] 실제 기간·좌표·출처·단위 상태·결측률·시간 분할·rolling-origin·patch 여부를 기록한다.
- [ ] 실제 지표·EDA, DSR proxy 한계와 후속 수집 계획을 추가한다.
- [ ] Issue `#86`에 10분 API 근거, 재현 명령, artifact, acceptance criteria별 증거를 연결한다.

GitHub Issue 변경은 로컬 구현·검증 완료 후 별도 승인하에 수행한다.

## 10. 이번 스프린트 완료 정의

- [x] KMA 7일 실데이터가 raw·CSV·manifest로 재현 가능하게 보존된다.
- [x] 출처, 시간대, 간격, 단위 상태, 결측·이상값 처리 방식이 기록된다.
- [ ] `--kma-csv` real 실험이 시간순 분할·rolling-origin으로 실행된다.
- [ ] 동일 시각의 실제 Open-Meteo snapshot과 비교된다.
- [ ] RMSE, MAE, Bias, Pinball Loss, PICP와 구간별 표본 수가 생성된다.
- [ ] EDA·weights·predictions·실행 metadata가 artifact로 남는다.
- [ ] synthetic과 real 결과가 문서에서 명확히 분리된다.

성능 향상 자체는 완료 조건이 아니다. 동일 데이터·평가 조건에서 얻은 결과와 한계를 재현 가능하게 보고하는 것이 완료 조건이다.

</details>

위 계획은 이후 `kma_model_dataset.py`, horizon별 학습 도구, Stage 6 artifact loader와 평가기로
구현됐다. 초기 계획과 달라진 핵심은 고정 `60/20/20` 비율 대신 날짜 기반 split과
horizon embargo를 사용하고, PNG 그래프 대신 재현 가능한 CSV·JSON을 우선한 점이다.
