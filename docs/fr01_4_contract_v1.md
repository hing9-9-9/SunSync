# SunSync FR-01.4 상세 개발 계약 v1.2 — 역사적 계약

> **폐기됨:** 이 문서는 이전의 다중 source Stage 2 계약 기록이다. 현행 구현·운영 기준은
> [FR-01.4 Open-Meteo 단일 source 미래 DNI 설계](fr01_4_open_meteo_dni_design.md)다.
> 새 기능, API 계약 또는 검증 기준은 이 문서를 기준으로 추가·변경하지 않는다.

## 목적과 범위

FR-01.4는 기존 FR-01.1~FR-01.3의 기하학적 직사광선 경로 결과에 DNI(Direct Normal Irradiance)를 결합하여, 보호면 sample별 직접일사량을 제공한다. FR-02는 이 결과를 입력으로 셀별 불투명도를 결정한다. **FR-01.4는 FR-02의 셀 매핑·등급 판단·불투명도 계산을 수행하지 않는다.**

Stage 1은 필수이며, 유효한 `Scene.weather`를 우선 사용하고 Open-Meteo 예보, pvlib clear-sky DNI 순으로 대체한다. Stage 2는 **다중 source 기반 미래 DNI 예측 엔진**이다. 최근 관측·위성·초단기예보와 일반 예보를 시간 범위별로 결합하여 미래 DNI를 갱신한다. AI는 이후 정확도 개선을 위한 선택 사항이며, Stage 2의 선행 조건이 아니다.

### 문서 역할과 FR-01 경계

| 문서 | 범위 |
| --- | --- |
| `docs/fr01_design.md`, `docs/fr01_implementation_guide.md` | FR-01.1~FR-01.3 기하학적 햇빛 경로 MVP. DNI·weather는 최초 범위에서 명시적으로 제외했다. |
| `docs/fr01_4_contract_v1.md` (본 문서) | FR-01.4 확장. Stage 1의 DNI 선택·직접일사량과 Stage 2의 다중 source 미래 DNI 예측 계약을 정의한다. |
| `docs/deprecated_fr01_4_nowcast_implementation_guide.md` | **폐기된** 다중 source Stage 2 설계 기록이다. 현행 구현 기준은 `docs/fr01_4_open_meteo_dni_design.md`다. |
| `docs/fr01_api.md` | 현재 구현된 Stage 1 API v0.2.0 계약. Stage 2 v0.3.0은 본 문서의 향후 API 확장을 따른다. |

## 현행 계약 및 코드와의 정합성

- API 경로와 request는 유지한다: `POST /api/scenes/{scene_id}/sunpath-predictions`, `SunpathPredictionRequest`.
- 응답 DTO는 `SunpathPredictionResponse`이며, 기존 `solar_positions[]`, `regions[]`, `RegionPredictionResponse.timeline[]`, `RegionTimeResponse.samples[]`, `SampleResultResponse`의 계층과 기하 필드를 유지한다.
- Stage 1 응답은 `result_version="0.2.0"`이다. 현행 `0.1.0` 기하 전용 응답의 기존 필드는 삭제하거나 의미를 바꾸지 않고 additive하게 확장한다. `exposure_basis`는 `geometric_direct_path`, `optics_applied`는 `false`를 유지한다.
- 계산 엔진 `engine/fr01`은 HTTP·DB·외부 source를 읽지 않는 순수 계산 계층으로 남긴다. backend adapter/service가 source 수집, snapshot, provenance, nowcast 결합을 담당한다.
- FR-06 `Weather`/`WeatherRecord`의 `dni_W_m2`, `valid_time`, `fetched_at`, `forecast_reference_time`, `temporal_meaning`, `interpolation`, `max_age_s` 의미를 재사용한다. `null` DNI는 **미상**이고 `0`은 유효한 물리값이다.

### 기존 문서와의 충돌 및 해소

| 항목 | 기존 상태 | v1.2 해소 |
| --- | --- | --- |
| FR-01 API | 현행 `docs/fr01_api.md`와 `SunpathPredictionResponse`는 Stage 1 DNI 선택·직접일사량을 포함한 `0.2.0`을 반환하며, Stage 2 nowcast는 아직 없다 | 본 문서가 Stage 2의 `0.3.0` additive 확장 계약을 정한다. 구현 시 API 문서와 DTO를 동기화한다. |
| `features.md`의 표현 | clear-sky 후 Open-Meteo로 대체한다고 읽힐 수 있음 | `decisions.md` 및 이 계약의 명시 우선순위: Scene.weather → Open-Meteo → clear-sky. |
| FR-06 weather | Stage 1 service가 `Scene.weather`를 선택·검증하지만, 다중 source snapshot 저장은 아직 없다 | Stage 2는 Scene을 수정하지 않고 별도 immutable snapshot을 수집·결합한다. |
| 시간 보간 | FR-06은 hourly mean 선형보간을 금지하지만 FR-01 API는 더 짧은 interval을 허용 | 본 계약이 hourly mean 구간 적용과 instant 전용 보간을 확정한다. |

## Stage 1 계약

### 날씨 선택과 요청 snapshot

1. 서비스는 요청 시작 시각에 저장된 Scene revision을 한 번 읽어 검증하고, 그 요청에서만 쓰는 불변 weather snapshot을 만든다. prediction은 Scene DB record나 `Scene.weather`를 갱신·저장하지 않는다.
2. timestamp별 DNI 우선순위는 다음과 같다.
   1. 요청 시작 시각에 fresh이고 해당 timestamp에 유효한 `Scene.weather` record
   2. 검증된 Open-Meteo forecast snapshot
   3. pvlib clear-sky DNI
3. `Scene.weather` 또는 Open-Meteo의 `dni_W_m2=0`은 선택 가능한 유효값이다. `null`, 비유한값, 음수, 단위/시간 의미 불일치, 범위 밖 값은 후보가 아니며 다음 source로 넘어간다.
4. freshness는 **prediction 요청을 서비스가 수신한 UTC 시각**을 기준으로 `fetched_at`과 `max_age_s`를 비교한다. `fetched_at`이 없거나 `request_received_at - fetched_at > max_age_s`이면 stale이다. 미래 `fetched_at`은 유효하지 않다.
5. HTTP 호출은 요청당 최대 한 번의 Open-Meteo 응답을 snapshot으로 고정한다. 모든 timestamp가 이 응답 또는 fallback에서 선택되며, 요청 중 재시도/재조회로 snapshot을 교체하지 않는다.

### 시간 의미와 DNI 선택

- 모든 계산 timestamp와 provenance timestamp는 timezone-aware UTC instant로 직렬화한다.
- `temporal_meaning="preceding_hour_mean"`인 record는 정확히 `[interval_start, interval_end)`에 적용한다. `interval_end == valid_time`, 구간 길이는 1시간이어야 하며 선형보간하지 않는다. 요청 timestamp가 이 구간에 없으면 그 record는 사용할 수 없다.
- `temporal_meaning="instant"`만 `interpolation="linear_estimate"`일 때 인접한 유효 instant 사이 선형보간을 허용한다. 경계 밖 extrapolation은 금지한다. `interpolation="none"`이면 timestamp와 정확히 일치하는 record만 사용한다.
- 하나의 timestamp에 겹치거나 상충하는 record가 있으면 해당 Scene.weather candidate는 invalid이며 Open-Meteo로 fallback한다. Scene schema가 보장하지 않는 coverage/overlap 검증은 Stage 1 service의 책임이다.

### 계산

- sample이 기하학적으로 `clear_path`이면 `direct_irradiance_W_m2 = final_dni_W_m2 × max(0, dot(receiving_normal, sun_direction_enu))`이다.
- 기하학적으로 비노출(`sun_below_horizon`, `back_facing`, 창 미교차 또는 blocker 포함)이면 DNI를 알 수 없어도 sample 직접일사량은 `0`이다.
- `clear_path`인데 최종 DNI가 미상이면 sample 직접일사량은 `null`이다.
- 창 유리/필름 투과, 반사, 굴절, 확산광 및 실외 차폐는 계산하지 않는다. 따라서 `optics_applied=false`를 유지하며 이는 투과 후 실내 조도 또는 FR-02 제어 성공을 뜻하지 않는다.

### API 응답 확장

`SunpathPredictionResponse` 최상위에 `irradiance[]`를 추가한다. 길이와 순서는 `timestamps[]`와 정확히 일대일이고, 각 entry의 `timestamp`는 같은 인덱스의 timestamp와 같아야 한다.

```json
{
  "result_version": "0.2.0",
  "timestamps": ["2026-10-17T01:30:00Z"],
  "irradiance": [
    {
      "timestamp": "2026-10-17T01:30:00Z",
      "baseline_dni_W_m2": 520.0,
      "final_dni_W_m2": 520.0,
      "source": "scene_weather",
      "fallback_used": false,
      "fallback_reason": null,
      "valid_time": "2026-10-17T01:30:00Z",
      "fetched_at": "2026-10-17T01:00:00Z",
      "ml_applied": false,
      "ml_reason": "stage2_not_enabled",
      "model_version": null
    }
  ]
}
```

- `source` enum: `scene_weather`, `open_meteo`, `pvlib_clear_sky`, `unknown`.
- `fallback_reason` enum: `scene_weather_missing`, `scene_weather_stale`, `scene_weather_invalid`, `scene_weather_out_of_coverage`, `open_meteo_timeout`, `open_meteo_transport_error`, `open_meteo_invalid_response`, `open_meteo_out_of_coverage`, `clear_sky_error`, `none`. 첫 source를 사용했으면 `null`; fallback을 거친 최종 source에는 원인을 기록한다.
- `valid_time`은 선택한 instant 또는 평균 구간의 `interval_end`; `fetched_at`은 외부/Scene snapshot의 취득시각이며 clear-sky에는 `null`이다. `baseline_dni_W_m2`와 `final_dni_W_m2`는 W/m² 또는 `null`이다.
- Stage 1 구현에 이미 존재하는 `ml_applied`, `ml_reason`, `model_version`은 v0.2.0 호환 필드로 유지할 수 있으나, Stage 2 v0.3.0에서는 nowcast provenance로 대체한다. Stage 1만 수행한 entry의 `prediction_method`는 `stage1_weather_selection`이다.
- 각 `SampleResultResponse`에 nullable `direct_irradiance_W_m2`를 추가한다. 값의 의미는 위 계산 절을 따른다.

### Open-Meteo adapter

공식 Forecast API 확인 결과 endpoint는 `https://api.open-meteo.com/v1/forecast`이며, WGS84 `latitude`, `longitude`와 `hourly` variable 목록을 받는다. 기본 hourly `direct_normal_irradiance`는 W/m²의 **직전 1시간 평균**이고, `cloud_cover`는 instant %이다. API의 ISO time은 고정 UTC offset/DST 처리 특성이 있으므로 adapter는 `timeformat=unixtime`을 요청하고 UTC aware timestamp로 정규화한다. 공식 명세는 historical forecast/single-run API를 과거 발행 forecast 학습용으로 안내한다. [Open-Meteo Forecast API](https://open-meteo.com/en/docs), [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api)

- Stage 1 기본 요청은 `hourly=direct_normal_irradiance,cloud_cover`, `latitude=Scene.site.latitude_deg`, `longitude=Scene.site.longitude_deg`, `timeformat=unixtime`이다. 반환 DNI는 `Weather.variable="direct_normal_irradiance"`, `temporal_meaning="preceding_hour_mean"`, `interpolation="none"`으로 정규화한다.
- adapter는 HTTP status, JSON 구조, 시간 배열 길이 일치, W/m² 단위, timestamp 단조성, DNI finite/nonnegative를 검증한다. 검증 실패와 네트워크 실패는 fallback reason을 남기고 clear-sky로 진행하며, 정상 prediction 전체를 실패시키지 않는다.
- **구현 정책(공식 강제 규칙 아님):** connect+read 총 timeout은 5초, retry는 하지 않는다. 이는 요청 snapshot 불변성과 지연 상한을 위한 MVP 정책이다.
- **미확정:** Open-Meteo의 `direct_normal_irradiance_instant`를 Stage 1에 사용할지, provider의 model 선택(`models`)을 고정할지, 15분 endpoint를 사용할지는 정하지 않았다. 기본 hourly 평균만 지원한다. 지역/모델별 variable 가용성과 Best Match 변경 가능성도 adapter 테스트에서 다뤄야 한다.

## Stage 2: 다중 source 기반 미래 DNI 예측 엔진

### 목적과 범위

Stage 2는 미래 차광 스케줄에 쓸 DNI를 현재부터 예측 범위 끝까지 갱신한다. 이는 Stage 1의 단일 timestamp source 선택을 대체하지 않고, Stage 1 결과·청천 DNI·최근 관측 및 예보를 결합하는 backend 계층이다. 창 밖 현장 센서는 이 계약의 범위 밖이다.

- 목표는 예보 오차의 사후 보정이 아니라, 구름 변화가 큰 환경에서 **0~6시간 미래 DNI의 최신성·연속성**을 높이는 것이다.
- 태양 위치, 실내 기하, 표면 법선 투영, FR-02의 보호 등급·차광 결정은 기존 결정론적 계약을 유지한다.
- 모든 Stage 2 실패는 Stage 1 선택 결과 또는 pvlib clear-sky로 fallback하며, prediction 요청 자체를 실패시키지 않는다.

### source와 snapshot 계약

각 provider 응답은 수신 즉시 원본 식별자/해시와 정규화된 snapshot으로 보존한다. snapshot은 불변이며, 예측 실행 중 재조회로 교체하지 않는다. source별 adapter는 다음 공통 필드를 만들어야 한다.

| 필드 | 의미 |
| --- | --- |
| `snapshot_id`, `provider`, `product`, `model` | source와 특정 자료 제품 식별 |
| `observed_at`, `issued_at`, `fetched_at`, `valid_time` | 각각 관측·발표·수신·예측 대상 UTC instant |
| `latitude_deg`, `longitude_deg`, `grid_resolution_km` | 값의 공간 대표성 |
| `dni_W_m2`, `ghi_W_m2`, `direct_horizontal_W_m2`, `cloud_cover_percent` | 제공 가능한 정규화 기상값. 없는 값은 `null` |
| `unit`, `temporal_meaning`, `interval_start`, `interval_end` | 단위와 instant/평균 구간 의미 |
| `quality_status`, `raw_payload_hash` | 검증 결과와 재현성 참조 |

초기 source 구성은 다음과 같다.

1. **기상청 천리안 2A**: 한반도 영역의 최신 구름·복사 산출물을 현재 상태와 0~60분 nowcast 입력으로 사용한다.
2. **기상청 초단기 실황/예보**: 현재 확인 및 1~6시간의 국내 단기 예보 입력으로 사용한다.
3. **Open-Meteo**: DNI와 cloud cover를 1시간 이후 예보 및 provider 장애 fallback으로 사용한다.
4. **pvlib clear-sky**: 모든 source가 불충분할 때의 물리적 상한·최종 fallback이다.

source의 값은 단순 평균하지 않는다. `observed_at`/`issued_at`/`fetched_at`이 없거나 미래이고, 좌표·단위·시간 의미가 맞지 않거나, 비유한·음수 DNI, 유효하지 않은 평균 구간이면 해당 값은 제외한다. 0 W/m²는 유효한 값이다.

### 정규화와 결정론적 nowcast

모든 후보는 같은 timestamp의 pvlib clear-sky DNI `C(t)`에 대한 청천지수로 정규화한다.

\[
k_j(t)=\operatorname{clip}\left(\frac{D_j(t)}{C(t)},0,1.2\right)
\]

`D_j`가 DNI이면 직접 사용한다. GHI 또는 surface shortwave만 제공되면, 해당 source의 시간 의미를 보존한 뒤 pvlib DISC/DIRINT 계열의 명시된 변환으로 DNI를 추정하며 `derived_from_ghi` provenance를 남긴다. direct horizontal irradiance가 있으면 태양 고도와 함께 DNI로 변환하되, 지평선 부근의 불안정한 변환은 제외한다.

최근 천리안 프레임 둘 이상에서 산출한 구름/청천지수 이동 벡터를 `v`라고 하면, AI 없이 사용하는 0~60분 advection nowcast는 다음과 같다.

\[
k_{\mathrm{adv}}(t+h,x)=k(t,x-vh),\qquad
D_{\mathrm{adv}}(t+h)=k_{\mathrm{adv}}(t+h,x)C(t+h)
\]

시간 범위별 결합은 아래 정책을 따른다. 가중치는 source의 freshness, 시간·공간 해상도, adapter 검증 품질 및 예측 lead time에서 결정한다.

| 미래 lead time | 우선 입력 | 결합 정책 |
| --- | --- | --- |
| 0~15분 | 천리안 최신 복사/구름, 기상청 실황 | 최신 관측·advection nowcast 우선 |
| 15~60분 | 천리안 advection, 기상청 초단기예보 | lead time 증가에 따라 예보 비중을 높임 |
| 1~6시간 | 기상청 초단기예보, Open-Meteo | 국내 초단기예보 우선, 검증된 Open-Meteo 보조 |
| 6시간 이후 | Open-Meteo | Stage 1과 같은 예보/fallback 정책 |

후보 비율의 결정론적 결합은 다음과 같다.

\[
w_j=w_{\mathrm{fresh}}w_{\mathrm{temporal}}w_{\mathrm{spatial}}w_{\mathrm{quality}},\qquad
k_{\mathrm{final}}(t)=\frac{\sum_jw_jk_j(t)}{\sum_jw_j},\qquad
D_{\mathrm{final}}(t)=k_{\mathrm{final}}(t)C(t)
\]

가용 후보가 하나면 해당 후보만 사용하고, 없으면 Stage 1 값, 마지막으로 clear-sky를 사용한다. 급격한 source 튐은 최근 3~5개 결과의 median 또는 EWMA로 완화한다. 이 smoothing은 FR-04의 hysteresis와 minimum hold time을 대체하지 않으며, 최종 필름 상태 안정화는 FR-04 책임이다.

### API provenance 확장

Stage 2 구현 시 응답 버전은 `0.3.0`으로 올린다. `irradiance[]`의 기존 Stage 1 필드는 유지하고 다음을 additive하게 추가한다.

```json
{
  "prediction_method": "deterministic_nowcast",
  "nowcast_horizon_seconds": 1800,
  "clear_sky_dni_W_m2": 780.0,
  "clear_sky_index": 0.42,
  "source_snapshots": [
    {"snapshot_id": "...", "provider": "kma_gk2a", "role": "advection", "weight": 0.72},
    {"snapshot_id": "...", "provider": "kma_ultra_short", "role": "forecast", "weight": 0.28}
  ],
  "fallback_reason": null
}
```

- `prediction_method` enum은 `stage1_weather_selection`, `deterministic_nowcast`, `forecast_fusion`, `clear_sky_fallback`이다.
- `source_snapshots[]`는 실제 기여한 snapshot만 포함한다. source 간 값이 불일치해 제외된 snapshot은 diagnostics에만 남기며 최종 가중치에 포함하지 않는다.
- Stage 1의 `source`는 호환성을 위해 최종 기여도가 가장 큰 provider를 나타내고, 다중 source의 완전한 출처는 `source_snapshots[]`가 권위 있는 기록이다.
- `final_dni_W_m2`는 항상 이 계약의 최종 `D_final`이며, sample별 직접일사량 계산식은 Stage 1과 동일하다.

## 구현 작업과 완료 기준

### Stage 1

1. `SunpathPredictionResponse`, `RegionTimeResponse`/`SampleResultResponse`의 v0.2.0 DTO와 API 문서를 위 응답 계약으로 확장한다.
2. request-scoped weather snapshot selector, Scene.weather coverage/freshness validator, Open-Meteo adapter, pvlib clear-sky fallback을 backend service에 구현한다.
3. 순수 FR-01 engine에 DNI-법선 투영 계산을 추가하고 HTTP/DB 의존성은 추가하지 않는다.
4. 완료 기준: 기존 FR-01/FR-06 회귀 테스트 통과; timestamp/irradiance 1:1; 0과 null 구분; Scene 우선·stale/null/out-of-coverage fallback; Open-Meteo timeout/transport/invalid JSON; hourly `[start,end)` 경계; instant 보간 규칙; 기하 비노출 0·clear path unknown null; snapshot 불변 및 DB 무변경을 mock HTTP로 검증한다.

### Stage 2

1. 기상청 천리안 2A, 기상청 초단기, Open-Meteo adapter와 versioned immutable snapshot 저장소를 만든다. provider 수집은 예측 요청과 분리된 주기 작업으로 수행하며, 요청은 저장된 최신 검증 snapshot만 읽는다.
2. source별 시간·공간 좌표·단위·평균 구간 검증, DNI/청천지수 정규화, GHI→DNI 변환 provenance를 구현한다.
3. 천리안 연속 프레임의 이동 벡터 기반 advection nowcast, lead time별 deterministic fusion, Stage 1/clear-sky fallback을 구현한다.
4. v0.3.0 `irradiance[]` provenance와 API 문서를 확장하고, FR-04가 소비할 미래 DNI grid의 갱신 주기·유효기간을 명시한다.
5. 완료 기준: source별 snapshot 불변성·freshness·시간 의미 검증, 0과 null 구분, 위성 프레임 누락/이동 벡터 실패 fallback, lead time별 source 가중치, GHI→DNI provenance, `timestamps[]`/`irradiance[]` 1:1, source 장애에서 Stage 1/clear-sky fallback, smoothing과 FR-04 역할 분리를 모두 mock adapter로 검증한다.

## v1.2 이후 미확정 사항

- 기상청 초단기 API의 인증·호출 한도와 production 운영 계정 정책
- Open-Meteo instant DNI·15분 데이터 채택 및 특정 `models` 고정 여부
- FR-04 구현 시 사용할 schedule DTO의 세부 형식과 소비 주기

## 추가 논의 및 확장 가능성: AI residual 보정

AI residual 보정은 Stage 2의 필수 구성요소가 아니다. 충분한 snapshot 이력과 신뢰할 수 있는 reference DNI가 확보된 뒤, `reference_DNI - forecast_DNI`를 학습해 결정론적 nowcast 결과를 보조 보정하는 확장으로만 검토한다. 모델은 미래 정보 누수 없이 시간순으로 평가하고, 성능·입력·artifact 검증에 실패하면 결과를 변경하지 않고 deterministic fusion으로 fallback해야 한다.
