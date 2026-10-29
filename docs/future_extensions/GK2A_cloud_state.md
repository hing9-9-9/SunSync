# GK2A 관측 기반 구름 상태 확장 — 향후 설계

> **상태:** 운영 DNI source가 아니다. 현재 기준 설계인
> [Open-Meteo 단일 source 미래 DNI 설계](../FR-01.4_open_meteo_dni_design.md)와 독립적으로
> 검증·개발한다.

## 목적

GK2A DSR을 Open-Meteo DNI와 가중 평균하지 않는다. 이 확장은 검증된 위성 관측으로 현재
구름 상태를 분류하고, 장래에만 단기 prediction 보정 가능성을 평가하기 위한 별도 기능이다.

## 확인된 입력

- point DSR API: 약 10분 timestamp, DSR은 GHI 성격, point 응답에는 DQF 없음
- KO SWRAD NetCDF: 2 km grid, DSR scale/fill value, `DSR_DQF1`, `SW_DQF` 존재
- SWRAD 원본에는 pixel latitude/longitude 배열이 없으므로 companion KO coordinate grid 필요

## 재도입 gate

1. companion latitude/longitude grid를 실제 SWRAD grid와 같은 projection·shape·해상도로 검증한다.
2. site-to-pixel mapping, pixel representativeness, frame timestamp 및 제공 지연을 검증한다.
3. DQF의 usable value와 fill/mask 규칙을 실제 파일로 확정한다.
4. DSR의 시간 의미를 검증하고 pvlib clear-sky GHI와 비교 가능한 interval을 정의한다.
5. 독립 관측 기준으로 DSR·DNI derivation 및 cloud-state 분류 정확도를 평가한다.
6. 관측 누락, 저태양고도, 저대비, geometry failure를 모두 safe fallback으로 처리한다.

## 후보 분석

검증된 DSR과 동시각 pvlib clear-sky GHI가 있을 때만 다음 지수를 산출할 수 있다.

```text
clear_sky_index = clip(DSR / clear_sky_GHI, 0, upper_bound)
```

이 값은 임계값 기반 맑음·부분 흐림·흐림 상태 분류의 입력 후보이며, 임계값과 품질 평가는
실제 관측 데이터로 확정한다. 이 문서의 지수는 Open-Meteo DNI 기반 `clear_sky_index`와
물리량·용도가 다를 수 있으므로 혼용하지 않는다.

## 운영 재결정 기준

GK2A를 미래 DNI 운영 source 또는 advection 입력으로 추가하려면, 시간 정렬·공간 매핑·DQF·
정확도·fallback behavior를 문서화하고 Open-Meteo 단일 source 기준선 대비 유효성을 입증해야
한다. 그 전까지 GK2A worker/adapter는 수동 검증 또는 분리 배치로만 사용한다.
