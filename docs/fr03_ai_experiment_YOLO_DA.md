# FR-03/FR-06 사진 1장 기반 가구 자동 인식: 구현 및 현장 실험

## 개발 목적

기존 ArUco 방식은 가구의 각 측정 지점에 카메라를 놓고 창문 마커를 향해 여러 번 촬영하여 3D 좌표를 계산한다.

신규 방식은 가구 전체와 창문 ArUco 마커가 동시에 보이는 사진 1장으로 YOLOv8 객체 검출 → Depth Anything V2 상대 깊이 추정 → ArUco 기준 실제 거리 보정 → ENU 3D 가구 생성까지 자동화하는 것이다.

최종 목표는 신규 방식의 위치·치수 정확도를 기존 ArUco 다점 측정 및 실측값과 비교하는 것이다.

## 구현 요구사항

기존 사진 API에서 실제 YOLOv8·Depth Anything V2 어댑터를 사용하도록 연결한다. 기존 수동 ArUco 측정 경로는 변경하지 않는다.

현장 실험 모드에서는 실제 모델 사용을 필수로 하고, 실패 시 synthetic fallback 없이 원인을 반환한다. 일반 모드의 기존 fallback은 유지한다.

### 실험용 진단 출력

- 모델명·가중치 버전·실제 모델 사용 여부·fallback 원인

- 2D 검출 박스·클래스·confidence

- 상대 깊이·보정 계수(alpha/beta)·보정 거리

- ArUco 카메라 자세·재투영 오차·카메라 내부 파라미터

- 3D 중심·너비·깊이·높이·yaw

- class prior 적용 여부 및 clamp 전후 값

실험용 출력은 기존 API 응답 계약과 Scene 저장 구조를 최대한 유지하면서 선택적으로 제공한다.

apply_to_scene=false로 사진을 분석하고 JSON 결과를 저장할 수 있는 실험 절차를 제공한다.

기존 ArUco 다점 측정값·줄자 실측값과 자동 인식 결과를 비교할 CSV 양식 및 위치·치수·방향 오차 계산 스크립트를 추가한다.

실제 모델 선택·실패·fallback·진단 출력·dry-run에 대한 테스트를 추가하고 기존 테스트도 실행한다.

## 권장 구현 및 현장 실험 결정

이 작업의 정확도 평가는 자동화 자체가 아니라, 현장에서 사람이 측정한 실제 방·가구 치수를 기준으로 한다. 코드는 사진 분석 결과와 실측값의 비교·기록을 돕되, 실측을 대체하지 않는다.

### 1. 요청별 실험 모드

- 사진 API 요청에 명시적인 실험 모드(예: `mode=experiment`)를 둔다.
- 실험 모드에서는 YOLOv8과 Depth Anything V2가 모두 실제로 실행되어야 하며, 패키지·가중치 로드·추론 중 하나라도 실패하면 fallback하지 않고 원인과 함께 실패를 반환한다.
- 일반 모드에서는 현재의 fallback 정책을 유지한다.

### 2. 진단 결과 형식

- 기존 응답 및 Scene 저장 구조는 유지하고, 응답에 선택적 `diagnostics` 객체를 추가한다.
- `diagnostics`에는 모델/가중치/라이브러리 버전, device, 실제 모델 사용 여부, fallback 원인, 추론 시간을 기록한다.
- ArUco 진단에는 마커 코너 픽셀, PnP 방식, 평균·최대 재투영 오차, 이미지 해상도와 실제 사용한 카메라 내부 파라미터를 기록한다.
- 깊이 진단에는 상대 깊이 범위, alpha/beta, 보정 잔차, scale-only 보정 여부와 보정 실패·불안정 사유를 기록한다.
- YOLO 검출 박스와 Depth Anything 깊이 맵을 연결할 때 가구별 대표 깊이를 추출한 픽셀 또는 영역, 통계값(예: 중앙값)과 유효 픽셀 수를 기록한다. 배경 픽셀 혼입 가능성과 그에 따른 거리 추정 오차를 현장 실험에서 평가할 수 있어야 한다.
- 가구마다 2D 박스·원 클래스·매핑 클래스·confidence, clamp 전후 중심/치수, 폭·깊이·높이별 산출 출처(`measured`, `prior`, `clamped`)와 prior 값을 기록한다.

### 3. 현장 실측 비교 절차

- 기본 호출은 `apply_to_scene=false`로 한다. 현장에서 촬영한 원본 사진의 식별자, API 응답 JSON, 줄자 실측표를 같은 실험 ID로 보관한다.
- 실측표에는 방 기준 원점, X/Y 방향, yaw 0도 기준, 가구 바닥 중심, 폭·깊이·높이와 측정 규칙(돌출부 포함 여부)을 명시한다.
- 비교 CSV와 스크립트는 중심 위치 오차(cm), 폭/깊이/높이 오차(cm 및 %), yaw 절대 오차(도), 검출/3D 생성 성공 여부와 clamp 여부를 계산한다.
- 결과 해석에서는 ArUco 재투영 오차(카메라 자세의 영상 내 일관성), depth 보정 잔차(상대 깊이와 마커 기준 깊이의 일치도), 최종 가구 거리의 실측 오차를 분리한다. 앞의 두 진단값이 작더라도 최종 거리 오차가 작다고 가정하지 않는다.
- 동일 가구를 거리·촬영 각도·조도·가림·방 경계 인접 여부별로 반복 촬영한다. 가구 종류별 위치/치수/yaw 허용오차와 최소 반복 수는 촬영 전에 정한다.
- 정확도 기준을 통과한 사례만 `apply_to_scene=true`로 다시 호출해 Scene 등록 및 UI Space 동기화를 확인한다.

### 4. 모델과 데이터의 한계 기록

- YOLO의 지원 클래스 및 내부 가구 타입 매핑을 결과에 남긴다. 지원하지 않는 가구는 `other`로 분리해 평가하며, 이번 범위에서는 모델 재학습을 하지 않는다.
- 실험 레코드에는 YOLO 가중치 파일의 SHA-256, Depth Anything 모델 revision, `ultralytics`/`torch`/`transformers`/OpenCV 버전을 남겨 재현성을 확보한다.
- 모델 의존성·가중치 설치와 메모리/추론 시간 스모크 테스트는 코드 변경과 별도로 현장 실험 전에 완료한다.

### 완료 및 성공 기준

- 이번 개발의 완료 기준은 실제 AI 모델 연결과 진단 가능한 실험 환경 구축이다.
- 현장 실험의 성공 기준은 사진 한 장 기반 자동 인식 결과가 기존 ArUco 다점 측정 및 줄자 실측값에 근접한 정확도를 보이는지 확인하는 것이다. 허용오차는 촬영 전에 가구 종류별로 정한다.

## 구현 단계 및 완료 조건(AC)

### Step 1. 실험 모드와 모델 실행 정책

대상: `backend/app/config.py`, `backend/app/api/vision_ai.py`, `engine/vision_ai/protocols.py`, `engine/vision_ai/pipeline.py`

- 요청에서 `mode=experiment`를 선택할 수 있게 한다. 기존 요청은 일반 모드로 유지한다.
- 일반 모드는 실제 모델이 사용 가능하면 사용하고, 불가능하거나 추론에 실패하면 기존 synthetic fallback을 유지한다.
- 실험 모드는 YOLOv8 및 Depth Anything V2를 모두 실제로 실행해야 한다. 의존성·가중치·모델 로드·추론 중 하나라도 실패하면 fallback하지 않고 원인을 포함해 실패를 반환한다.
- 모델 선택 정책은 API가 `VisionRegistrationPipeline`을 생성할 때 전달한다. 기존 수동 ArUco 다점 측정 경로는 변경하지 않는다.

**AC**

- [x] 일반 모드의 기존 사진 API 테스트가 계속 통과한다.
- [x] 실험 모드에서 YOLO와 Depth Anything이 실제 어댑터로 선택된다.
- [x] 실험 모드에서 둘 중 하나가 없거나 실패하면 synthetic 결과를 만들지 않고 명시적 오류를 반환한다.
- [x] 일반 모드에서는 모델 미설치·실패 시 기존 fallback 결과를 반환한다.

### Step 2. 진단 데이터 모델과 API 응답

대상: `backend/app/schemas/vision_ai.py`, `engine/vision_ai/pipeline.py`, `engine/vision_ai/protocols.py`, `backend/app/api/vision_ai.py`

- 기존 응답 필드는 호환성을 위해 유지하고, 선택적 `diagnostics` 객체를 추가한다.
- 전체 진단에는 요청 모드, 실제/fallback 모델 출처, 모델명·가중치 식별자·라이브러리 버전·device·추론 시간, 이미지 해상도와 사용 카메라 내부 파라미터를 담는다.
- ArUco 진단에는 marker corner pixel, 사용한 PnP 방식, 평균/최대 재투영 오차를 담는다.
- depth 진단에는 마커 기반 상대 깊이 범위, alpha/beta, 보정 잔차, scale-only 여부와 실패·불안정 사유를 담는다.
- 가구별 진단에는 2D 박스, 원 클래스/매핑 클래스, confidence, 대표 깊이 영역과 통계값, 유효 픽셀 수, prior 적용 여부, clamp 전후 중심·치수를 담는다.

**AC**

- [x] 기존 클라이언트가 `diagnostics` 없이도 기존 응답을 정상 처리한다.
- [x] `apply_to_scene=false` 응답에 모델·ArUco·depth·가구별 진단이 포함된다.
- [x] depth 대표값의 추출 영역·통계값·유효 픽셀 수가 가구별로 기록된다.
- [x] clamp 또는 prior가 적용된 경우 적용 전후값과 사유를 응답에서 확인할 수 있다.

### Step 3. 3D 추정 진단 연결

대상: `engine/vision_ai/pipeline.py`, `engine/vision_ai/protocols.py`

- YOLO의 검출 결과와 Depth Anything의 깊이 맵이 동일한 검출 박스를 기준으로 연결되도록 한다.
- 대표 깊이 추출은 현 구현의 "검출 박스 가장자리 20%를 제외한 내부 영역의 중앙값"을 초기 규칙으로 사용하고, 이 규칙을 진단에 명시한다.
- 배경 픽셀 혼입, 빈 깊이 영역, 불안정한 ArUco 보정은 진단 사유로 남긴다.
- 현재 class prior 및 방 경계 clamp의 동작은 유지하되, 결과 출처를 기록한다.

**AC**

- [x] 가구별 결과에서 `measured`/`prior`/`clamped` 출처를 확인할 수 있다.
- [x] depth patch가 비었거나 유효하지 않을 때 사용한 대체값과 사유를 확인할 수 있다.
- [x] 기존 2D→3D 추정·보호영역 생성·방 경계 clamp 테스트가 통과한다.

### Step 4. 실측 비교 도구와 실험 레코드

대상: `tools/ai_experiments/`, `docs/`, 필요 시 `tests/`

- 현장 원본 사진 식별자, API dry-run JSON, 줄자 실측값을 같은 `experiment_id`로 연결하는 CSV 양식을 제공한다.
- 비교 스크립트는 중심 위치 오차(cm), 폭·깊이·높이 오차(cm/%), yaw 절대 오차(°), 검출/3D 생성 성공 여부, clamp 여부를 계산한다.
- 보고서는 ArUco 재투영 오차, depth 보정 잔차, 최종 실측 거리 오차를 별도 열로 기록한다.
- 실험 기록에는 모델/가중치 식별자와 버전, 촬영 조건(거리·각도·조도·가림)을 포함한다.

#### 현장 비교 도구 사용

- 실측값은 [`docs/examples/fr03_field_measurements_template.csv`](examples/fr03_field_measurements_template.csv)를 복사해 작성한다. 사진을 보관할 수 없으면 파일 경로 대신 재현 가능한 `photo_id`(예: SHA-256)를 기록한다.
- 각 행의 `api_response_json`에는 같은 `experiment_id`의 `apply_to_scene=false` API 응답 JSON 경로를 적고, 한 사진에 가구가 여러 개면 `item_index`를 지정한다.
- 실행 명령:

  ```powershell
  python tools/ai_experiments/compare_fr03_field_measurements.py `
    --measurements-csv path/to/measurements.csv `
    --output-csv docs/reports/fr03_field_comparison.csv `
    --summary-json docs/reports/fr03_field_comparison_summary.json
  ```

- 결과 CSV는 위치·폭/깊이/높이·yaw·최종 거리 오차와 ArUco 재투영 오차, depth 보정 잔차를 별도 열로 제공한다.

**AC**

- [x] 예시 실측 CSV와 dry-run JSON으로 비교 스크립트가 실행된다.
- [x] 결과 CSV 또는 JSON에서 위치·치수·yaw 오차와 세 가지 진단 지표를 분리해 확인할 수 있다.
- [x] 사진을 저장하지 못하는 환경에서도 사진 식별자와 결과 JSON만으로 실험 결과를 추적할 수 있다.

### Step 5. 자동화 검증 및 현장 준비

대상: `tests/test_fr03_vision_ai.py`, 필요 시 모델 어댑터 테스트 및 문서

- 실제 모델 선택/실패, 일반 모드 fallback, 진단 출력, dry-run 비저장, Scene 등록을 테스트한다.
- 실제 모델 테스트는 외부 다운로드에 의존하지 않도록 어댑터를 대체한 단위/API 테스트로 고정한다.
- 현장 전에는 별도 환경에서 의존성·가중치·실제 모델 로드·사진 한 장 dry-run을 스모크 테스트한다.

#### 현장 스모크 테스트 체크리스트

1. Python 3.11+ 가상환경에서 기본 의존성을 설치한 뒤, `pip install -r requirements-vision.txt`를 실행한다. CPU 환경이면 먼저 `torch` CPU wheel을 설치한다.
2. YOLO 가중치 파일을 서비스가 읽을 수 있는 위치에 두고 `SUNSYNC_YOLO_WEIGHTS`에 절대 경로를 설정한다. 가중치 SHA-256과 `ultralytics`, `torch`, `transformers` 버전을 실험 CSV에 기록한다.
3. Depth Anything 다운로드 캐시 경로는 쓰기 가능한 `HF_HOME`으로 지정한다. 첫 실행은 모델을 내려받고 로드하므로 네트워크·디스크·메모리를 사전에 확인한다.
4. API를 실행한다. 로컬 개발 환경에서는 `cd backend` 후 `uvicorn app.main:app --reload`를 사용한다.
5. 창문 ArUco와 가구가 함께 나온 사진 한 장을 먼저 dry-run으로 보낸다. `mode=experiment`와 `apply_to_scene=false`를 반드시 함께 쓴다.

   ```powershell
   curl.exe -X POST "http://localhost:8000/api/scenes/{scene_id}/vision-furniture-photo?mode=experiment&apply_to_scene=false&code_id=0&side_length_m=0.6" `
     -H "Content-Type: image/jpeg" `
     --data-binary "@photo.jpg" `
     -o dry_run_response.json
   ```

6. 응답에서 `diagnostics.model.detector_source=yolov8`, `diagnostics.model.depth_source=depth_anything_v2`, 두 adapter 선택값이 `true`인지 확인한다. 503 또는 synthetic 출처면 측정을 진행하지 않고 의존성·가중치·로그를 점검한다.
7. `photo_id`, dry-run JSON, 줄자 실측값을 Step 4 CSV로 연결해 비교한다. 촬영 전 정한 정확도 기준을 통과한 사례만 같은 사진으로 `apply_to_scene=true`를 재요청해 Scene 등록을 확인한다.

**AC**

- [x] 기존 `tests/test_fr03_vision_ai.py`와 추가 테스트가 모두 통과한다.
- [x] 테스트는 실제 모델 미설치 환경에서도 안정적으로 실행된다.
- [ ] 현장 장비에서 `mode=experiment`, `apply_to_scene=false` 요청이 실제 모델 출처를 포함해 성공한다.
- [ ] 정확도 기준을 통과한 사례만 `apply_to_scene=true`로 Scene 등록을 확인한다.

### 진행률 산정

각 Step은 해당 Step의 모든 AC가 충족되어야 완료로 표시한다. 전체 진행률은 완료한 Step 수 기준으로 계산한다.

| 단계 | 상태 | 가중치 |
| --- | --- | --- |
| Step 1. 모델 실행 정책 | 완료 | 20% |
| Step 2. 진단 응답 | 완료 | 25% |
| Step 3. 3D 추정 진단 | 완료 | 20% |
| Step 4. 실측 비교 도구 | 완료 | 20% |
| Step 5. 검증·현장 준비 | 현장 검증 대기 | 15% |

## 제약

의존성 설치·배포 설정은 수정하지 않는다.

기존 FR-03 수동 측정 기능과 Scene/API 호환성을 유지한다.

새로운 UI는 구현하지 않는다.

모델을 새로 학습하지 않는다.

Depth Anything의 상대 깊이를 실제 거리로 보정하는 기존 수식은 우선 유지하되, 보정 실패·불안정성을 진단할 수 있도록 한다.

코드 수정 후 변경 파일, 테스트 결과, 남은 한계, 실제 현장 실험 실행 순서를 보고한다.

현재 코드와 설계 문서를 먼저 대조한 뒤 구현까지 진행해줘.
