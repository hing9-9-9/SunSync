# FR-01.4 문서 안내

## 현재 상태

2026-11-08 기준으로 KMA GK2A DSR·CA 수집, 28일 개발 데이터 구성, 10·30·60분
P10·P50·P90 선형 분위수 모델 학습, Validation 평가, 모델 동결, 운영 JSON 저장·로드와
**동결 모델의 Final test 1회 평가**까지 완료했다. V1은 결과와 관계없이 수정하지 않는다.

```text
개발 데이터       2026-10-05 ~ 2026-11-01 (학습·Validation·보고용 Diagnostic)
동결 모델         fr01_kma_quantile_v1, horizon 10·30·60분
Final test KMA    2026-11-02 ~ 2026-11-05, 576행
Final test OM     같은 기간 archived run 18개·504행
Final test 결과   1회 평가 완료, V1 재튜닝 금지
운영 API 정책     10·30분 AI, 60분 Open-Meteo, 결측 시 OM fallback
운영 API 연결     Shadow 운영과 팀 검토 후 진행
```

Final test 결과를 보고 현재 모델의 feature, weight, 전처리 또는 후처리를 바꾸지 않는다.
모델을 변경하려면 새 버전과 새로운 holdout 기간이 필요하다.

## 문서 읽는 순서

| 순서 | 문서 | 역할 | 현재성 |
|---:|---|---|---|
| 1 | [`fr01_4_ai_model_implementation.md`](fr01_4_ai_model_implementation.md) | 동결 모델의 정확한 입력·학습·분할·artifact 계약 | 현재 기준 |
| 2 | [`fr01_4_ai_kma_analysis.md`](fr01_4_ai_kma_analysis.md) | 데이터 품질과 Stage 4~6 실제 결과 | 현재 기준 |
| 3 | [`fr01_4_ai_implement_complete.md`](fr01_4_ai_implement_complete.md) | 수집기·queue·데이터 인계 요약 | 현재 기준 |
| 4 | [`fr01_4_ai_doc.md`](fr01_4_ai_doc.md) | DSR·DNI·분위수 모델을 이해하기 위한 설명 | 개념 설명 |
| 5 | [`fr01_4_ai_implement.md`](fr01_4_ai_implement.md) | 초기 계획과 의사결정 기록 | 기록용 |
| 6 | [`fr01_4_open_meteo_dni_design.md`](fr01_4_open_meteo_dni_design.md) | 기존 Open-Meteo worker와 FR-01 타일 DNI 설계 | 별도 운영 흐름 |
| 7 | [`fr01_4_kma_ai_nowcast.md`](fr01_4_kma_ai_nowcast.md) | 합성 benchmark·patch·API에 대한 초기 제안 | 레거시 제안 |

문서가 충돌하면 위 표의 “현재 기준” 문서와
`backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1/manifest.json`을 우선한다.

## 구현 산출물

```text
수집·정규화       backend/app/nowcast/kma_adapter.py
durable queue     backend/app/nowcast/kma_collection_queue.py
데이터 계약       backend/app/nowcast/kma_model_dataset.py
선형 분위수 모델  backend/app/nowcast/kma_linear_model.py
artifact loader   backend/app/nowcast/kma_model_artifact.py
Final 입력 검사   backend/app/nowcast/kma_final_test.py
Final 준비 명령   tools/ai_experiments/prepare_fr01_final_test.py
Final 평가 명령   tools/ai_experiments/evaluate_fr01_kma_final_test.py
운영 모델         backend/app/nowcast/model_artifacts/fr01_kma_quantile_v1/
개발 평가 결과    data/fr01/model/stage6/validation_segment_evaluation.json
```

## Final test 진행 상태

1. [x] `2026-11-02~2026-11-05` Open-Meteo archived run을 개발 때와 같은 규칙으로 준비했다.
2. [x] 성능을 보지 않고 timestamp, run, `issued_at + 7시간`, hash와 행 완전성을 검사했다.
3. [x] 학습 기능이 없는 final-test 전용 평가기를 작성하고 fixture로 검증했다.
4. [x] 동결 artifact와 입력 hash를 다시 확인하고 10·30·60분을 한 번 평가했다.
5. [x] 모델과 Open-Meteo를 horizon별 동일 표본에서 비교하고 결과를 문서화했다.
6. [x] 결과와 관계없이 V1 모델을 수정하지 않고 평가 완료 상태로 닫았다.

입력 준비 결과는
`data/fr01/archive/frozen_final_test/final_test_20261102_20261105/input_readiness_manifest.json`에
고정했다. 1회 평가 결과는 `data/fr01/model/final_test_20261102_20261105/`에 있으며
상태는 `completed_no_retuning_allowed`다.

## 운영 API 확정 정책

- 10·30분은 동결 V1의 P10·P50·P90을 정식 제공한다.
- 60분은 Open-Meteo를 기본 제공하고 V1 결과는 내부 Shadow 기록으로만 남긴다.
- KMA DSR·CA 결측 또는 관측 나이 20분 초과 시 Open-Meteo로 fallback한다.
- Open-Meteo도 사용할 수 없으면 임의 보간하지 않고 `unavailable`을 반환한다.
- 외부 자료는 worker가 미리 수집하며 사용자 요청 중 외부 API를 직접 호출하지 않는다.
- 운영 연결 전 Shadow 운영과 팀 검토를 거친다.

상세 응답 필드와 배포 조건은 `fr01_4_ai_model_implementation.md` 15장을 기준으로 한다.
