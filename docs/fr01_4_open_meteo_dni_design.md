# FR-01.4 — Open-Meteo 단일 source 미래 DNI 설계

> **현행 기준 설계:** 이 문서는 FR-01.4의 구현·운영 기준이다. 기존 다중 source
> Stage 2 설계는 [**폐기된 설계 문서**](deprecated_fr01_4_nowcast_implementation_guide.md)에 보존한다.

## 1. 목적과 경계

SunSync는 건물 위치의 지역 단위 DNI 예보를 태양 위치, 창문 방향, 실내 ray tracing,
보호면 입사각에 결합해 미래의 직달일사량을 계산한다. Open-Meteo DNI는 특정 층 또는
창문에서 직접 측정한 값이 아니다. 층 높이·주변 차폐·창문 방향·실내 보호 대상의 차이는
FR-01 기하 계산이 담당하고, 기상 source는 지역 DNI만 제공한다.

이번 설계는 여러 외부 source를 가중 평균하거나 정확도를 주장하지 않는다. 운영 DNI
source는 Open-Meteo 하나로 제한하며, 신뢰할 수 없는 경우 pvlib clear-sky로 fallback한다.
GK2A는 수집·변환 실험을 보존하되 운영 prediction 후보가 아니다.

```text
Scene.weather (유효할 때만)
  → durable Open-Meteo hourly DNI snapshot
  → pvlib clear-sky DNI
  → unknown (계산 실패 시)
```

`engine/fr01`은 순수 태양·기하 계산 계층으로 유지한다. HTTP, SQLite, provider parsing은
backend adapter/worker가 담당하며, API request는 외부 HTTP 또는 worker를 직접 실행하지
않고 저장된 snapshot만 읽는다.

## 2. 데이터 모델과 시간 의미

### 2.1 Open-Meteo DNI

요청은 `hourly=direct_normal_irradiance,cloud_cover`, `timeformat=unixtime`이며 `models`는
지정하지 않는다. 따라서 모델 정책은 `best_match`다. `direct_normal_irradiance`는 W/m²의
**직전 1시간 평균**이며, time label은 적용 구간의 종료 시각이다.

```text
record.valid_time = 14:00Z
record.interval   = [13:00Z, 14:00Z)

13:00, 13:05, …, 13:55 → 14:00 record
14:00                  → 15:00 record
```

이 값은 instantaneous observation으로 재해석하거나 5분·10분 값으로 선형 보간하지 않는다.
예측 timestamp가 interval에 포함되면 해당 한 시간 평균 DNI를 적용한다. 모든 내부 timestamp는
timezone-aware UTC instant다.

### 2.2 요청 위치와 예보 격자 위치

두 위치는 반드시 구분한다.

| 구분 | 의미 | 사용처 |
| --- | --- | --- |
| requested latitude/longitude | Scene의 건물 위치 | 태양 위치 및 FR-01 기하 계산 |
| grid latitude/longitude/elevation | Open-Meteo 응답 최상위 provider grid center | DNI provenance·공간적 대표성 설명 |

Open-Meteo가 Best Match로 실제 선택한 모델을 응답에서 신뢰성 있게 식별할 수 없으면
`resolved_model="unknown"`으로 기록한다. 격자 중심 좌표만으로 모델이나 공간 해상도를
추정하지 않는다.

### 2.3 Snapshot 보존 정보

각 immutable Open-Meteo snapshot은 다음을 보존한다.

- request site와 provider grid location/elevation
- `model_selection="best_match"`, `resolved_model`
- `fetched_at`, raw payload hash, quality status
- 각 point의 DNI, cloud cover, `valid_time`, `interval_start`, `interval_end`, temporal meaning

`weather_point.latitude_deg`/`longitude_deg`는 새 snapshot부터 provider representative grid
location을 뜻한다. 요청 좌표는 snapshot provenance에 보존한다. 기존 snapshot은 요청 좌표를
point에 저장했으므로 legacy spatial provenance로 구분하거나 새 candidate에서 제외한다.

## 3. 선택·freshness·fallback

각 prediction timestamp는 독립적으로 처리한다.

1. `Scene.weather`가 단위·coverage·freshness·시간 의미 검증을 통과하면 사용한다.
2. 그렇지 않으면 request 수신 시각 이전에 수집된 Open-Meteo snapshot 중 다음 조건을 만족하는
   point를 찾는다.
   - `quality_status="valid"`
   - DNI가 finite nonnegative (`0`은 유효)
   - `fetched_at <= request_received_at`
   - provider freshness TTL 이내
   - `interval_start <= timestamp < interval_end`
3. 같은 후보가 여럿이면 최신 `fetched_at`, 이어서 stable snapshot ID 순으로 선택한다.
4. 후보가 없거나 stale/invalid/null이면 해당 timestamp만 pvlib clear-sky로 fallback한다.
5. clear-sky 계산도 실패한 경우만 `unknown`을 반환한다.

미래 `valid_time`은 freshness 판정 기준이 아니다. freshness는 예보를 **언제 수집했는지**인
`fetched_at`으로 판단한다. 이 규칙은 request 시점 이후에 수집된 예보를 과거 prediction에
사용하는 미래 정보 누수를 막는다.

부분 fallback은 정상 동작이다.

```text
13:00~13:55 → Open-Meteo snapshot
14:00~14:55 → clear-sky fallback
```

한 timestamp가 fallback이라고 전체 응답을 v0.2로 낮추지 않는다. 해당 prediction은 v0.3
provenance를 계속 제공한다.

## 4. API v0.3 provenance

v0.2의 기존 fields는 삭제하거나 의미를 바꾸지 않는다. v0.3은 `irradiance[]` entry마다 실제
선택 근거를 additive하게 기록한다.

| 정보 | 규칙 |
| --- | --- |
| `source` | `scene_weather`, `open_meteo`, `pvlib_clear_sky`, `unknown` |
| snapshot ID / source snapshots | durable Open-Meteo snapshot 사용 시에만 기록 |
| `valid_time`, `interval_start`, `interval_end` | Open-Meteo hourly mean의 실제 적용 구간 |
| `fetched_at` | 선택 snapshot의 수집 시각 |
| request/grid coordinates | 건물 위치와 provider grid 위치를 명시적으로 분리 |
| model selection | `best_match`; 식별 불가 model은 `unknown` |
| fallback | timestamp 단위의 `fallback_used`, reason |
| clear-sky DNI | 해당 timestamp의 실제 pvlib 계산값 |
| clear-sky index | `final_dni / clear_sky_dni`; 분모가 0 또는 unknown이면 `null` |

`clear_sky_dni_W_m2=최종 DNI`, `clear_sky_index=1.0` 같은 placeholder는 금지한다.
`nowcast_run`은 선택된 durable snapshot과 timestamp별 결과를 추적하기 위한 기록이며, FR-04를
직접 호출하거나 최종 필름 상태를 결정하지 않는다.

## 5. 구현 단계와 완료 기준

### A. 공통 snapshot 기반 — 완료

- SQLite immutable `weather_snapshot` / `weather_point`, lease, retention, hash dedup 구현
- Open-Meteo worker와 site dedup 구현
- API request와 collection worker 분리

### B. GK2A point DSR 수집 — 보존, 운영 제외

- authenticated `nph_sun_sat_txt` point DSR parser와 24시간 분할 수집을 보존
- DSR은 GHI 성격이며 point API에는 DQF가 없어 운영 DNI 후보가 아니다
- provider representative location은 보존하되, grid resolution 검증은 미완료

### C. GK2A SWRAD DSR→DNI — 보존, 운영 제외

- DQF, DSR 범위, 태양천정각, DISC, clear-sky clip gate를 보존
- KO SWRAD의 companion latitude/longitude grid 검증 전 production collection은 금지
- 이 결과는 Open-Meteo와 혼합·가중평균하지 않는다

### D. Open-Meteo interval projection과 provenance — 완료

1. adapter가 response grid metadata와 Best Match policy를 snapshot provenance에 저장한다.
2. selector가 exact `valid_time` 비교 대신 hourly interval containment를 사용한다.
3. selector는 Open-Meteo만 조회하고 timestamp별 partial fallback을 생성한다.
4. clear-sky DNI/index를 실제 값으로 계산한다.
5. v0.3 DTO/API에 interval·spatial·model provenance를 additive하게 노출한다.
6. legacy snapshot의 spatial meaning을 안전하게 구분한다.

### E. GK2A cloud-state extension — 향후 별도 작업

상세 gate와 재도입 조건은 `docs/future_extensions/GK2A_cloud_state.md`에 기록한다. 이 작업은
Open-Meteo DNI 운영 경로와 독립적이며, 검증 전에는 production selection에 연결하지 않는다.

### F. GK2A experimental package 분리 — 완료

> **상태:** GK2A worker·SWRAD adapter·derivation은 `nowcast/experimental/gk2a/`로 격리됐다.
> Open-Meteo production 경로는 해당 package를 import하거나 조회하지 않는다.

현재 `nowcast` 최상위에는 Open-Meteo adapter와 GK2A point/SWRAD 실험 코드가 함께 있다.
GK2A를 보존하되 운영 source가 아니라는 사실을 코드 구조에서도 드러내기 위해 아래 구조로
이동하는 것을 목표로 한다.

```text
backend/app/nowcast/
  adapters.py                 # RawPoint, RawSnapshot, SnapshotAdapter 등 공통 DTO
  repository.py               # immutable snapshot, lease, retention
  sites.py                    # persisted Scene site discovery

  open_meteo/
    adapter.py
    worker.py
    selection.py

  experimental/
    gk2a/
      point_dsr_adapter.py    # Step B
      point_dsr_worker.py     # Step B
      swrad_dni_adapter.py    # Step C
      swrad_dni_worker.py     # Step C
      derivation.py           # DSR → DNI 실험
```

리팩터링 시에는 다음 원칙을 지킨다.

1. 공통 DTO와 repository는 provider package 밖에 남긴다.
2. Open-Meteo production selector는 `experimental.gk2a`를 import하거나 조회하지 않는다.
3. GK2A의 `KMA_API_KEY`, 수동 worker, integration fixture는 experimental package와 test namespace로
   함께 이동한다.
4. import 경로 변경 후 Open-Meteo worker·D selector·API request가 GK2A worker를 실행하지 않는지
   regression test로 보장한다.
5. `docs/future_extensions/GK2A_cloud_state.md`의 재도입 gate를 통과하기 전에는 package 이동만으로
   GK2A를 production source로 승격하지 않는다.
6. 대규모 rename이므로 D의 interval/provenance 변경과 같은 기능 변경에 섞지 않는다.

완료 기준은 기존 GK2A parser/derivation test와 Open-Meteo 운영 경로의 전체 회귀가 통과하고,
production API dependency graph에 GK2A experimental worker가 남지 않는 것이다.

## 6. 검증 계획

- 5분·10분·1시간 grid 각각에서 interval mapping 검증
- `[start,end)` 경계: 13:55는 14:00 record, 14:00은 15:00 record
- `DNI=0`, null, invalid, stale, future `fetched_at` 검증
- 같은 interval 후보의 newest fetched-at / stable snapshot-ID tie-break 검증
- 일부 Open-Meteo·일부 clear-sky인 v0.3 response 검증
- response grid location/elevation과 requested location이 분리 저장되는지 검증
- `clear_sky_index`의 정상, 야간/분모 0, unknown 검증
- API request가 provider HTTP 또는 worker를 직접 호출하지 않는지 검증
- 기존 FR-01 태양 위치, ray tracing, direct irradiance 및 v0.2 응답 회귀 검증

## 7. 운영 원칙

- Open-Meteo worker는 별도 process에서 주기 실행한다.
- provider 실패는 invalid snapshot을 만들지 않고 마지막 정상 snapshot을 보존한다.
- snapshot retention은 prediction horizon과 재현·감사 요구를 충족해야 한다.
- API는 worker 실패를 5xx로 전파하지 않으며 timestamp별 fallback을 사용한다.
- FR-04는 DNI 결과를 비동기로 소비할 수 있으나 hysteresis, hold time, 필름 상태 결정은
  FR-04의 책임이다.

### 7.1 임시 worker 운영 계획 (배포 전 확정 필요)

> 이 절은 구현 완료된 worker를 실제 운영에 연결하기 위한 **임시 계획**이다. 배포 플랫폼, FR-01의
> 최대 예측 horizon, 관측·알림 체계가 확정되면 실행 주기와 수치 기준을 재검토하여 갱신한다.
> API request는 이 계획의 어떤 단계에서도 worker를 직접 실행하거나 Open-Meteo에 HTTP 요청하지 않는다.

#### 실행 주기와 실행 주체

- scheduler가 `backend/`를 working directory로 하거나 `app` package를 설치한 환경에서
  `python -m app.nowcast.open_meteo_worker --database-url <운영 DB URL>`을 별도 process로 실행한다.
  API process와 worker process는 독립적으로 배포·재시작할 수 있어야 한다.
- 배포 형태별 후보는 다음과 같다. 하나만 운영 표준으로 확정하며, 둘 이상을 동시에 같은 DB에
  등록하지 않는다.

  | 배포 환경 | 실행 주체 후보 | 비고 |
  | --- | --- | --- |
  | Windows 기반 개발/온프레미스 | Windows Task Scheduler | 전용 서비스 계정, 작업 이력과 실패 코드 기록 |
  | Docker/Kubernetes | CronJob | API deployment와 분리된 Job, 동시 실행 제한 설정 |
  | VM/리눅스 서버 | `cron` 또는 systemd timer | 운영 계정, 표준 출력·오류의 중앙 수집 설정 |

- 정확한 실행 간격은 배포 전에 정한다. 간격은 Open-Meteo forecast 갱신 특성, `OPEN_METEO_FRESHNESS_SECONDS`,
  FR-01 최대 예측 horizon 및 허용 가능한 fallback 비율을 함께 고려해야 한다.
- repository의 lease는 중복 실행을 방지하는 보조 장치다. scheduler 설정에서도 이전 실행이 끝나기 전에
  새 실행을 중첩하지 않도록 제한한다.

#### Snapshot 보존과 예보 범위

- 운영 retention은 최소한 **FR-01 최대 예측 horizon + worker 실행 간격 + 장애·재시도 여유**보다 길게
  정한다. 재현·감사에 필요한 기간이 더 길면 그 기간을 우선한다.
- worker가 새 snapshot을 저장하면 API는 요청 시점 이전에 수집된 유효 snapshot 중 timestamp를 포함하는
  것을 선택한다. 새 snapshot이 있어도 과거 prediction의 근거 snapshot을 임의로 덮어쓰지 않는다.
- provider가 제공하는 forecast horizon 자체를 벗어나거나, 유효 snapshot이 없거나 stale이면 API는
  timestamp별 clear-sky fallback을 사용한다. fallback은 worker 즉시 실행 신호가 아니다.

#### 실패, 재시도, 로그와 알림

- 일시적 DNS·timeout·5xx 오류에는 scheduler 또는 worker wrapper에서 제한된 횟수의 exponential-backoff
  재시도를 적용한다. 재시도가 끝나도 invalid snapshot은 저장하지 않는다.
- 실행마다 시작·종료 시각, 실행 주체, 대상 site 수, 저장/중복/실패 수, 오류 분류, exit code를 구조화 로그로
  남긴다. raw provider payload와 민감한 환경 변수 값은 로그에 남기지 않는다.
- 연속 실패 횟수, 마지막 정상 snapshot의 age, fallback 비율의 임계값은 배포 전 확정하고, 초과 시 운영
  알림 채널에 알린다. 알림 수신자와 채널은 배포 환경의 관측 도구에 맞춰 정한다.

#### 운영 DB와 환경 변수

- worker와 API는 **동일한 운영 DB**를 사용해야 한다. 개발용 `sunsync.db`, 로컬 검증 artifact DB와
  운영 DB를 혼용하지 않는다.
- DB 연결 정보는 scheduler의 secret/environment configuration 또는 배포 플랫폼의 secret store로 제공한다.
  CLI에서는 `--database-url`로 명시적으로 전달한다. 연결 문자열이나 credential은 repository와 로그에
  저장하지 않는다.
- Open-Meteo production 수집에는 인증 API key가 필요하지 않지만, HTTP timeout·proxy·TLS 정책처럼
  배포 환경에 따라 달라지는 설정은 환경 변수 또는 배포 설정으로 관리할 수 있다. 이름과 기본값은
  도입 시 별도 운영 runbook에 확정한다.

#### 배포 환경 smoke test

- 배포 직후와 provider 응답 형식·수집 주기 변경이 의심될 때, 운영 DB와 분리된 test site/DB에서
  `tools/run_fr01_4_open_meteo_integration.py`와 동등한 smoke test를 실행한다.
- smoke test는 실제 Open-Meteo 응답이 snapshot/provenance/interval로 저장되는지, API가 provider HTTP나
  worker를 호출하지 않고 snapshot만 선택하는지, 범위 밖 timestamp가 clear-sky fallback 되는지를 확인한다.
- smoke test의 DB, raw 응답, 로그는 Git에 올리지 않는다. 실행 시각, 배포 버전, 결과 요약과 실패 분류만
  운영 기록에 보관한다.

배포 대상과 수치 기준이 확정되면 실행 명령, 주기, retention 값, retry/alert 임계값, secret 이름을
`docs/fr01_4_open_meteo_operations.md`에 운영 runbook으로 분리한다.
