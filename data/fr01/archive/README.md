# FR-01 archive

이 폴더는 삭제하지 않고 보존한 수집 원본, 중간 산출물, 이전 실험 결과를 모아 둔다. 현재 모델 입력과 동결된 Stage 4 모델은 각각 `../datasets`와 `../model`에 있다.

## 구성

- `source_and_intermediate/raw`: KMA·Open-Meteo API 원본 응답
- `source_and_intermediate/manifests`: 수집 queue와 collection manifest
- `source_and_intermediate/normalized`: 확정 dataset을 만들기 전 공급자별 변환 CSV
- `previous_work/legacy_pre_20261005_extension`: 학습 기간 확장 전 CSV와 모델 artifact
- `previous_work/discarded`: 초기 진단·폐기 데이터
- `frozen_final_test/final_test_20261102_20261105`: 학습에 사용하지 않은 동결 final test

Final test는 동결 모델의 최종 평가 전까지 학습, feature 선택, 전처리 선택 또는 tuning에 사용하지 않는다.
