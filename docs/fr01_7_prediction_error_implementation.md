# FR-01-7 예측 오류 구조화 구현 명세

## 1. 문서 목적

이 문서는 GitHub issue #114, **예측 오류를 종류별로 구분해서 알리기**를 구현하기 위한 명세다. 구현자는 이 문서만으로 서버 오류 모델, 변환 위치, HTTP 응답, 화면 문구, 테스트 범위와 완료 조건을 파악할 수 있어야 한다.

관련 이슈:

- #114 `[FR-01-7] 예측 오류를 종류별로 구분해서 알리기`
- #113 `[FR-01-8] 큰 공간의 예측 응답 크기와 계산 한도 정리`

#113의 계산 한도 초과도 이 문서에서 정의하는 오류 계약을 사용한다. 따라서 구현 순서는 #114의 공통 오류 계약을 먼저 만들고, #113에서 계산량 상세 정보를 채우는 순서를 권장한다.

## 2. 현재 동작과 문제

### 서버

`backend/app/services/sunpath.py`의 `SunpathValidationError`는 메시지만 가진 `ValueError`다. 다음처럼 원인이 다른 실패가 모두 문자열로 전달된다.

- 계산 한도: `prediction exceeds the intersection-check limit`
- 보호 타일과 가구 겹침: `sample 20 of ui_region_<hash> is inside occluder ui_furniture_<hash>`
- 샘플 수 한도: `selected regions exceed the sample-point limit`
- 그 밖의 Scene 기하 및 요청 오류

`backend/app/api/sunpath.py`는 위 예외와 `pydantic.ValidationError`를 모두 다음과 같은 422로 바꾼다.

```json
{
  "detail": "prediction exceeds the intersection-check limit"
}
```

### 화면

`frontend/src/spaceApi.ts`는 사람이 읽을 수 없는 영문 서버 문구를 표시하지 않는다. 따라서 위 실패들은 모두 일반적인 422 문구로 합쳐진다.

```text
입력값 중 허용 범위를 벗어난 항목이 있습니다. 숫자와 필수 항목을 확인해 주세요.
```

사용자는 가구 배치를 고쳐야 하는지, 계산 범위를 줄여야 하는지 판단할 수 없다. 해시된 내부 ID를 그대로 보여주는 것도 허용하지 않는다.

## 3. 구현 목표

1. FR-01 도메인 오류를 안정적인 코드로 구분한다.
2. API가 오류 코드, 대상 종류, 대상 ID, 사용자 표시 이름, 추가 정보를 반환한다.
3. 화면이 오류 종류별 한국어 안내를 표시한다.
4. `ui_furniture_<hash>`, `ui_region_<hash>` 같은 내부 ID를 화면에 표시하지 않는다.
5. FastAPI/Pydantic의 기존 필드 검증 422 배열 형식은 유지한다.
6. 기존 404, 409, 성공 응답의 동작은 바꾸지 않는다.

## 4. 범위에서 제외하는 것

- HTTP 상태를 422 이외의 값으로 세분화하지 않는다.
- Scene 저장 형식에 오류 표시 전용 필드를 추가하지 않는다.
- 영속 오류 로그나 별도 오류 테이블을 만들지 않는다.
- FR-02, FR-04의 모든 오류를 같은 코드 체계로 마이그레이션하지 않는다.
- 서버의 임의 문자열을 사용자에게 그대로 표시하지 않는다.

## 5. 최종 오류 계약

### 5.1 오류 코드

`backend/app/schemas/sunpath.py`에 다음 literal 타입과 응답 모델을 추가한다.

```python
SunpathErrorCode = Literal[
    "FURNITURE_OVERLAP",
    "INTERSECTION_LIMIT_EXCEEDED",
    "SAMPLE_LIMIT_EXCEEDED",
    "INVALID_SCENE_GEOMETRY",
    "INVALID_PREDICTION_REQUEST",
]

SunpathErrorTargetType = Literal[
    "furniture",
    "protected_region",
    "scene",
    "request",
]


class SunpathErrorDetail(SunpathModel):
    code: SunpathErrorCode
    message: str
    target_type: SunpathErrorTargetType | None = None
    target_id: str | None = None
    target_name: str | None = None
    related_target_type: SunpathErrorTargetType | None = None
    related_target_id: str | None = None
    related_target_name: str | None = None
    context: dict[str, object] = Field(default_factory=dict)
```

`message`는 서버가 관리하는 안전한 한국어 기본 문구다. 화면은 `code`를 우선 사용해 문구를 선택하고, 알 수 없는 신규 코드에만 `message`를 fallback으로 사용한다.

`context`는 코드별 기계 판독 정보만 담는다. 예외 traceback, 원본 Scene JSON, 임의 런타임 문자열은 담지 않는다.

### 5.2 HTTP 응답

FR-01 도메인 오류는 HTTP 422와 다음 형태를 사용한다.

```json
{
  "detail": {
    "code": "FURNITURE_OVERLAP",
    "message": "보호 영역과 가구가 겹쳐 있어 예측할 수 없습니다.",
    "target_type": "protected_region",
    "target_id": "ui_region_0123...",
    "target_name": "서가 3 보호 영역",
    "related_target_type": "furniture",
    "related_target_id": "ui_furniture_4567...",
    "related_target_name": "서가 3",
    "context": {
      "sample_index": 20
    }
  }
}
```

계산 한도 오류의 예시는 다음과 같다. 수치는 #113에서 채운다.

```json
{
  "detail": {
    "code": "INTERSECTION_LIMIT_EXCEEDED",
    "message": "공간이 너무 복잡해 예측 계산 한도를 초과했습니다.",
    "target_type": "request",
    "target_id": null,
    "target_name": null,
    "related_target_type": null,
    "related_target_id": null,
    "related_target_name": null,
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

### 5.3 기존 Pydantic 오류와의 공존

요청 body 자체가 유효하지 않아 FastAPI가 만드는 422는 변경하지 않는다.

```json
{
  "detail": [
    {
      "type": "greater_than_equal",
      "loc": ["body", "duration_minutes"],
      "msg": "Input should be greater than or equal to 1",
      "input": 0
    }
  ]
}
```

프런트의 `invalidFieldsMessage`는 이 배열 형식을 계속 처리해야 한다.

## 6. 서버 내부 예외 설계

### 6.1 `SunpathValidationError`

`backend/app/services/sunpath.py`에서 예외를 다음 정보가 있는 타입으로 바꾼다.

```python
class SunpathValidationError(ValueError):
    def __init__(
        self,
        code: SunpathErrorCode,
        message: str,
        *,
        target_type: SunpathErrorTargetType | None = None,
        target_id: str | None = None,
        target_name: str | None = None,
        related_target_type: SunpathErrorTargetType | None = None,
        related_target_id: str | None = None,
        related_target_name: str | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.detail = SunpathErrorDetail(...)
```

API 계층은 `str(error)`를 사용하지 않고 `error.detail.model_dump(mode="json")`을 사용한다.

### 6.2 엔진 예외

문자열 정규식으로 `sample ... is inside occluder ...`를 영구 파싱하지 않는다. `engine/fr01/predictor.py`에 엔진 전용 타입 예외를 추가한다.

```python
class SampleInsideOccluderError(ValueError):
    def __init__(self, sample_index: int, region_id: str, occluder_id: str) -> None:
        self.sample_index = sample_index
        self.region_id = region_id
        self.occluder_id = occluder_id
        super().__init__(f"sample {sample_index} of {region_id} is inside occluder {occluder_id}")
```

기존 메시지는 엔진 단위 테스트와 로그 호환성을 위해 유지하되, 서비스는 속성으로 분류한다.

`predict_region_exposure` 또는 실제 겹침을 발견하는 가장 가까운 위치에서 이 예외를 발생시킨다.

### 6.3 이름 조회

FR-06 Scene의 객체에는 이미 `name`이 있다. `predict_scene`에서 다음 lookup을 한 번 만든다.

```python
region_names = {item.region_id: item.name for item in scene.protected_regions}
occluder_names = {item.occluder_id: item.name for item in scene.occluders}
```

`SampleInsideOccluderError`를 잡으면 다음과 같이 변환한다.

- `target_type`: `protected_region`
- `target_id`: `error.region_id`
- `target_name`: `region_names.get(error.region_id)`
- `related_target_type`: `furniture`
- `related_target_id`: `error.occluder_id`
- `related_target_name`: `occluder_names.get(error.occluder_id)`
- `context.sample_index`: `error.sample_index`

이름이 없는 외부 Scene은 `target_name=null`로 둔다. 프런트는 이 경우에도 내부 ID를 문구에 넣지 않는다.

### 6.4 오류 분류표

| 발생 조건 | 코드 | 대상 | 기본 메시지 |
|---|---|---|---|
| 보호 타일이 occluder 내부 | `FURNITURE_OVERLAP` | 보호 영역 + 가구 | 보호 영역과 가구가 겹쳐 있어 예측할 수 없습니다. |
| 계산 교차 검사량 초과 | `INTERSECTION_LIMIT_EXCEEDED` | 요청 | 공간이 너무 복잡해 예측 계산 한도를 초과했습니다. |
| 선택 샘플 수 초과 | `SAMPLE_LIMIT_EXCEEDED` | 요청 | 보호 영역의 계산 지점 수가 한도를 초과했습니다. |
| 저장 Scene 기하 변환 또는 엔진 기하 실패 | `INVALID_SCENE_GEOMETRY` | Scene 또는 관련 객체 | 공간 배치를 계산할 수 없습니다. |
| 서비스 단계 요청 의미 검증 실패 | `INVALID_PREDICTION_REQUEST` | 요청 | 예측 요청값을 확인해 주세요. |

다음은 `INVALID_PREDICTION_REQUEST`로 분류한다.

- 알 수 없는 `protected_region_ids`
- 선택된 보호 영역이 없음
- 서비스 직접 호출에서 잘못된 timestamp 또는 prediction grid

FastAPI가 먼저 거르는 body schema 오류는 기존 Pydantic 오류로 남긴다.

## 7. API 계층 변경

`backend/app/api/sunpath.py`의 예외 처리를 분리한다.

```python
except SunpathValidationError as error:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=error.detail.model_dump(mode="json"),
    ) from error
except ValidationError as error:
    # 저장된 Scene JSON이 현재 schema를 만족하지 않는 경우다.
    detail = SunpathErrorDetail(
        code="INVALID_SCENE_GEOMETRY",
        message="저장된 공간 정보를 계산할 수 없습니다.",
        target_type="scene",
    )
    raise HTTPException(status_code=422, detail=detail.model_dump(mode="json")) from error
```

주의: 요청 body의 `RequestValidationError`는 이 함수에 진입하기 전에 FastAPI가 처리하므로 위 `ValidationError`와 다르다.

`backend/app/api/shading.py`도 같은 `SunpathValidationError`를 잡는다. 최소 변경 원칙은 다음과 같다.

- 예외 생성자 변경으로 shading 경로가 깨지지 않게 한다.
- shading endpoint도 동일한 `detail` 객체를 반환해 일관성을 유지한다.
- 이 작업에서 shading 전용 신규 오류 코드는 추가하지 않는다.

## 8. 프런트 구현

### 8.1 타입 가드

`frontend/src/spaceApi.ts`에 서버 오류 타입과 타입 가드를 둔다.

```typescript
type SunpathErrorCode =
  | "FURNITURE_OVERLAP"
  | "INTERSECTION_LIMIT_EXCEEDED"
  | "SAMPLE_LIMIT_EXCEEDED"
  | "INVALID_SCENE_GEOMETRY"
  | "INVALID_PREDICTION_REQUEST";

type SunpathErrorDetail = {
  code: SunpathErrorCode;
  message: string;
  target_type?: string | null;
  target_name?: string | null;
  related_target_type?: string | null;
  related_target_name?: string | null;
  context?: Record<string, unknown>;
};
```

`request()`가 422를 받았을 때 처리 순서는 다음과 같다.

1. `detail`이 구조화된 FR-01 오류이면 코드별 문구를 만든다.
2. `detail`이 배열이면 기존 `invalidFieldsMessage`를 사용한다.
3. 그 밖의 422는 기존 일반 문구를 사용한다.

### 8.2 사용자 문구

정확한 문구는 다음으로 고정한다.

| 코드 | 표시 문구 |
|---|---|
| `FURNITURE_OVERLAP` | 이름 둘 다 있음: `“{가구명}”과 “{보호 영역명}”이 겹쳐 있어 예측할 수 없습니다. 가구 또는 보호 영역의 위치를 확인해 주세요.` |
| `FURNITURE_OVERLAP` | 이름 일부/전부 없음: `가구와 보호 영역이 겹쳐 있어 예측할 수 없습니다. 위치를 확인해 주세요.` |
| `INTERSECTION_LIMIT_EXCEEDED` | `공간이 너무 복잡해 예측 범위를 초과했습니다. 예측 시간 간격을 늘리거나 보호 영역 또는 가구 수를 줄여 주세요.` |
| `SAMPLE_LIMIT_EXCEEDED` | `보호 영역이 너무 넓어 계산 지점 수를 초과했습니다. 보호 영역의 크기나 개수를 줄여 주세요.` |
| `INVALID_SCENE_GEOMETRY` | `공간 배치를 계산할 수 없습니다. 창문, 가구, 보호 영역의 위치를 확인해 주세요.` |
| `INVALID_PREDICTION_REQUEST` | `예측 요청값을 확인해 주세요.` |

문구 생성 함수에는 `target_id`, `related_target_id`를 전달하지 않거나 사용하지 않는다.

알 수 없는 신규 코드이면서 서버 `message`가 한글을 포함하면 그 메시지를 사용하고, 아니면 일반 422 문구를 사용한다.

## 9. 파일별 구현 순서

1. `engine/fr01/predictor.py`
   - `SampleInsideOccluderError` 추가
   - 기존 `ValueError` 발생 지점을 타입 예외로 변경
2. `backend/app/schemas/sunpath.py`
   - 오류 코드, 대상 타입, `SunpathErrorDetail` 추가
3. `backend/app/services/sunpath.py`
   - 구조화된 `SunpathValidationError` 구현
   - 기존 모든 발생 지점에 코드 부여
   - 엔진 오류를 ID와 이름이 포함된 API 오류로 변환
4. `backend/app/api/sunpath.py`
   - 구조화된 detail 반환
   - 저장 Scene `ValidationError`를 Scene 오류로 변환
5. `backend/app/api/shading.py`
   - 공유 예외의 detail 객체 반환
6. `frontend/src/spaceApi.ts`
   - 타입 가드와 코드별 문구 추가
7. 테스트 추가 및 기존 assertion 갱신

## 10. 테스트 명세

### 10.1 엔진 단위 테스트

`tests/test_fr01_prediction.py`:

- 타일이 occluder 안에 있으면 `SampleInsideOccluderError` 발생
- `sample_index`, `region_id`, `occluder_id` 속성이 정확함
- 기존 예외 메시지 내용도 유지됨

### 10.2 API 테스트

`tests/test_sunpath_api.py`:

1. 가구 겹침
   - status 422
   - `detail.code == "FURNITURE_OVERLAP"`
   - 보호 영역 및 가구 ID가 올바름
   - 두 사용자 이름이 포함됨
   - sample index가 context에 있음
2. 계산 한도
   - `MAX_INTERSECTION_CHECKS`를 낮춰 발생
   - `detail.code == "INTERSECTION_LIMIT_EXCEEDED"`
3. 샘플 한도
   - `MAX_SELECTED_SAMPLES`를 낮춰 발생
   - `detail.code == "SAMPLE_LIMIT_EXCEEDED"`
4. 잘못된 저장 Scene
   - `detail.code == "INVALID_SCENE_GEOMETRY"`
5. 알 수 없는 region 선택
   - `detail.code == "INVALID_PREDICTION_REQUEST"`
6. 기존 body 검증
   - timezone 없는 `start_time`은 detail 배열 형식 유지
7. 기존 404와 409
   - status 및 의미 변화 없음

### 10.3 프런트 테스트

`frontend/src/spaceApi.test.ts` 또는 `frontend/src/sunpath.test.ts`:

- 각 코드에 맞는 한국어 문구 생성
- 가구와 보호 영역 이름이 문구에 포함
- 내부 해시 ID가 문구에 포함되지 않음
- 이름이 없을 때 안전한 일반 문구 사용
- Pydantic detail 배열은 기존 필드 문구 사용
- 알 수 없는 영문 detail을 그대로 표시하지 않음

### 10.4 실행 명령

```powershell
python -m pytest -q tests/test_fr01_prediction.py tests/test_sunpath_api.py tests/test_shading_plans_api.py
cd frontend
npm test
```

전체 프런트 테스트가 오래 걸리면 개발 중에는 관련 파일만 Node test runner로 실행할 수 있지만, 완료 전에는 `npm test` 전체를 실행한다.

## 11. 완료 조건

- [ ] 가구 겹침, 계산 한도, 샘플 한도, 기타 기하 오류가 서로 다른 코드로 반환된다.
- [ ] 오류 응답에 관련 대상 ID와 가능한 경우 사용자 이름이 포함된다.
- [ ] 화면은 코드별 한국어 문구를 표시한다.
- [ ] 화면 문구에 내부 해시 ID가 노출되지 않는다.
- [ ] 기존 Pydantic 422 배열 처리가 유지된다.
- [ ] 기존 404, 409와 성공 응답이 바뀌지 않는다.
- [ ] sunpath와 shading endpoint가 공유 예외 변경 때문에 깨지지 않는다.
- [ ] API, 엔진, 프런트 테스트가 통과한다.

## 12. 후속 작업과 #113 연결

#113은 `INTERSECTION_LIMIT_EXCEEDED`의 `context`에 실제 계산량과 각 구성 요소를 추가한다. 이 문서 구현 시 context가 임의 JSON이 아니라 JSON 직렬화 가능한 숫자·문자열·배열만 받도록 유지해야 한다.

#113에서 기본 응답을 summary로 바꾸더라도 이 오류 계약과 오류 코드는 변경하지 않는다.
