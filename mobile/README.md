# 모바일 앱 촬영 연동

실서비스의 촬영·사진 분석은 네이티브 앱에서 시작합니다. 사용자는 카메라 내부 파라미터나 왜곡 보정값을 입력하지 않습니다. 현재 저장소에는 완성된 Android/iOS 앱이 없으므로 이 디렉터리는 앱에 연결할 카메라 어댑터와 공통 클라이언트를 제공합니다.

## 구성

- `android/`: Android 9(API 28)+ Camera2 라이브러리. 같은 촬영의 JPEG·메타데이터를 센서 시각으로 대조하고, 실제 내부 파라미터·왜곡 계수·출력 크기·크롭 정보를 읽습니다.
- `ios/NativeMeasurementCapture.swift`: iOS 17+ AVFoundation 어댑터. 동일 AVCapturePhoto의 이미지·내부 행렬·렌즈 왜곡 보정표를 읽습니다. 이미지는 회전 없이 전체 화각을 유지해 최대 1920px로 축소합니다.
- `MeasurementClient.ts`: 네이티브 앱의 네트워크/상태 연결 예시. 기기 보정 정보가 없으면 자동 설정을 진행하고, 준비된 촬영 결과를 기존 `/v1/measurements`의 `captures` 형식으로 반환합니다. TypeScript 호스트가 아니면 동일 API 흐름을 Kotlin/Swift로 연결하세요.

웹의 `mode=app` 쿼리나 PWA 설치는 네이티브 카메라 연동을 의미하지 않습니다. 웹에 이 SDK를 연결하거나 카메라 숫자 입력 폼을 추가하지 않습니다.

## 앱 연결 순서

1. 네이티브 앱에서 카메라 권한을 요청하고 후면 카메라를 엽니다. 보드 규격은 서비스의 실제 설치 보드 설정을 사용합니다. 카메라 정보와 보드 크기는 서로 다른 정보입니다.
2. Android는 `recommendedJpegSize()`로 ImageReader 크기를 선택합니다. 미리보기 초점이 안정되면 `configure()`를 실제 촬영 요청에 적용합니다. 초점·줌·손떨림 보정을 고정합니다. JPEG_ORIENTATION은 0입니다. JPEG Image와 TotalCaptureResult는 SENSOR_TIMESTAMP로 연결합니다.
3. iOS는 카메라 세션을 구성한 뒤 `lockMeasurementConfiguration()`을 호출하고, `settings()`로 만든 설정을 AVCapturePhotoOutput에 전달합니다. 촬영 delegate의 uniqueID별로 지점 ID와 `willCapturePhotoFor` 시점의 시간을 보관합니다. `didFinishProcessingPhoto`에서 해당 사진을 `payload()`에 전달합니다. NSCameraUsageDescription과 앱의 카메라 권한 처리는 호스트 앱이 제공합니다.
4. 셔터 시점에 capture ID·point ID·실제 촬영 시각을 고정합니다. Android `onCaptureStarted` 또는 iOS `willCapturePhotoFor`에서 저장한 시각을 사용합니다. 업로드 완료 시각을 넣지 않습니다.
5. `payload()` 결과를 `MeasurementClient.capture()` 또는 `POST /v1/mobile/captures`로 보냅니다. 주소는 측정 서버의 HTTPS 주소이며 기존 웹/DB 서버와 포트를 혼동하지 않습니다. 인증 헤더와 세션 소유권은 서비스의 인증 계층에서 붙입니다.
6. `accepted`이면 반환된 `capture_id`/`point_id`가 현재 촬영 티켓과 같은지 확인한 뒤 저장합니다. 기존 촬영 계획의 지점별 반복 수를 채우고 `/v1/measurements`에 전달합니다. 기본 제공 offset `[0,0,0]`은 렌즈 중심을 측정점으로 사용한다는 뜻입니다.
7. `retake`는 같은 지점 재촬영, `setup_required`는 아래 자동 설정 안내를 표시합니다. `setup_complete`는 준비 완료이며 가구 모서리 촬영 완료로 계산하지 않습니다.

Android의 라이브러리 빌드: JDK 17 + Android SDK 35 + Gradle 8.11.1에서 `gradle -p mobile/android assembleDebug`. iOS 파일은 iOS 앱 타깃에 추가합니다. 두 플랫폼 모두 카메라 세션 수명·미리보기·권한·네이티브 화면은 호스트 앱에서 연결해야 합니다. 이 저장소는 앱 스토어 배포 패키지를 만들지 않습니다.

## 보정 정보가 없는 기기

일부 기기는 보정 정보를 제공하지 않습니다. API는 임의의 초점거리나 왜곡 0을 만들지 않고 `automatic_setup_required`를 반환합니다. 사용자는 앱의 안내에 따라 이미 설치된 60cm 단일 마커를 화면 여러 위치·각도로 비춥니다. 체커보드 인쇄나 보정 숫자 입력은 요구하지 않습니다.

앱은 네 마커가 모두 검출된 서로 다른 사진 12~30장의 관측점을 `/v1/mobile/automatic-setup`에 전달합니다. 서버는 카메라 내부 행렬과 OpenCV 왜곡 계수를 추정하고, 화면 범위·각도 다양성·초점거리 불확실성·별도로 남겨둔 사진의 투영 오차를 검사합니다. 실패하면 추가 촬영을 안내하며 가구 등록을 진행하지 않습니다. 자동 설정용 사진과 가구 모서리 사진은 구분합니다.

자동 설정 결과는 현재 stream_id에서만 재사용합니다. 렌즈·초점·줌·출력 해상도·크롭 변경 또는 카메라 재시작 때 adapter와 client의 `reset()`을 호출하고 다시 준비합니다. 동일 파일을 복사해 반복 사진으로 사용하지 않습니다. 자동 설정에 사용한 이미지를 다른 보정 파이프라인으로 재처리하면 안 됩니다.

## 서버 처리

- Android: 센서 기준 내부 파라미터에 실제 크롭/출력 배율을 적용합니다. 왜곡 `[k1,k2,k3,p1,p2]`를 OpenCV `[k1,k2,p1,p2,k3]`로 변환합니다. 이미 왜곡 보정된 이미지에 이 계수를 이중 적용하지 않습니다.
- iOS: lensDistortionLookupTable로 원본 픽셀을 실제로 보정한 후 해당 픽셀의 코너를 검출합니다. 이때에만 `pixel_space=rectified`, `distortion=[]`를 사용합니다. Apple LUT를 OpenCV 계수로 간주하지 않습니다.
- 공통: 이미지 크기·픽셀 방향·시각·유한수를 검증합니다. 사용자가 카메라 숫자를 알 필요가 없습니다.

지원 버전/카메라 API 제공 여부·광학 특성에 따라 자동 설정이 실패할 수 있습니다. 모든 휴대폰 지원이나 실제 측정 정확도를 보장하지 않습니다. 네이티브 컴파일 검사와 합성 데이터 검사는 실기기 촬영 검증을 대신하지 않습니다. 출시 전에 대상 기기에서 초점 고정, 이미지/메타데이터 일치, 반복 촬영과 실측 오차를 확인해야 합니다.

## 웹 테스트와 서비스 빌드

웹 사진 추가는 `frontend` 개발 서버에서만 `VITE_ENABLE_CAPTURE_TESTS=true`로 켭니다. 기본값은 꺼짐입니다. 환경변수는 개발 실행 전에 설정하거나 git에 올리지 않는 `.env.local`에 둡니다.

`npm run build`로 생성한 실서비스 빌드는 테스트 플래그가 켜져 있어도 사진 선택·사진 검사·샘플 측정 완료 기능을 제공하지 않습니다. `mode=app`으로 들어가도 동일합니다. 앱 연결 안내와 측정된 도면 확인은 유지합니다. 네이티브 앱이 실제로 준비되기 전에는 웹을 네이티브 앱이라고 표시하지 않습니다.

웹 UI 제한은 서버 인증 장치가 아닙니다. 측정 서버 배포 시 기존 서비스 인증 계층을 사용해야 합니다.

## 공식 API 근거

- [Android CameraCharacteristics](https://developer.android.com/reference/android/hardware/camera2/CameraCharacteristics): 내부 파라미터·왜곡 계수는 기기에 따라 null이며 센서 좌표 기준입니다.
- [Apple AVCameraCalibrationData](https://developer.apple.com/documentation/avfoundation/avcameracalibrationdata)
- [Apple lens distortion correction 설명](https://developer.apple.com/videos/play/wwdc2017/507/)
- [OpenCV camera calibration](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html)


## 촬영 세션의 필름 마커

앱에서 공간 촬영을 시작할 때 `MeasurementClient.startFilmCapture(backendURL, sceneId, requestId)`를 호출한다. `requestId`는 UUID이며 응답 유실·재진입 때 재사용하도록 호스트가 보관한다. 응답의 `board`를 네이티브 촬영 어댑터 입력으로 전달한다. ID를 0으로 덮어쓰지 않는다.

```ts
const session = await client.startFilmCapture(backendURL, sceneId, requestId);
// Android/iOS shutter adapter: use session.board in every NativePayload.
const outcome = await client.capture({ ...nativePayload, board: session.board });
// Keep session.board for the final measurement, and use its marker for ENU alignment.
// On completion or explicit cancellation:
await client.endFilmCapture();
```

클라이언트는 매 촬영 전 세션 상태를 확인하고 다른 보드/ID 및 만료된 세션을 거절한다. 완료된 사진에는 `marker_session_id`가 포함된다. 현재 `virtual_applied`는 가상 필름 상태 적용이며 MCU의 실제 표시 확인은 아니다. 호스트 앱 UI 연결과 실기기 시험은 별도다.


필름 선택: `startFilmCapture(backendURL, sceneId, requestId, { window_id, module_row: 1, module_col: 2 })`처럼 네 번째 인자를 전달한다. 행·열은 실내 기준 왼쪽 위부터 0부터 센다. 생략하면 왼쪽 위 모듈이며, 응답 `film_module`과 `marker.T_ENU_marker`가 선택 결과다. 창 전체는 여러 1m 모듈로 구성할 수 있고 마커 크기는 60cm로 고정이다. 선택을 바꿀 때는 기존 세션 종료 후 새 요청 ID를 사용한다.
