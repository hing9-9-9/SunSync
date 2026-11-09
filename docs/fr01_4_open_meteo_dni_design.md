# FR-01.4 Open-Meteo 기반 미래 직접일사량 예측

> **문서 역할:** 기존 Open-Meteo worker가 Scene별 hourly DNI를 저장하고 FR-01 타일
> 계산에 제공하는 운영 설계다. KMA AI 모델의 학습용 archived run 복원과는 목적이 다르다.
>
> - 운영 worker: Best Match 예보 snapshot을 미리 저장하고 API가 이를 조회한다.
> - KMA AI 개발: ECMWF IFS 0.25° single run을 복원하고 `issued_at + 7시간` 이후에만
>   이용 가능하다고 간주한다.
> - 동결 AI 모델을 운영 DNI 선택 흐름에 연결하는 작업은 final test와 팀 검토 후 진행한다.

## 목적과 범위

SunSync의 FR-01은 미래의 어느 시각에 햇빛이 보호 대상에 닿는지, 그리고 닿는다면 얼마나 강한지를 예측한다. 이 기능은 두 부분이 함께 동작해야 한다.

- **FR-01.1**은 저장된 Scene의 건물 위치, 창 방향, 실내 구조와 가구를 사용한다. pvlib로 태양 위치를 계산하고, 보호영역의 5 cm 타일 중심에서 태양까지 가는 경로가 창을 통과하는지 확인한다. 즉, 햇빛이 *들어올 수 있는지*를 판단한다.
- **FR-01.4**는 그 시각의 지역별 직달일사량(DNI)을 선택한다. 먼저 Scene에 유효한 날씨 값이 있는지 확인하고, 없으면 미리 수집해 둔 Open-Meteo 예보를 사용하며, 이것도 없으면 pvlib의 맑은 하늘 계산값을 사용한다. 즉, 들어올 수 있는 햇빛이 *얼마나 강한지*를 결정한다.

FR-01.1의 기하학 결과와 FR-01.4의 최종 DNI를 합쳐 각 타일의 `direct_irradiance_W_m2`를 만든다. 이 값은 W/m²(제곱미터당 와트) 단위의 타일 표면 직달일사량이다.

Open-Meteo 값은 건물 내부나 창에서 직접 측정한 값이 아니라, 해당 지역 격자에 대한 예보다. 창 방향·가구 차폐·보호면 방향에 따른 차이는 FR-01.1이 처리한다.

```text
Scene의 건물 위치·실내 구조
        │
        ├─ FR-01.1: 태양 위치와 햇빛 경로 확인
        │            → 타일별 노출 / 비노출
        │
        └─ FR-01.4: 날씨·예보에서 최종 DNI 선택
                     → 타일별 직접일사량 계산
                                  │
                                  └─ FR-02: 필요한 film cell 결정
```

FR-01은 film cell의 최종 상태를 정하지 않는다. 타일별 경로와 직접일사량을 반환하고, 창의 어느 film cell을 제어할지는 FR-02가 결정한다.

## Open-Meteo에서 수집하는 데이터

별도 worker가 저장된 Scene의 각 고유 건물 위치에 대해 Open-Meteo Forecast API를 호출한다. API 요청은 다음 형식이다.

```text
GET https://api.open-meteo.com/v1/forecast
  ?latitude=<Scene.site.latitude_deg>
  &longitude=<Scene.site.longitude_deg>
  &hourly=direct_normal_irradiance,cloud_cover
  &timeformat=unixtime
```

| 요청 값 | 뜻 | 사용하는 이유 |
| --- | --- | --- |
| `latitude`, `longitude` | Scene에 저장된 건물 위치 | 해당 건물 위치의 지역 예보를 요청한다. |
| `direct_normal_irradiance` | 태양을 정면으로 향한 면의 직접일사량(DNI), W/m² | 타일별 햇빛 세기를 계산하는 기본 입력이다. |
| `cloud_cover` | 구름량, 0~100% | 예보 응답의 보조 정보와 provenance로 보관한다. 현재 타일 세기를 별도 보정하는 데 사용하지 않는다. |
| `timeformat=unixtime` | 응답 시각 형식 | timezone 혼동 없이 UTC 시각으로 처리하기 위해 사용한다. |

`models` 파라미터는 보내지 않는다. 따라서 Open-Meteo의 기본 Best Match 모델 선택을 따르며, 응답에 모델 정보가 있으면 함께 기록하고 없으면 `unknown`으로 기록한다.

Open-Meteo의 hourly DNI는 순간값이 아니라 **직전 한 시간 평균값**이다. 예를 들어 응답 timestamp가 `14:00Z`이면 이 값은 `[13:00Z, 14:00Z)`에 적용한다. 따라서 13:05Z 예측에는 14:00Z record를 사용하고, 14:00Z 예측에는 다음 한 시간 record를 사용한다.

```text
record.valid_time = 14:00Z
적용 시간 구간     = [13:00Z, 14:00Z)
```

## 저장하는 데이터

worker는 Open-Meteo 응답을 API 요청 중에 다시 호출하지 않고, SQLite에 변경되지 않는 snapshot으로 저장한다. 저장 내용은 다음과 같다.

- 수집 시각(`fetched_at`)과 응답 원문 hash
- 요청한 건물 좌표와 Open-Meteo가 반환한 예보 격자 좌표·고도
- Best Match 모델 선택 정보
- 각 한 시간 구간의 시작·끝 시각, DNI, 구름량
- 응답이 유효한지 나타내는 상태

건물 좌표와 예보 격자 좌표는 다를 수 있다. 건물 좌표는 태양 위치와 실내 기하 계산에 사용하고, 격자 좌표는 이 DNI 값이 어느 지역 예보인지 설명하는 provenance로 반환한다.

## 최종 DNI를 고르는 방법

예측 요청의 각 timestamp마다 아래 순서로 하나의 최종 DNI를 고른다. 한 시각의 fallback이 다른 시각의 결과를 바꾸지 않는다.

1. Scene에 포함된 날씨 데이터가 단위, 시간 범위, 신선도 조건을 만족하면 그 값을 사용한다.
2. 그렇지 않으면 요청을 받은 시각 이전에 저장된 Open-Meteo snapshot 중, 해당 timestamp를 포함하는 한 시간 구간의 DNI를 사용한다.
3. 같은 구간을 포함하는 snapshot이 여러 개면 더 최근에 수집한 것을 우선하고, 수집 시각도 같으면 snapshot ID 순서로 하나를 고른다.
4. 사용할 Scene 날씨나 Open-Meteo DNI가 없거나 유효하지 않으면, 그 timestamp만 pvlib의 clear-sky DNI로 계산한다.
5. clear-sky 계산도 할 수 없으면 최종 DNI는 `unknown`이다.

여기서 신선도는 예보가 가리키는 미래 시각이 아니라 **언제 수집했는지**(`fetched_at`)로 판단한다. 요청을 받은 뒤에 수집된 snapshot은 과거 예측의 근거로 사용하지 않는다.

```text
유효한 Scene.weather
        ↓ 없거나 사용할 수 없음
저장된 Open-Meteo hourly snapshot
        ↓ 없거나 사용할 수 없음
pvlib clear-sky DNI
        ↓ 계산 실패
unknown
```

## 타일별 직접일사량과 FR-02 전달

worker는 예보를 수집·저장하는 역할만 한다. 최종 DNI 선택과 타일별 직접일사량 계산은 햇빛 경로 예측 API가 요청을 처리할 때 수행한다.

1. FR-01.1이 각 5 cm 타일에 대해 해당 시각의 햇빛 경로를 검사한다.
2. FR-01.4가 같은 시각의 최종 DNI를 위 우선순위로 고른다.
3. 햇빛 경로가 열려 있는 타일은 아래 식으로 표면에 실제 닿는 직접일사량을 계산한다.

   ```text
   direct_irradiance_W_m2
     = 최종 DNI × max(0, 보호면 법선 · 태양 방향 벡터)
   ```

   보호면이 태양을 비스듬히 볼수록 값이 줄어든다. 창의 투과율, 반사, 굴절, 실외 차폐는 아직 계산하지 않는다.

4. 햇빛 경로가 막힌 타일은 DNI 값과 무관하게 `0`을 반환한다. 경로는 열려 있지만 DNI를 알 수 없으면 `null`을 반환한다.
5. FR-01 API는 타일별 `direct_irradiance_W_m2`, `candidate_path.window_id`, `candidate_path.window_hit_m`를 함께 반환한다.
6. FR-02는 같은 `scene_id`와 `scene_revision`의 응답에서 `status="exposed"`, `reason="clear_path"`, 창 교점이 모두 있는 타일을 shading 후보로 삼는다. FR-02.1이 그 창 교점을 film cell로 변환하고, 보호 등급 판단에는 raw DNI가 아닌 타일별 `direct_irradiance_W_m2`를 사용한다.

FR-01에서 FR-02로 전달하는 필드와 좌표 규칙의 상세 내용은 [FR-01 → FR-02 tile contract](fr01_fr02_tile_contract.md)를 따른다.

## 구현 흐름과 코드 위치

| 단계 | 담당 코드 | 하는 일 |
| --- | --- | --- |
| DB 준비 | `backend/app/migrations/runner.py` | weather snapshot, weather point, worker lease 테이블을 만든다. |
| Open-Meteo 요청·검증 | `backend/app/nowcast/adapters.py` | API를 호출하고 DNI·구름량·시간 구간·좌표를 검증한다. |
| 저장 | `backend/app/nowcast/repository.py` | 중복 응답을 피하고 snapshot을 SQLite에 저장한다. |
| worker 실행 | `backend/app/nowcast/open_meteo_worker.py` | 저장된 Scene의 고유 위치마다 예보를 한 번 수집한다. |
| DNI 선택 | `backend/app/services/sunpath.py`, `backend/app/nowcast/selection.py` | Scene 날씨, snapshot, clear-sky 순서로 timestamp별 최종 DNI를 정한다. |
| 타일 계산 | `engine/fr01/predictor.py` | 햇빛 경로와 최종 DNI를 결합해 타일별 직접일사량을 계산한다. |
| API 반환 | `backend/app/schemas/sunpath.py`, `backend/app/services/sunpath.py` | DNI 출처와 타일별 결과를 FR-01 API 응답으로 만든다. |

API 요청은 Open-Meteo HTTP 호출이나 worker 실행을 하지 않는다. 이미 저장된 snapshot만 읽으므로, 외부 API가 일시적으로 실패해도 각 timestamp에서 clear-sky fallback으로 응답할 수 있다.

## 실행과 검증

모든 명령은 repository root에서 virtual environment를 활성화한 뒤 실행한다. Open-Meteo 수집과 실연동 smoke test에는 인터넷 연결이 필요하다.

```powershell
pip install -r requirements.txt
$env:PYTHONPATH = "backend"

# 운영 DB에 저장된 Scene 위치들의 예보를 한 번 수집한다.
python -m app.nowcast.open_meteo_worker

# 실제 Open-Meteo 응답을 이용하되, 별도 임시 SQLite DB에서 전체 흐름을 확인한다.
python tools/run_fr01_4_open_meteo_integration.py

# 외부 네트워크 없이 실행되는 단위·API 회귀 검증이다.
python -m pytest -q tests/test_nowcast_open_meteo_worker.py tests/test_nowcast_persistence.py tests/test_nowcast_selection.py tests/test_sunpath_api.py
```

smoke test는 `artifacts/local/fr01_4_open_meteo/<run-id>/`에 임시 SQLite DB와 `summary.json`을 만든다. 이 파일과 raw provider 응답은 Git에 올리지 않는다. worker의 정기 실행 주기, 재시도, 데이터 보존 기간, 장애 알림은 배포 환경에서 정한다.

## 검증 기준

- Open-Meteo hourly timestamp가 올바른 `[start, end)` 한 시간 구간에 적용되는지
- DNI `0`은 유효값으로 유지되고, `null`·잘못된 값·오래된 값은 fallback 되는지
- 같은 시간 구간의 여러 snapshot에서 정해진 우선순위가 유지되는지
- 일부 timestamp는 Open-Meteo, 다른 timestamp는 clear-sky를 사용할 수 있는지
- API 요청이 Open-Meteo를 새로 호출하지 않고 저장된 snapshot만 읽는지
- 타일별 직접일사량이 햇빛 경로·보호면 방향·선택된 DNI를 함께 반영하는지
