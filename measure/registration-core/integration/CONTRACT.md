# 모바일 연동 계약

API는 상태를 저장하지 않습니다. 사용자·공간·보드·가구·촬영 진행 상태는 앱 또는 서비스 DB가 관리합니다. API에 파일 경로나 카메라 3D 위치를 입력하지 않습니다.

## 엔드포인트

실제 촬영은 네이티브 앱에서만 제공합니다. Android/iOS 어댑터와 숫자 입력 없는 카메라 자동 설정은 [모바일 연동 코드](../../../mobile/README.md)를 참고하세요. 웹 사진 추가는 개발 테스트용입니다.

| 경로 | 입력 | 출력 |
|---|---|---|
| `POST /v1/plans` | 가구 종류·모델 ID·상판 모양·높이 모드 | plan ID, 지점과 순서 |
| `POST /v1/detect` | Base64 JPEG/PNG, 보드 규격, 카메라 정보 | 검출 ID·코너 픽셀 좌표, 자세 유효 상태 |
| `POST /v1/measurements` | plan 원본, 보드, 지점별 코너 관측·카메라, 바닥/ENU | 지점 좌표·형상·모델 배치·재촬영 이유 |
| `POST /v1/scenes` | 동일 공간의 완료 측정 결과 배열 | 복수 가구 Scene |
| `POST /v1/fr06` | ENU 정렬이 있는 완료 측정 결과 | `sunsync.fr06-fragment/1` |
| `POST /v1/fr06/validate` | 별도 버전의 fragment | 스키마·참조·좌표 일관성 검증 |
| `POST /v1/mobile/captures` | 네이티브 사진·촬영 시각·기기 메타데이터 | 보정된 관측/카메라 정보 또는 자동 설정 안내 |
| `POST /v1/mobile/automatic-setup` | 동일 고정 카메라의 다양한 보드 관측 12~30장 | 검증된 현재 스트림용 카메라 정보 |
| `POST /api/scenes/{scene_id}/registrations` (백엔드) | `expected_revision`, `fragment` | 가구가 추가된 전체 FR-06 Scene, DB 저장 |

전체 요청 스키마는 실행 서버 `/openapi.json`과 저장소 `openapi.json`을 참고하세요. 예제 JSON에는 실제 사용자의 사진·보정 정보가 없습니다.

## 사진을 자동 분류하는 방법

1. 앱은 `/v1/plans`의 `steps`를 순서대로 표시합니다.
2. 캡처 버튼 시점에 `begin_capture(capture_id, captured_at)`를 호출해 지점 ID와 실제 촬영 시각을 고정합니다. 시각은 UTC 오프셋을 포함한 ISO 8601 문자열입니다. 비동기 응답 때 현재 지점 ID를 다시 읽지 않습니다. `accept(capture_id, point_id, camera, detection_response)`에 티켓의 두 ID를 함께 전달합니다. 이전 지점의 늦은 응답은 거절합니다.
3. 검출에 실패하면 현재 지점에 머무르고 재촬영합니다. 성공하면 해당 가구·지점에 코너 픽셀 좌표와 카메라 정보를 함께 저장합니다.
4. 지정된 반복 수를 채우면 다음 지점으로 이동합니다. 사용자가 파일명을 변경하지 않습니다.
5. 앱은 같은 정지 위치에서 얻은 실제 서로 다른 프레임을 사용합니다. 복사한 동일 사진에 새 ID를 주는 행위는 반복성 검증이 아닙니다.
6. 측정 결과의 `required_actions`에 `recapture:P2` 등이 있으면 해당 지점만 다시 안내합니다. 참조 `CaptureSession.retake()`는 해당 지점의 결과를 대체하고 처리 중인 티켓을 모두 무효화합니다. 재촬영 뒤 같은 지점에 도착한 과거 응답도 수용하지 않습니다. 새 사진에는 새 capture ID를 사용합니다.

```python
ticket = session.begin_capture(capture_id, captured_at)  # 셔터 시점
# 같은 이미지·카메라 정보로 /v1/detect 호출 후
session.accept(ticket["capture_id"], ticket["point_id"], camera, detection_response)
```

`samples_per_point=3`은 기본값이며 고정된 서비스 요구사항은 아닙니다. 1장은 반복성 평가가 불가능해 `repeat_spread_m=null`을 반환합니다. 어느 경우도 `uncertainty_m`나 실측 오차를 0으로 만들지 않습니다.

## 픽셀과 카메라 좌표

- 코너는 **디코딩된 마커 기준** 왼쪽 위 → 오른쪽 위 → 오른쪽 아래 → 왼쪽 아래입니다. 화면에서의 위치로 재정렬하지 않습니다.
- 이미지는 EXIF 방향을 무시한 저장 픽셀로 디코딩합니다. 앱이 이미지를 회전했다면 EXIF만 수정하지 말고 실제 픽셀과 K를 함께 맞춰야 합니다.
- OpenCV 카메라 축은 오른쪽·아래·앞입니다. `camera_to_point_m`는 이 축 기준 렌즈 중심에서 접촉 지점으로 향하는 벡터입니다.
- ARKit/ARCore의 월드 pose를 이 API의 `T_board_camera`로 직접 넣지 않습니다. 여기서는 마커 PnP로 자세를 계산합니다. 향후 VIO를 연결한다면 플랫폼 카메라 축·월드 프레임의 변환이 별도로 필요합니다.
- 같은 ID가 여러 화면에 보이면 `/detect`는 중복 오류를 반환합니다. 대상 보드 이외의 복제 화면을 가리거나, 픽셀 좌표와 K를 정확히 보정한 ROI를 사용해야 합니다. 가장 큰 보드를 자동으로 정답이라고 가정하지 않습니다.

## 수신 측 해석

- `incomplete`, `invalid_surface`: 표준 모델 확정 배치에 사용하지 않습니다.
- `surface_measured`: 상판은 계산했지만 높이·바닥 기준 등의 보완이 필요할 수 있습니다.
- `model_ready_unreviewed`: 모델 배치 정보가 준비됐습니다. 사용자 확인 완료를 뜻하지 않습니다.
- `model.dimensions_m`는 모델 경계상자 크기, 각 변환은 행 우선 4×4입니다. 앱 렌더러가 y-up/z-up 또는 열 우선 행렬을 쓰면 변환해야 합니다.
- 가구 형태와 회전은 근사입니다. 표준 모델 ID와 로컬 축·원점을 카탈로그에서 일관되게 정의하세요.
- `reference_id`는 보드 치수와 정렬 정보의 해시입니다. 정렬/보드 치수 변경 후에는 기존 측정 결과를 같은 참조로 취급하지 않습니다.
- `/v1/scenes`와 fragment 출력은 검증된 행렬에서 중심·yaw를 다시 계산하고 허용된 모델 필드만 반환합니다. 클라이언트가 보낸 파생 좌표나 임의 필드는 전달하지 않습니다.
- fragment는 `POST /api/scenes/{scene_id}/registrations`에 `{"expected_revision": 현재revision, "fragment": fragment}`로 전달합니다. 전체 Scene과 다른 전송 봉투이므로 `/api/scenes`에 직접 보내지 않습니다.
- 백엔드는 `vertical`/`horizontal` 마커와 일반 3D `aruco_board_camera_at_point` 관측을 지원합니다. 실제 촬영 시각이 누락되면 내보내지 않습니다. 높이 산출 모드·수동 높이·설치 높이·모델 ID를 관측에 기록하며 `derived_fields`는 가로/깊이 `[0:2]`, 높이 `[2]`, yaw, base-center를 지정합니다.
- 등록은 직사각형 상판/직육면체에 한정합니다. 기존 방의 `room_id`와 공통 ENU 정렬이 필요하며 진북·바닥 기준을 확인해야 합니다. 서버는 점과 변환, 치수·높이·중심·yaw의 일치, 방 내부 형상·출처를 확인합니다. 성공 시 revision+1과 전체 Scene을 반환합니다. 409는 재조회 후 충돌 처리가 필요하며, 422에서는 입력을 수정해야 합니다. 실패 시 기존 Scene을 변경하지 않습니다.

## 배포 담당 확인 사항

기기에서 카메라 정보를 얻는 코드·지원 기기 목록·3D 자산은 플랫폼 결정 후 구현합니다. 서버 인증과 세션 소유권, 실제 사용자 확인 기록, 저장소와 모니터링은 상위 서비스가 담당합니다. API는 요청 내 JSON을 검증하지만 입력 카메라 정보가 실제 기기에서 왔다는 사실까지 증명하지 않습니다.
