# FR-01-8 큰 공간 응답 및 계산 한도 구현 명세

## 1. 문서 목적

이 문서는 GitHub issue #113, **큰 공간의 예측 응답 크기와 계산 한도 정리**를 구현하기 위한 명세다. 구현자는 이 문서만으로 현재 병목, 목표 API 계약, 계산 한도, 프런트 호환 변경, 성능 측정과 테스트 범위를 파악할 수 있어야 한다.

관련 이슈:

- #113 `[FR-01-8] 큰 공간의 예측 응답 크기와 계산 한도 정리`
- #114 `[FR-01-7] 예측 오류를 종류별로 구분해서 알리기`

이 문서는 #114에서 정의한 구조화 오류 계약이 먼저 구현되었다고 가정한다. 동시에 작업할 경우 #114 문서의 `SunpathErrorDetail`과 `SunpathValidationError`를 먼저 구현한다.

## 2. 현재 동작과 측정 기준

현재 측정값:

- 공간: 가로 12 m, 세로 8 m, 높이 3.5 m
- 가구: 서가 4개
- 보호 타일: 약 720개
- 응답: 약 31.5 MB
- 계산 시간: 약 3.8~5.2초
- 서가 9개: HTTP 422, `prediction exceeds the intersection-check limit`

현재 기본 응답은 모든 보호 영역에 대해 다음 정보를 모두 반환한다.

```text
region
  └─ timestamp
       └─ sample
            ├─ 좌표
            ├─ 상태와 reason
            ├─ candidate path 전체
            ├─ blocker 전체
            └─ 직접 일사량
```

응답 크기는 대략 `시각 수 × 샘플 수 × sample 진단 객체 크기`에 비례한다. 화면은 이 정보 중 대부분을 사용하지 않는다.

현재 프런트가 실제로 사용하는 값:

- `regions[].timeline[].status`
- `regions[].timeline[].exposed_sample_count`
- `samples.length`를 이용한 노출 비율
- `samples[].direct_irradiance_W_m2`를 이용한 최대 일사량
- `solar_positions[].timestamp`
- `solar_positions[].sun_direction_enu`
- `irradiance[]` 일부

따라서 노출 비율과 최대 일사량을 서버에서 집계하면 기본 화면에 전체 sample 진단은 필요하지 않다.

## 3. 구현 목표

1. 기본 FR-01 응답을 summary 형태로 경량화한다.
2. 개발·분석이 필요할 때만 기존 sample 진단을 요청할 수 있게 한다.
3. 작은 공간에서 노출 판정과 집계 결과를 바꾸지 않는다.
4. 계산 한도 공식을 코드와 문서에 명시한다.
5. 한도 초과 시 무엇을 줄여야 하는지 기계 판독 정보와 사용자 안내를 제공한다.
6. 4개 및 9개 서가 시나리오의 응답 크기·시간·한도 결과를 재현 가능하게 측정한다.

## 4. 확정 목표

### 4.1 기능 목표

- 기본 요청은 `summary` 응답을 반환한다.
- `diagnostic` 요청은 현재와 같은 sample 단위 진단을 반환한다.
- summary와 diagnostic의 region 상태, 노출 sample 수, 최대 직접 일사량은 동일해야 한다.
- 프런트 운영 화면은 summary만 사용한다.

### 4.2 크기와 시간 목표

기준 시나리오에서 다음 목표를 사용한다.

| 항목 | 목표 |
|---|---:|
| 12×8×3.5 m, 서가 4개 summary JSON | 1 MB 이하 |
| 같은 시나리오 계산 시간 | 기준 개발 환경 warm-run 중앙값 7초 이하 |
| 작은 기존 fixture 결과 | 의미상 변화 없음 |

시간은 CI의 절대 실패 조건으로 사용하지 않는다. 하드웨어와 첫 pvlib 호출 비용의 영향을 받기 때문이다. 응답 크기와 결과 동일성은 자동 테스트로 고정하고, 시간은 측정 도구가 기록하고 문서에 남긴다.

1 MB 목표를 달성하지 못하면 구현을 완료로 보지 않는다. 다만 JSON 직렬화 방식 차이를 피하기 위해 측정은 항상 compact JSON byte length로 한다.

```python
len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
```

## 5. 요청 계약

`backend/app/schemas/sunpath.py`의 `SunpathPredictionRequest`에 다음 필드를 추가한다.

```python
detail_level: Literal["summary", "diagnostic"] = "summary"
```

요청 예시:

```json
{
  "start_time": "2026-10-17T00:00:00+09:00",
  "duration_minutes": 1440,
  "interval_seconds": 1800,
  "expected_scene_revision": 2,
  "detail_level": "summary"
}
```

규칙:

- 생략 시 `summary`
- 운영 프런트는 항상 `summary`를 명시해 의도를 분명히 함
- `diagnostic`도 같은 계산 한도를 적용함
- 알 수 없는 값은 기존 Pydantic 422 배열로 거부

## 6. 응답 계약 v0.4.0

기본 응답 구조가 바뀌므로 `result_version`에 `0.4.0`을 추가하고 새 응답은 항상 `0.4.0`을 사용한다. nowcast DB engine 유무에 따라 `0.2.0` 또는 `0.3.0`을 선택하던 현재 로직은 제거한다. 응답 schema 버전은 데이터 source 유무가 아니라 JSON 계약 버전을 뜻해야 한다.

```python
result_version: Literal["0.4.0"] = "0.4.0"
```

### 6.1 timeline summary 필드

`RegionTimeResponse`를 다음처럼 변경한다.

```python
class RegionTimeResponse(SunpathModel):
    timestamp: datetime
    status: Literal["exposed", "not_exposed"]
    sample_count: int = Field(ge=0)
    exposed_sample_count: int = Field(ge=0)
    max_direct_irradiance_W_m2: NonnegativeIrradiance | None
    samples: list[SampleResultResponse] | None = None
```

직렬화 규칙:

- summary: `samples` 키 자체를 응답에서 제외
- diagnostic: `samples` 배열 포함
- `sample_count`: 해당 region의 전체 tile 수
- `exposed_sample_count`: `status == exposed`인 sample 수
- `max_direct_irradiance_W_m2`:
  - sample 중 일사량이 `null`이면 `null`
  - 모든 값이 숫자이면 최댓값
  - sample이 없으면 `null`
  - 차단되어 0인 sample은 정상 숫자 0으로 취급

Pydantic 모델에서 `samples=None`을 사용한다면 API route의 response serialization에서 `response_model_exclude_none=True`를 사용하거나, 명시적으로 `model_dump(exclude_none=True)`가 적용되는 응답 방식을 택한다. 목표는 summary JSON에 `"samples": null`도 반복하지 않는 것이다.

### 6.2 summary 응답 예시

```json
{
  "result_version": "0.4.0",
  "scene_id": "scene_demo_001",
  "scene_revision": 2,
  "coordinate_frame": "ENU",
  "units": "m",
  "exposure_basis": "geometric_direct_path",
  "optics_applied": false,
  "start_time": "2026-10-16T15:00:00Z",
  "end_time": "2026-10-17T15:00:00Z",
  "interval_seconds": 1800,
  "timestamps": ["..."],
  "solar_positions": ["..."],
  "irradiance": ["..."],
  "regions": [
    {
      "region_id": "ui_region_...",
      "aggregation": "any_sample",
      "first_predicted_exposure_time": "...",
      "exposed_at_start": false,
      "exposure_intervals": [],
      "timeline": [
        {
          "timestamp": "...",
          "status": "not_exposed",
          "sample_count": 180,
          "exposed_sample_count": 0,
          "max_direct_irradiance_W_m2": 0.0
        }
      ]
    }
  ],
  "assumptions": ["..."]
}
```

### 6.3 diagnostic 응답

`detail_level=diagnostic`이면 위 timeline entry에 기존 `samples`가 추가된다. sample 객체의 기존 필드는 삭제하거나 이름을 바꾸지 않는다.

```json
{
  "timestamp": "...",
  "status": "exposed",
  "sample_count": 180,
  "exposed_sample_count": 12,
  "max_direct_irradiance_W_m2": 425.2,
  "samples": [
    {
      "sample_index": 0,
      "point_m": [1.0, 2.0, 0.8],
      "status": "not_exposed",
      "reason": "occluder_blocked",
      "candidate_path": null,
      "blocker": {"kind": "occluder", "object_id": "...", "hit_m": ["..."], "distance_m": 1.2},
      "direct_irradiance_W_m2": 0.0
    }
  ]
}
```

## 7. 서비스 집계 구현

현재 `backend/app/services/sunpath.py`의 `_sample_response`와 region response 생성 부분을 변경한다.

각 engine `RegionTimeResult`에 대해 sample 결과를 한 번 순회해 다음을 계산한다.

```python
sample_count = len(entry.samples)
exposed_sample_count = sum(sample.exposed for sample in entry.samples)
irradiance_values = [direct_irradiance_W_m2(...each sample...)]
max_direct_irradiance_W_m2 = (
    None
    if not irradiance_values or any(value is None for value in irradiance_values)
    else max(irradiance_values)
)
```

주의 사항:

- summary라고 해서 engine sample 계산 자체를 생략하지 않는다. 현재 집계값을 만들려면 모든 tile 판정은 필요하다.
- 같은 sample의 직접 일사량을 summary 집계와 diagnostic 변환에서 두 번 계산하지 않는다.
- 내부 helper가 `(summary, optional sample responses)`를 한 번에 만들도록 한다.
- `entry.status`와 `exposed_sample_count > 0`의 관계를 테스트한다.
- 최대값 계산에서 `null`을 0으로 바꾸지 않는다.

추후 엔진 자체가 aggregate-only mode를 지원할 수 있지만 이번 이슈의 범위는 응답 직렬화 크기 절감이다.

## 8. 계산 한도

### 8.1 공식

현재 공식은 다음과 같으며 이 값을 명시적으로 유지한다.

```text
intersection_checks =
    timestamp_count
    × selected_sample_count
    × (
        window_count
        + occluder_count
        + room_corner_count
        + 2
      )
```

구성 요소:

- `timestamp_count`: `make_prediction_grid`가 만든 시각 수. 시작과 종료가 포함된다.
- `selected_sample_count`: 선택된 보호 영역들의 `tile_centres` 개수 합
- `window_count`: Scene window 수
- `occluder_count`: Scene 가구/가림물 수
- `room_corner_count`: `room.floor_vertices_m` 수
- `2`: 고정 기하 검사 여유 계수. 기존 공식을 보존하며 이 문서에서 의미를 바꾸지 않는다.

### 8.2 코드 구조

계산을 별도 불변 객체로 분리한다.

```python
@dataclass(frozen=True, slots=True)
class PredictionWorkEstimate:
    timestamp_count: int
    sample_count: int
    window_count: int
    occluder_count: int
    room_corner_count: int

    @property
    def intersection_checks(self) -> int:
        return (
            self.timestamp_count
            * self.sample_count
            * (
                self.window_count
                + self.occluder_count
                + self.room_corner_count
                + 2
            )
        )

    def context(self, max_checks: int) -> dict[str, int]: ...
```

`predict_scene`은 이 객체를 만든 뒤 한도와 비교한다. 공식이 여러 곳에 복제되면 안 된다.

### 8.3 한도 값

- `MAX_SELECTED_SAMPLES = 10_000` 유지
- `MAX_INTERSECTION_CHECKS = 5_000_000`은 측정 전에 임의로 올리지 않는다.

9개 서가를 성공시키기 위해 한도를 올리는 것은 측정 결과와 안전 근거가 있을 때만 한다. 다음을 모두 기록한 뒤 변경한다.

- 실제 estimated checks
- wall-clock 시간
- peak memory 또는 최소한 process RSS 측정 가능 여부
- timeout 15초 안에 프런트 요청이 완료되는지

안전 근거가 없으면 9개 서가는 구조화된 422를 반환하는 것이 정상 완료 조건이다.

### 8.4 한도 초과 응답

#114의 오류 계약을 사용한다.

```json
{
  "detail": {
    "code": "INTERSECTION_LIMIT_EXCEEDED",
    "message": "공간이 너무 복잡해 예측 계산 한도를 초과했습니다.",
    "target_type": "request",
    "context": {
      "estimated_checks": 7234560,
      "max_checks": 5000000,
      "timestamp_count": 49,
      "sample_count": 720,
      "window_count": 1,
      "occluder_count": 9,
      "room_corner_count": 4
    }
  }
}
```

샘플 한도 초과도 같은 방식으로 다음 context를 제공한다.

```json
{
  "detail": {
    "code": "SAMPLE_LIMIT_EXCEEDED",
    "message": "보호 영역의 계산 지점 수가 한도를 초과했습니다.",
    "target_type": "request",
    "context": {
      "sample_count": 12000,
      "max_samples": 10000
    }
  }
}
```

사용자에게 무엇을 줄일지는 프런트가 안정적인 코드에 따라 안내한다. 서버 context에 한국어 suggestions 배열을 중복 저장하지 않는다.

## 9. 프런트 변경

### 9.1 요청

`frontend/src/spaceApi.ts`의 `predictSunpath` body에 다음을 추가한다.

```typescript
detail_level: "summary"
```

### 9.2 타입

```typescript
type SunpathTimeline = {
  timestamp: string;
  status: "exposed" | "not_exposed";
  sample_count: number;
  exposed_sample_count: number;
  max_direct_irradiance_W_m2: number | null;
  samples?: {
    direct_irradiance_W_m2: number | null;
  }[];
};
```

운영 코드는 `samples`에 의존하지 않는다. optional 필드는 diagnostic 응답을 타입상 허용하기 위해서만 둔다.

### 9.3 3D 변환

`frontend/src/space3dServer.ts`의 `compactDay`를 변경한다.

기존:

```typescript
share: t.samples?.length
  ? (t.exposed_sample_count ?? 0) / t.samples.length
  : 0
```

변경:

```typescript
share: t.sample_count > 0
  ? t.exposed_sample_count / t.sample_count
  : 0
```

기존 sample 배열 최대값 계산은 다음으로 바꾼다.

```typescript
maxW: r.timeline.map(t => t.max_direct_irradiance_W_m2)
```

### 9.4 일정 계산

`frontend/src/sunlight.ts`의 `scheduleFromPrediction`은 이미 timeline status만 사용하므로 알고리즘 변경이 없어야 한다. 새 fixture에 `sample_count`와 `max_direct_irradiance_W_m2`를 추가하는 정도만 필요하다.

## 10. 내부 호출과 호환성 점검

`predict_scene`은 sunpath API 외에도 다음 경로에서 사용된다.

- `backend/app/services/shading.py`
- `backend/app/services/adaptive_control.py`

점검 규칙:

- 두 호출자가 sample 상세를 실제로 읽는지 검색한다.
- sample 상세가 필요하면 내부 호출 request에 `detail_level="diagnostic"`을 강제하지 말고, 가능하면 engine 결과 또는 새 summary 집계값을 사용하도록 바꾼다.
- 불가피하게 diagnostic이 필요하다면 이유를 코드 주석과 테스트에 남긴다.
- response schema가 `0.4.0`만 허용하도록 바뀌므로 기존 fixture의 version assertion을 모두 갱신한다.

FR-02 shading 응답의 의미나 결과를 바꾸면 안 된다.

## 11. 성능 측정 도구

`tools/` 아래에 재현 가능한 측정 스크립트를 추가하거나 기존 dummy data 실행 도구를 확장한다. 예시 이름:

```text
tools/measure_fr01_large_space.py
```

입력:

- `tools/dummy_data/scenarios.json`의 서가 4개 기준 시나리오
- 같은 파일의 S6b 서가 9개 시나리오
- warm-up 횟수 1
- 측정 횟수 기본 3

출력:

```json
{
  "scenario": "S6a",
  "detail_level": "summary",
  "status": 200,
  "response_bytes": 456789,
  "elapsed_seconds": [4.1, 3.9, 4.0],
  "median_elapsed_seconds": 4.0,
  "estimated_checks": 3213000
}
```

9개 서가가 거부되면 다음을 기록한다.

```json
{
  "scenario": "S6b",
  "status": 422,
  "error_code": "INTERSECTION_LIMIT_EXCEEDED",
  "estimated_checks": 7234560,
  "max_checks": 5000000
}
```

측정 스크립트는 외부 네트워크에 의존하지 않아야 한다. 테스트 DB와 저장된/dummy Scene을 사용하고, provider 호출 없이 durable fallback 또는 고정 weather를 사용한다.

측정 결과는 `docs/fr01_api.md`의 성능/한도 절에 실행 날짜, 환경, 결과와 함께 기록한다. 측정 산출물 자체를 매번 git에 추가할 필요는 없다.

## 12. 파일별 구현 순서

1. #114 문서의 구조화 오류 계약 구현
2. `backend/app/schemas/sunpath.py`
   - request `detail_level`
   - timeline summary 필드
   - `result_version="0.4.0"`
3. `backend/app/services/sunpath.py`
   - `PredictionWorkEstimate`
   - 한도 context
   - sample 단일 순회 집계
   - summary/diagnostic response 생성
4. `backend/app/api/sunpath.py`
   - summary에서 `None` 필드 제외 확인
5. `frontend/src/spaceApi.ts`
   - summary 요청과 타입 갱신
6. `frontend/src/space3dServer.ts`
   - 서버 집계 필드 사용
7. 내부 호출자와 fixture 갱신
8. 테스트 추가
9. 측정 도구 및 API 문서 갱신

## 13. 테스트 명세

### 13.1 API 계약 테스트

`tests/test_sunpath_api.py`:

1. 기본 요청
   - `result_version == "0.4.0"`
   - timeline에 `sample_count`, `exposed_sample_count`, `max_direct_irradiance_W_m2`가 있음
   - timeline에 `samples` 키가 없음
2. `detail_level=summary`
   - 기본 요청과 같은 shape
3. `detail_level=diagnostic`
   - 기존 sample 필드가 모두 존재
4. summary/diagnostic 동일성
   - region 수와 순서 동일
   - timestamp, status, sample count, exposed count, max irradiance 동일
5. 잘못된 detail level
   - 기존 Pydantic 422 배열
6. 계산량 한도
   - 오류 코드와 모든 context 수치 검증
   - `estimated_checks`가 공식과 일치
7. 샘플 한도
   - sample count와 max samples 검증

### 13.2 응답 크기 테스트

대형 fixture가 테스트 시간을 과도하게 늘리지 않는 범위에서 compact JSON byte 크기를 assertion한다.

권장 방식:

- 기준 fixture를 한 번 계산
- summary가 1 MB 이하인지 확인
- 같은 결과의 diagnostic보다 summary가 최소 80% 이상 작은지 확인

시간은 assertion하지 않는다.

### 13.3 프런트 테스트

`frontend/src/sunpath.test.ts`:

- 요청 body에 `detail_level: "summary"`
- 기존 revision, duration, interval 값 유지

`frontend/src/space3dServer.test.ts`:

- sample 배열 없이 share 계산
- `sample_count == 0`이면 share 0
- maxW가 summary 필드를 그대로 사용
- maxW의 null 보존

`frontend/src/sunlight.test.ts` 또는 기존 관련 테스트:

- sample 배열이 없는 prediction으로 일정 생성
- 작은 공간의 기존 차단 구간과 동일

### 13.4 회귀 테스트

```powershell
python -m pytest -q tests/test_fr01_prediction.py tests/test_sunpath_api.py tests/test_shading_plans_api.py tests/test_ui_spaces.py
cd frontend
npm test
```

완료 전에는 전체 Python test suite도 실행한다.

```powershell
python -m pytest -q
```

## 14. 문서 변경

`docs/fr01_api.md`를 v0.4.0 기준으로 갱신한다.

반드시 포함할 내용:

- `detail_level` request 필드와 기본값
- summary와 diagnostic 응답 예시
- timeline 집계 필드 의미
- result version 변경 이유
- 계산 한도 공식
- sample 및 intersection 기본 한도
- 한도 초과 구조화 오류 예시
- 무엇을 줄이면 되는지에 대한 사용자 지침
- 기준 시나리오의 측정 환경과 결과

`docs/architecture.md`에는 기본 응답이 aggregate summary이고 sample 진단은 opt-in이라는 한 문장을 추가한다.

## 15. 완료 조건

- [ ] 기본 응답은 summary이며 `samples` 키를 포함하지 않는다.
- [ ] diagnostic 요청은 기존 sample 진단을 반환한다.
- [ ] 응답 version은 `0.4.0`이다.
- [ ] summary와 diagnostic의 집계 결과가 동일하다.
- [ ] 프런트는 sample 배열 없이 동일한 일정과 3D 집계를 만든다.
- [ ] 기준 4개 서가 summary 응답이 compact JSON 1 MB 이하다.
- [ ] 계산 한도 공식이 단일 코드 경로와 API 문서에 있다.
- [ ] 9개 서가가 한도 안에서 성공하거나, 실제값과 한도를 담은 구조화된 422를 반환한다.
- [ ] 한도 초과 화면이 줄여야 할 항목을 한국어로 안내한다.
- [ ] 작은 공간의 기존 결과가 바뀌지 않는다.
- [ ] sunpath, shading, UI 관련 테스트가 통과한다.
- [ ] 측정 도구로 크기와 시간을 재현할 수 있다.

## 16. 구현 시 주의할 점

- 응답만 줄이는 작업과 계산 알고리즘 최적화를 한 커밋에서 섞지 않는다.
- 한도를 올려 9개 서가를 억지로 통과시키지 않는다.
- summary에서 `samples: null`을 반복 반환하지 않는다.
- `result_version`을 날씨 source 또는 DB engine 유무에 따라 선택하지 않는다.
- `max_direct_irradiance_W_m2=null`은 미상이라는 뜻이며 0으로 바꾸지 않는다.
- diagnostic의 기존 sample 필드 이름과 의미를 유지한다.
- 프런트 오류 문구에 estimated checks나 내부 객체 ID를 직접 표시하지 않는다.
