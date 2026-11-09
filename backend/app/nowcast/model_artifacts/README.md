# FR-01 운영 모델 artifact

이 디렉터리는 FR-01.4 backend가 추론할 때 직접 읽는 **작고 동결된 운영 모델**을 보관한다.
Stage 6에서 10·30·60분 모델의 저장·검증·로드와 Validation 구간 평가를 구현했다.

## 실험 결과와의 구분

| 위치 | 역할 | Git 관리 |
|---|---|---|
| `data/fr01/model/` | 학습 dataset, 예측 CSV, 평가 결과, 기각 후보 등 실험 근거 | 제외 |
| `backend/app/nowcast/model_artifacts/` | backend가 읽는 검증된 모델 계약과 weight | 포함 |

학습 dataset, validation 예측 CSV, diagnostic 결과와 API 원본 응답은 이 디렉터리에 복사하지 않는다.

## 현재 구조

```text
model_artifacts/
├── README.md
└── fr01_kma_quantile_v1/
    ├── manifest.json
    ├── horizon_10/
    │   └── model.json
    ├── horizon_30/
    │   └── model.json
    └── horizon_60/
        └── model.json
```

- `manifest.json`: 모델 묶음의 schema·model version, 지원 horizon, 파일 경로와 무결성 hash
- `model.json`: 한 horizon의 P10·P50·P90 weight, 입력 feature 계약, 제한 범위와 후처리 규칙
- 최초 버전은 `fr01_kma_quantile_v1`로 고정하고, 동결 weight나 계약을 바꾸면 기존 파일을 덮어쓰지 않고 새 버전을 만든다.
- loader는 `backend/app/nowcast/kma_model_artifact.py`에 있으며 manifest의 SHA-256과 각 JSON 계약을 검증한다.

## 동결 원본

Stage 6 모델 파일은 다음 동결 기록에서 생성한다.

- 공통 동결 목록: `data/fr01/model/frozen_model_manifest.json`
- 10분: `data/fr01/model/quantile_stage4_train_20261005/quantile_model_summary.json`
- 30분: `data/fr01/model/stage5/horizon_30min/quantile_model_summary.json`
- 60분: `data/fr01/model/stage5/horizon_60min/quantile_model_summary.json`

60분 P90 intercept는 validation 비교 결과 `-0.5 ~ 0.5` 범위를 채택했다. Diagnostic과
2026-11-02~2026-11-05 final test는 모델 선택에 사용하지 않았다.

## `model.json` 필수 계약

각 파일에는 최소한 다음 값을 저장한다.

- artifact schema version과 model version
- horizon minutes와 모델 종류
- feature 이름·순서·단위
- P10·P50·P90 weight
- quantile별 weight bounds
- factor 물리 범위 `0.0 ~ 1.05`
- `P10 <= P50 <= P90` 후처리 규칙
- DSR 변환, Open-Meteo 정렬·공개 지연 등 전처리 계약 버전
- 학습 기간, split 경계, 입력 파일 hash와 코드 commit
- 생성 시각과 동결 상태

JSON 숫자만으로 추론하지 않는다. 구현된 loader가 schema, feature 순서,
horizon, 유한 숫자, weight 개수와 hash를 검증한 뒤 모델을 생성해야 한다.

## 생성·검증·평가 명령

```powershell
.\.venv\Scripts\python.exe tools/ai_experiments/package_fr01_kma_models.py
.\.venv\Scripts\python.exe tools/ai_experiments/evaluate_fr01_kma_models.py
.\.venv\Scripts\python.exe -m pytest tests/test_fr01_kma_model_artifact.py -q
```

- 패키징: 동결 summary 세 개를 horizon별 `model.json`과 공통 `manifest.json`으로 변환한다.
- Round-trip: 저장 전 weight 계산과 재로드 계산을 비교한다. 현재 factor와 DNI 최대 절대 오차는 모두 `0.0`이다.
- 전체 평가: Validation만 사용해 전체·한국 시간대·태양 고도·구름 구간 지표와 세 horizon 공통 78개 origin 지표를 만든다.
- 평가 결과: `data/fr01/model/stage6/validation_segment_evaluation.json`
- Diagnostic은 계산하지 않았고 2026-11-02~2026-11-05 final test도 사용하지 않았다.

구간별 결과를 해석할 때 태양 고도 45° 이상 표본은 0개이며 부분 흐림 표본은 horizon별 7~8개뿐이라는 제한을 반드시 함께 표시한다.

## Stage 6 완료 상태

- [x] 10·30·60분 `model.json`이 모두 schema와 SHA-256 검증을 통과한다.
- [x] 저장 전후 예측이 factor `1e-10`, DNI `1e-6 W/m²` 허용 오차 안에서 동일하다.
- [x] feature 순서 변경·알 수 없는 horizon·비유한 weight·손상된 hash를 loader가 거부한다.
- [x] Validation 전체·구간별 평가와 공통 표본 비교를 생성한다.
- [x] 모델 선택에 diagnostic이나 final test를 사용하지 않는다.
- 운영 API 연결은 Stage 6 검증과 팀 검토가 끝난 뒤 별도 작업으로 진행한다.
