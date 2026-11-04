# Sunsync 가구 등록 공통 모듈

Android·iOS 중 어느 쪽에서도 사용할 수 있도록 분리한 Python 측정 코어와 HTTP API입니다. 앱은 촬영 화면과 카메라 정보 수집을 담당하고, 이 모듈은 ArUco 보드 인식, 지점별 좌표 계산, 표준 모델 배치, ENU 변환을 담당합니다.

**상태: 서비스 연동용 초기 버전입니다.** 서버 API·단위 테스트·합성 예제는 실행 가능하지만, Android/iOS 어댑터와 실제 사용자 화면, DB, 3D 모델 자산은 포함하지 않습니다. 기존 실측 실험의 수평 이동 오차는 약 10.6%였으며, 이 코드가 그 오차를 해결했다고 주장하지 않습니다. 합성 테스트 통과는 실제 정확도 검증과 다릅니다.

## 설치와 실행

Python 3.11 이상에서 이 폴더를 독립 저장소로 사용하세요. 기존 `sunsync_poc` 패키지나 실험 사진은 필요 없습니다.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[api,test]"
.venv\Scripts\python -m uvicorn sunsync_registration.api:app --host 127.0.0.1 --port 8001
```

API 문서: `http://127.0.0.1:8001/docs`. 기존 백엔드의 8000 포트와 분리해 실행합니다.

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python examples/build_synthetic_example.py
```

예제는 실제 사진 대신 알려진 카메라·보드 좌표를 투영한 합성 데이터입니다. 가로 0.60m, 세로 0.45m, 높이 0.75m 모델을 생성합니다. `examples/measurement-request.json`을 `/v1/measurements`에 POST하면 실행을 확인할 수 있습니다. 검증 당시 버전은 `requirements-tested.txt`에 있습니다.

## 체커보드가 없어지는 범위

서비스 API에는 체커보드 촬영·등록 단계가 없습니다. **카메라 정보 자체가 불필요해진다는 뜻은 아닙니다.** 앱이 각 촬영 프레임과 일치하는 내부 파라미터를 반드시 제공해야 합니다.

| 앱이 전달할 정보 | 의미 |
|---|---|
| `width`, `height` | 처리하는 이미지의 실제 픽셀 크기 |
| `fx`, `fy`, `cx`, `cy` | 같은 이미지 좌표계의 초점거리·주점, 단위 px |
| `pixel_space` | `raw_opencv` 또는 `rectified` |
| `distortion` | raw일 때 OpenCV 왜곡 계수, rectified일 때 빈 배열 |
| `source`, `lens_id` | 정보 출처와 렌즈 식별자 |

- Android: ARCore `Camera.getImageIntrinsics()`의 CPU 이미지 기준 정보를 사용할 수 있습니다. GPU texture용 K와 CPU 이미지를 섞으면 안 됩니다.
- iOS: ARKit `ARCamera.intrinsics`와 대응 이미지 크기·픽셀 버퍼를 연결하는 어댑터가 필요합니다.
- 이 API는 EXIF 회전을 적용하지 않습니다. 업로드 파일의 저장된 픽셀 방향과 검출 좌표·K를 맞추세요. 회전·리사이즈·크롭 후에는 K도 변환해야 합니다.
- ARCore/ARKit에서 K를 얻었다는 이유만으로 입력 사진의 왜곡이 0이라고 가정하지 않습니다. 플랫폼 어댑터가 해당 스트림의 왜곡/보정 상태를 확인해야 합니다. `rectified`는 실제로 보정된 이미지에만 사용하세요.
- 정보를 확보할 수 없는 기기·일반 JPEG만 있는 경우에는 별도 보정 프로필 또는 개발 단계의 체커보드 보정이 여전히 필요합니다. 기존 iPhone 보정값을 다른 기기에 복사하지 마세요.

## 서비스 처리 흐름

1. 공간 ID, 고정 보드 ID·실측 치수·배치, 바닥 기준과 필요 시 ENU 정렬을 등록합니다.
2. 가구 종류·표준 모델 ID와 상판 모양을 선택합니다. 다각형은 직선으로 연결한 닫힌 도형의 꼭짓점을 입력합니다. 첫 꼭짓점을 마지막에 중복 입력하지 않습니다.
3. `/v1/plans`가 `P1..Pn` 촬영 지점, 순서, 반복 촬영 수를 반환합니다. 안내 좌표는 모양 설명용이며 실제 크기 입력이 아닙니다.
4. 앱이 현재 지점에 렌즈를 맞추도록 안내하고 `/v1/detect`로 사진 한 장의 보드 검출을 요청합니다.
5. 앱은 셔터 시점에 `CaptureSession.begin_capture(capture_id)`로 지점 ID를 고정합니다. 검출 응답이 오면 티켓의 `capture_id`·`point_id`로 `accept()`를 호출합니다. 늦게 도착한 이전 지점 응답이나 재촬영 이전 응답은 거절합니다.
6. 모든 지점 촬영 후 `/v1/measurements`에 지점별 검출 코너와 각 프레임의 카메라 정보를 제출합니다. 서버는 자세를 다시 계산하며 클라이언트가 보낸 3D 좌표를 그대로 신뢰하지 않습니다.
7. 앱이 표준 모델을 `dimensions_m`에 맞춰 조정하고 `T_board_model` 또는 `T_enu_model`로 배치합니다. 가구 앞 방향과 결과를 사용자가 확인합니다.
8. 같은 공간의 여러 결과를 `/v1/scenes`로 모읍니다. 다른 보드를 썼다면 공통 공간의 ENU 정렬이 필요합니다.

## 구현한 범위

| 기능 | 동작 |
|---|---|
| 보드 | `DICT_4X4_50`의 2×2, ID·개별 한 변·가로세로 간격 입력, 2개 이상 마커로 자세 추정 |
| 모양 | 직사각형 4점, 원 4점, 타원 6점, 단순 다각형 3~16점(오목 포함) |
| 품질 확인 | 평면 자세 모호성, 재투영 오차, 지점별 반복 편차, 평면·형상 잔차, 바닥 기준 수평성 |
| 높이 | 상판이 최고점, 최고점 추가 촬영 `H1`, 사용자 실측 입력 |
| 표준 모델 | 모델 ID, 크기, 바닥 배치점, 회전 행렬 반환. 모델 파일·렌더러는 앱에서 제공 |
| 공간 | 같은 보드 좌표 또는 공통 ENU에서 여러 가구를 조합 |
| 출력 | 측정 JSON, Scene JSON, 별도 계약 `sunsync.fr06-fragment/1` |

여기서 카메라 광학 중심이 지정 지점의 좌표를 대신합니다. 카메라 축 기준 렌즈→접촉점 벡터 `camera_to_point_m`를 알면 보정할 수 있습니다. 모양에 맞게 무조건 강제 변형하거나 실패 임계값을 자동 완화하지 않습니다.

## 높이와 ENU

보드 원점은 **왼쪽 위 마커의 검정 외곽 왼쪽 위**입니다. x는 오른쪽, y는 아래쪽, z는 보드 안쪽입니다. 기존 단일 마커 중심 좌표와 다릅니다.

- 수직·정방향 보드의 바닥 높이만 아는 경우: `floor={"board_origin_height_m":실측값,"up_board":[0,-1,0]}`를 전달합니다. 이 높이는 마커 중심 높이가 아니라 보드 원점 높이입니다.
- 바닥 기준 지점 높이 = `board_origin_height_m + dot(point_board_m, up_board)`.
- `surface_is_top`: 바닥에 놓인 책상 등의 상판 높이를 전체 높이로 사용합니다.
- `capture_highest`: 의자 좌판 등과 별도로 최고점 `H1`을 촬영합니다.
- `manual`: 실측 전체 높이를 입력합니다. 떠 있는 가구는 `floor_supported=false`와 설치 바닥 높이 `manual_base_height_m`도 필요합니다.
- ENU는 동·북·위입니다. 행 우선 16개인 `T_enu_board`를 입력하면 `p_enu = R @ p_board + t`로 변환합니다. 진북·공간 원점·바닥 기준이 확인되어야 하며, 마커 높이만으로 ENU를 생성하지 않습니다.
- `space_id` 하나는 하나의 ENU 원점·북쪽·바닥 기준을 의미합니다. 다른 원점을 같은 ID로 등록하지 않도록 앱/DB에서 관리해야 합니다.

## 전달 데이터와 3D 모델 규약

`model`은 `model_id`, `dimensions_m=[가로,깊이,높이]`, `T_board_model`, ENU가 있으면 `T_enu_model`, `yaw_deg_ccw_from_east`를 포함합니다. 모델의 로컬 원점은 바닥 경계상자 중심, x=가로·y=깊이·z=위입니다. 정규화 모델의 원래 크기를 알고 비율을 적용하거나 1m 기준 모델을 제공하세요. 보드/가구의 yaw를 진북 방위각과 혼동하지 마세요.

원형에는 형상만으로 앞 방향이 없고 타원에는 180도 방향 모호성이 있습니다. 표준 모델의 앞 방향은 사용자 검수 대상입니다. 복잡한 실제 가구 형상을 자동 복원하거나 책상 다리를 측정한 결과가 아닙니다.

`/v1/fr06`은 **기존 백엔드 FR-06 0.2.0과 다른 계약**인 `sunsync.fr06-fragment/1`을 반환합니다. 현재 백엔드 `/api/scenes`에 직접 제출할 수 없습니다. 기존 백엔드는 바닥 평면 `aruco_planar` 관측을, 이 계약은 수직/수평 보드에 대한 일반 3D `aruco_board_camera_at_point` 관측을 표현합니다. `plane`은 측정점이 아니라 기준 마커의 방향입니다. 알 수 없는 촬영 시각은 `null`로 유지하며 처리 시각을 대신 넣지 않습니다.

명세는 `fr06-fragment.schema.json`, 실행 검증기는 `fragment_schema.BoardCameraFragmentV1`입니다. `POST /v1/fr06/validate`는 스키마·ID 참조·좌표 변환·derived field 경로를 검증합니다. 내보낸 객체 ID는 기존 식별자 문자 규칙을 따르며, 변환한 원래 ID는 `extensions.id_mapping`에 보존합니다. 보드 좌표는 실제 기준 마커 중심 좌표로 변환하고 위치·회전 파생값은 행렬에서 다시 계산합니다.

이 fragment를 기존 Scene에 연결하려면 수신 측에 명시적 버전 어댑터가 추가로 필요합니다. 가구는 표준 모델의 불투명 직육면체 경계상자이므로 다리 사이 빈 공간까지 채우는 근사치입니다. 전체 Scene·창문·기상·광학 정보를 만들어 넣지 않습니다. 기존 백엔드 스키마는 변경하지 않았습니다.

## 팀에서 추가할 부분

- Android/iOS 촬영 어댑터 및 기기별 카메라 파라미터 검증
- 단계별 UI, 상판 도형 편집기, 촬영 세션 저장·복구·동시 요청 처리
- 실제 3D 모델 자산·카탈로그·렌더러와 사용자 확인/수정 기록
- ENU 기준 등록, 북쪽 정확도와 바닥 측량 검증
- 실제 거리별·기종별 오차 검증과 서비스 합격 기준
- 배포 환경의 인증·권한·TLS·요청 빈도·DB 관리. 예제 서버는 로컬 통합 테스트용이며 공개 주소로 바로 노출하지 않습니다. 요청 본문은 16 MB로 제한하며 이미지를 저장하지 않습니다.

## 파일 구성

```text
sunsync_registration/
  models.py    입력 규격과 좌표 검증
  pose.py      사진 검출과 다중 마커 PnP
  geometry.py  평면 모양별 형상 계산
  core.py      촬영 계획·가구 측정·공간 조합
  session.py   단계별 자동 분류 참조 상태 머신
  fr06.py      별도 버전의 보드 측정 fragment 출력
  fragment_schema.py  fragment 스키마와 기하·참조 검증
  api.py       HTTP 연동
examples/      합성 입력·출력과 실행 예제
tests/         기하·실패 조건·HTTP 통합 테스트
integration/   모바일 연동 계약
```

## 카메라 정보 공식 문서

- [ARCore Camera](https://developers.google.com/ar/reference/java/com/google/ar/core/Camera)
- [ARCore CameraIntrinsics](https://developers.google.com/ar/reference/java/com/google/ar/core/CameraIntrinsics)
- [ARKit ARCamera intrinsics](https://developer.apple.com/documentation/arkit/arcamera/intrinsics)
- [OpenCV solvePnP](https://docs.opencv.org/4.x/d5/d1f/calib3d_solvePnP.html)

GitHub에는 이 폴더의 코드·예제·테스트만 올리면 됩니다. 개인 실험 사진, 기존 카메라 보정 파일, `.venv`, 자격 증명은 포함하지 않았습니다.
