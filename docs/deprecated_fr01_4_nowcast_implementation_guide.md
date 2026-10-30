# **폐기된 설계** — FR-01.4 Stage 2 다중 source 미래 DNI 구현 계획

> **폐기 상태:** 이 문서는 Open-Meteo와 GK2A DNI를 exact `valid_time`으로 비교·선택하는
> 이전 설계를 보존하기 위한 기록이며, 더 이상 구현·운영의 기준이 아니다. 특히 5분·10분
> prediction grid와 Open-Meteo의 hourly mean이 맞지 않는 문제를 해결하지 못한다.
> 현재 기준 설계는 [FR-01.4 Open-Meteo 단일 source DNI 설계](fr01_4_open_meteo_dni_design.md)다.

> **상위 계약:** [FR-01.4 상세 개발 계약](fr01_4_contract_v1.md)
>
> **계획 상태:** 2026-10-27 개정. 이 문서는 구현 순서와 gate를 고정한다. 상위 계약의
> source 우선순위·advection 가중치 표는 아래 충돌 사항을 해소하는 계약 개정 전까지
> 구현의 근거가 아니다.

> **용어:** 제목의 **Stage 2**는 FR-01.4의 상위 미래-DNI 프로그램 단계를 뜻한다. 이 문서
> 내부의 구현 순서는 혼동을 피하기 위해 **Implementation Step A~F**로 표기한다.

## 1. 경계와 현재 상태

- `engine/fr01`은 태양 위치, 기하 노출, `DNI × cos(incidence)`만 계산하는 순수 계층이다.
  HTTP, SQLite, NetCDF, OpenCV를 import하지 않는다.
- 요청 경로는 외부 HTTP를 호출하지 않고 저장된 유효 snapshot/run만 읽는다. 이 계획의 모든
  worker 실패는 현행 v0.2(`Scene.weather → Open-Meteo → clear-sky`) 결과로 fallback하며
  요청 자체를 실패시키지 않는다.
- FR-04는 필름 상태의 hysteresis와 minimum hold time을 담당한다. 이 계획은 준비된 DNI
  결과를 저장·발행할 뿐 FR-04를 동기 호출하지 않는다.
- 기상청 초단기예보는 DNI/GHI를 직접 제공하지 않으므로 이번 계획에서는 보류한다.
  SKY·강수 범주로 DNI를 추정하거나 정확도를 개선한다고 주장하지 않는다.

| 범위 | 상태 | 비고 |
| --- | --- | --- |
| 현행 v0.2 API | **PASS** | 기존 계약·fallback을 유지한다. |
| 공통 저장 기반 | **PASS** | migration, immutable snapshot repository, retention, mock adapter, worker lease와 테스트가 구현됐다. |
| GK2A DSR 지점 API | **PARTIAL** | historical `int=10`/`int=2` 요청은 HTTP 200이며 실제 관측 간격은 10분으로 확인됐다. 최신 지연·응답 표의 실데이터 파싱은 로컬 API key로 재검증한다. |
| GK2A 2-D 격자/NetCDF | **PARTIAL** | KO SWRAD NetCDF 실파일과 2 km 투영·DQF는 확인했다. 위·경도 매핑과 site crop 검증이 남아 있다. |
| Implementation Step A — Open-Meteo DNI worker | **PASS** | 독립 worker, site dedup, hourly 평균 구간 정규화, lease와 실패 보존을 구현했다. |
| Implementation Step B — GK2A DSR point worker | **PARTIAL** | 가로 시간축 표 파서·worker와 24시간 분할 수집을 구현했다. 원본 KO NetCDF는 2 km 격자임을 확인했지만, 지점과 격자를 대응시키는 위·경도 파일은 아직 확보·검증하지 않았다. |
| Implementation Step C — DSR→DNI 추정 | **PARTIAL** | DISC 기반 순수 derivation, DQF-gated NetCDF adapter 및 derived snapshot worker를 구현했다. 실제 KO SWRAD에는 위·경도 배열이 없으므로, companion 좌표 파일을 확보·검증하기 전 production 수집은 안전하게 제외된다. |
| Implementation Step D — DNI 선택·provenance | **PASS** | 저장된 snapshot의 exact valid-time·freshness·quality를 결정론적으로 선택하고, ready run 및 additive v0.3 provenance를 반환한다. 후보가 하나라도 없으면 기존 v0.2 선택으로 fallback한다. |
| Implementation Step E — 2-D advection | **BLOCKED** | 2-D data gate가 선행 조건이다. |
| Implementation Step F — 통합 검증·FR-04 연계 | **BLOCKED** | Step D 및 FR-04 소비 계약이 선행 조건이다. |

## 2. 계약 및 기존 가이드와의 충돌

| 기존 표현 | 충돌 | 이 계획의 처리 |
| --- | --- | --- |
| 초기 구현 순서가 Open-Meteo 뒤 초단기예보, GK2A `SWRAD`/`CSR`, advection, fusion 순서 | 초단기예보는 DNI가 없고, DSR 지점 API 검증이 먼저 필요하다. | Step A~D를 Open-Meteo와 GK2A 지점 DSR 기반으로 재정렬하고 초단기예보는 보류한다. |
| `SWRAD / CSR`를 GK2A 청천지수로 사용 | 공식 DSR은 지표 하향 단파복사(GHI 성격)이고 CSR은 surface clear-sky irradiance로 검증되지 않았다. | Step C는 `DSR → DISC/DIRINT → DNI`만 실험한다. `DSR/CSR`와 그 advection은 채택하지 않는다. |
| GK2A를 2분 간격 crop/advection 입력으로 가정 | 지점 DSR API의 실제 생산 관측 간격은 10분이며 point API는 공간장(field)이 아니다. | Step B에서 지점 API의 실제 timestamp·nearest-grid 정밀도를 검증한다. Step E는 2-D source 확보 전 BLOCKED다. |
| lead time별 source 가중 평균 및 “fusion” | 실제 기준 관측 또는 교정 데이터가 없는 가중치는 정확도 개선을 입증하지 못한다. | Step C~D는 검증 가능한 freshness·quality·유효시간 선택만 수행한다. 임의 보정·가중평균은 도입하지 않는다. |
| 상위 계약의 초단기예보 우선표 | 초단기예보가 DNI를 직접 주는 것처럼 읽힐 수 있다. | 상위 계약은 후속 개정 대상으로 기록한다. 이번 구현은 현행 v0.2 fallback과 Open-Meteo/GK2A의 명시적 선택만 사용한다. |

`docs/fr01_4_contract_v1.md`와 `docs/fr01_api.md`는 현재 구현된 v0.2 계약을 설명한다.
v0.3 DTO/API를 실제로 도입하는 Step D에서 상위 계약과 API 문서를 additive 변경으로
동기화한다. 이번 문서 개정은 기능 계약 또는 API를 변경하지 않는다.

## 3. 환경변수와 인증키 관리

- 로컬 개발은 repository root의 `.env`를 `python-dotenv`로 자동 로드한다. 이미 설정된
  process environment가 우선하며 `.env`는 이를 덮어쓰지 않는다.
- `.env`에는 `KMA_API_KEY`만 설정한다. 키는 Step B worker와 로컬 DSR 검증 script에서만
  읽고, 코드·로그·테스트 fixture·DB·API 응답에는 절대 기록하지 않는다.
- `.env.example`은 변수명과 placeholder만 포함한다. `.gitignore`는 `.env`, `.env.*`를
  무시하고 `.env.example`만 추적한다.
- 현행 Open-Meteo endpoint는 public URL 상수이며 API key나 `.env` 값이 없다. 이 동작은
  유지한다. 향후 endpoint/model 설정을 환경변수화할 때에도 기존 기본값을 유지해야 한다.
- Git 추적 파일과 이력은 실제 secret 값을 출력하지 않는 탐지로만 점검한다. 발견 시 값은
  노출하지 않고 파일/commit 식별자와 대응 필요성만 보고한다.

## 4. Implementation Step A — Open-Meteo DNI snapshot worker

**목표:** 현행 request-scoped Open-Meteo 호출과 독립된 worker가 site별 DNI snapshot을
수집·정규화·저장한다.

- 범위: 저장된 유효 `SceneRecord`의 고유 site를 읽고, `direct_normal_irradiance`,
  `cloud_cover`, `timeformat=unixtime`을 요청한다. hourly DNI의 `preceding_hour_mean`
  시간 의미와 `[interval_start, interval_end)`를 보존한다.
- 선행 조건: 기존 snapshot repository, worker lease, Open-Meteo timeout/응답 검증 정책.
- 완료 기준: 별도 worker process가 중복 실행되지 않고 새 정규화 값만 durable snapshot으로
  저장한다. provider 실패는 snapshot을 invalid로 저장하지 않고 마지막 정상 snapshot을 유지한다.
- 테스트: frozen clock, site dedup, UTC/평균 구간, 0과 null 구분, hash 중복, timeout/invalid
  JSON, lease 경쟁, 현행 request 경로에 외부 호출이 없음을 검증한다.
- 상태: **PASS — 독립 worker와 unit test 구현 완료**.

## 5. Implementation Step B — GK2A 지점 DSR adapter

**목표:** 인증된 `nph_sun_sat_txt` 지점 API에서 서울을 포함한 site별 DSR을 안전하게
파싱하여 snapshot으로 저장한다.

- 범위: `varn=DSR`, `tm1`, `tm2`, `int=10`, `lat`, `lon` 요청; API 표의 `TM`/`TIME`과
  `DSR` 열을 명시적으로 파싱한다. API 오류 텍스트는 행으로 집계하지 않으며 0 W/m²는
  정상 값으로 보존한다.
- 공간 검증: API가 위경도와 정확히 같은 지점이 아니라 **가장 가까운 격자점**을 반환함을
  provenance에 기록한다. 실제 응답의 좌표/격자 식별자와 요청 site의 거리를 측정해
  `grid_resolution_km`과 representative location을 확정한다. API가 좌표를 반환하지 않으면
  이 단계는 PARTIAL이며 해당 한계를 provenance에 기록한다.
- freshness 검증: 최근 24시간을 한 요청으로 조회해 최신 `observed_at`과 응답 수신
  `fetched_at`의 지연을 기록한다. historical `int=2`가 10분 timestamp만 반환하는지를
  회귀 검증한다.
- 선행 조건: `KMA_API_KEY`가 worker 전용 runtime secret으로 제공되고 로그·DB·응답에
  노출되지 않아야 한다.
- 완료 기준: 실제 site에서 HTTP/auth/no-data/정상 0을 구분하고, timestamp·DSR 값·단위
  W/m²·결측·latest latency·격자 대표성을 저장/진단할 수 있다.
- 테스트: 실제 API는 opt-in integration test, unit test는 captured/redacted response fixture로
  표 파싱, 오류 행 배제, 0, 결측, 열 순서 변경을 검증한다.
- 상태: **PARTIAL — 2026-10-28 서울시청 mock 좌표·과거 구간에서 실제 가로 시간축 응답을 확보해 parser를 검증했다. 대표 좌표는 응답에서 받지만 단일 지점 응답으로 격자 해상도는 확정할 수 없다**.

### 5.1 확인된 응답 형식과 정규화 규칙

2026-10-28에 FR-01 mock의 서울시청 인근 좌표(`37.5665`, `126.9780`)와 과거 UTC
구간(`2026-10-17T01:30:00Z`~`03:30:00Z`)으로 받은 인증키 제거 원문은 로컬
`artifacts/gk2a_dsr/`에만 보존한다. DB/API/log에는 원문을 넣지 않는다. 응답은 세로
`TM`/`DSR` 행이 아니라 pipe 구분 가로 시간축 표다.

| 열 그룹 | 원문 의미 | 전처리 |
| --- | --- | --- |
| `TMFC` | 표 생성/기준 시각 | snapshot provenance에만 보존 가능하며 point valid time으로 쓰지 않는다. |
| `VAR` | 변수 식별자 | 정확히 `DSR`인 행만 허용한다. |
| `LON`, `LAT` | API가 반환한 대표 좌표 | point 좌표로 저장하고 요청 좌표와의 great-circle 거리를 provenance에 기록한다. |
| `YYYYMMDDHHMM` 열들 | UTC timestamp별 DSR 값 | 각 열을 `RawPoint(valid_time, ghi_W_m2)` 하나로 펼친다. |

예상 원문 구조는 `TMFC | VAR | LON | LAT | <UTC timestamp>...` 헤더와
`<reference time> | DSR | <longitude> | <latitude> | <value>...` 데이터 행이다. `int=10`과
`int=2`의 실제 timestamp는 모두 10분 간격이었다. 값 `0`은 `0.0 W/m²`로 유지하고
`-`, `NA`, `N/A`, `NULL`, `MISSING`은 `null`로 정규화한다. 시간 열은 증가 순서여야 하며,
음수/비유한 값, 누락 열, 중복 대표 좌표는 응답 전체를 invalid로 처리한다.

정규화 후 각 point는 다음 형태다.

```text
RawPoint(
  valid_time=<header UTC timestamp>,
  latitude_deg=<response LAT>, longitude_deg=<response LON>,
  ghi_W_m2=<DSR float or null>, temporal_meaning="instant"
)
```

이 endpoint는 출력 timestamp 수를 24개로 제한한다. 따라서 worker는 `int=10`에서
3시간 50분(양 끝 포함 24개)의 창만 한 번에 요청한다. 단일 응답으로 격자 해상도는 산정할
수 없어 `grid_resolution_km=null`을 유지한다.

### 5.2 KMA API 재현 절차와 요청 파라미터

모든 KMA 요청은 worker 또는 수동 진단 script에서만 수행하며, API 요청 경로에는 넣지
않는다. 인증키는 repository root `.env`의 `KMA_API_KEY` 또는 process environment에서만
읽고, 명령 출력·DB·fixture·문서에는 기록하지 않는다. 아래 timestamp는 모두 UTC다.

재검증의 출발점은 [기상청 API 허브의 GK2A L2 다운로드 명세](https://apihub.kma.go.kr/apiList.do?apiMov=2.+%EC%B2%9C%EB%A6%AC%EC%95%88+2A%ED%98%B8+%EA%B8%B0%EC%83%81%EC%82%B0%EC%B6%9C%EB%AC%BC+%EC%A1%B0%ED%9A%8C&seqApi=6&seqApiSub=271)와
[국가기상위성센터 SWRAD 제품 설명](https://nmsc.kma.go.kr/homepage/html/base/cmm/selectPage.do?page=static.convergence.utilizationProducts)이다.
후자는 SWRAD의 DSR 및 DQF 변수를, 전자는 `sat_file_down2.php`의 L2 요청 형태를 확인하는
공식 근거다.

| 단계/목적 | Endpoint | 필수 파라미터 | 확인된 반환 형식 |
| --- | --- | --- | --- |
| Step B 지점 DSR | `nph_sun_sat_txt` | `varn=DSR`, `tm1`, `tm2`, `int=10`, `lat`, `lon`, `authKey` | `TMFC/VAR/LON/LAT` 뒤에 timestamp 열이 이어지는 pipe 표. `DSR` 행의 각 시간 열이 값 하나다. |
| Step C 원본 SWRAD | `sat_file_down2.php` | `typ=bin`, `lvl=l2`, `dat=swrad`, `are=ko`, `tm`, `authKey` | NetCDF4(HDF5) 파일. 파일명 조각 `swrad-dsr`는 **파일명**이고 `dat` 값은 `swrad`다. |
| Step C 대체 비교 source | `sat_file_down2.php` | `typ=bin`, `lvl=l2`, `dat=ai-dsr`, `are=ko`, `tm`, `authKey` | NetCDF4(HDF5) 1시간 누적 AI DSR. advection 입력으로 대체하지 않는다. |
| Step E 좌표 검증 | `gk2a_latlon_file_down.php` | `area=KO`, `grid=2`, `authKey` | 2 km GK2A 격자의 위경도 NetCDF. SWRAD pixel↔좌표 crop 검증 전 필수다. |
| 파일 존재 진단 전용 | `sat_file_list.php` | `sat=GK2A`, `vars`, `area`, `fmt=bin`, `tm`, `filter`, `authKey` | 텍스트 목록. 현재 L2 SWRAD의 공개된 `vars` 계약/보관 정책은 확정하지 못했다. runtime 의존성을 두지 않는다. |

#### Step B: 지점 DSR 요청

서울시청 mock 위치의 2시간 조회 예시는 다음과 같다. `tm1`/`tm2`는 `YYYYMMDDHHMM`이다.

```text
GET https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph_sun_sat_txt?
  varn=DSR&tm1=202610170130&tm2=202610170330&int=10&
  lat=37.5665&lon=126.9780&authKey=<KMA_API_KEY>
```

응답의 `LON`/`LAT`은 provider 대표 좌표이며, 요청 좌표와 일치한다고 가정하지 않는다.
지점 endpoint는 최신 상태에서 최대 24 timestamp 열만 반환하므로 worker는 3시간 50분
범위로 나눠 요청한다.

#### Step C: 원본 SWRAD-DSR NetCDF 한 파일 확보

`scripts/fetch_gk2a_swrad_dsr_probe.py`는 DB를 쓰지 않는 수동 probe다. `--direct`는 파일
목록의 불명확한 L2 `vars` 계약을 우회하여 지정 시각을 직접 검증한다.

```powershell
.venv\Scripts\python.exe scripts\fetch_gk2a_swrad_dsr_probe.py `
  --direct --product swrad --area ko --tm 202610170130 `
  --output artifacts\gk2a_swrad_probe\gk2a_swrad_dsr_ko_202610170130.nc
```

실제 파일에서 확인한 구조는 `900 × 900` 2 km KO Lambert Conformal Conic grid다. `DSR`은
`uint16`, `scale_factor=0.1`, `units=W/m2`, `_FillValue=65535`이고, 동반 품질 변수는
`DSR_DQF1`(값 범위 품질)과 `SW_DQF`(SZA/VZA/SGA 기하 품질)다. 이는 Step C의 DQF gate에
사용할 공식 source다. 원본 파일은 Git 무시 artifact에만 보관하고 DB BLOB으로 저장하지 않는다.

#### AI DSR 비교용 NetCDF

AI DSR은 별개 L2 product다. 공식 요청은 아래와 같고, 실제 파일은 `DSR`의 단위가 `MJ/m2`,
시간 의미가 1시간 누적이며 `DQF_DSR`은 그 시간 내 6개 10분 frame의 NaN 개수다.

```powershell
.venv\Scripts\python.exe scripts\fetch_gk2a_swrad_dsr_probe.py `
  --direct --product ai-dsr --area ko --tm 202610280200 `
  --output artifacts\gk2a_swrad_probe\gk2a_ai_dsr_ko_202610280200.nc
```

그러므로 AI DSR은 10분 SWRAD DSR의 2-D advection 입력을 대체하지 않으며, 시간 의미를
보존한 별도 비교 source로만 취급한다.

#### Step E 전 좌표 파일 확보

SWRAD 파일에는 pixel별 latitude/longitude 배열이 아니라 projection metadata가 들어 있다.
crop/거리 검증 전에는 같은 영역·해상도의 좌표 파일을 별도로 내려받아야 한다.

```text
GET https://apihub.kma.go.kr/api/typ01/url/gk2a_latlon_file_down.php?
  area=KO&grid=2&authKey=<KMA_API_KEY>
```

파일 목록 API는 `vars=L2`, `SWRAD`, `SWRAD-DSR`의 대소문자/하이픈 변형과 KO/EA/FD를
실제 조회했으나 SWRAD 목록을 반환하지 않았다. API Hub가 L2 SWRAD 목록의 정확한 `vars`
값 및 보관 기간을 문서화하거나 지원 답변으로 제공하기 전까지, 목록 API 기반 자동 탐색은
**BLOCKED**다. 직접 다운로드의 `dat=swrad`는 실제 NetCDF 파일로 검증됐다.

## 6. Implementation Step C — DSR → DNI 추정과 비교

**목표:** 유효한 GK2A DSR을 GHI 후보로 취급해 `pvlib.irradiance.disc` 또는 DIRINT로
추정 DNI를 만들고, Open-Meteo를 정답이 아닌 비교 기준으로 기록한다.

- 범위: 같은 UTC instant의 FR-01 태양 위치/zenith와 DSR을 사용한다. `derived_from_ghi`,
  알고리즘 이름, input snapshot, clear-sky clip, DQF/저태양고도 제외 사유를 provenance에
  남긴다.
- 품질 조건: DSR finite, 0~1500 W/m², 실제 DQF 정상, 태양/위성 geometry 정상,
  태양천정각 ≤70°를 요구한다. 야간은 final DNI=0이며, DQF 불량·결측·저태양고도는
  미상/제외로 처리한다. DSR 시간 의미가 point observation인지 평균인지 실제 응답과
  product 문서로 확정하기 전에는 평균값으로 재해석하지 않는다.
- 비교: 같은 위치·시각의 Open-Meteo DNI와 차이, 비율, source freshness를 기록할 수는
  있지만 Open-Meteo를 ground truth로 사용하거나 보정 계수를 학습/도입하지 않는다.
- 선행 조건: Step B의 실제 DSR timestamp·단위·결측·품질·공간 대표성 검증.
- 완료 기준: 정상/야간/저태양고도/결측/DQF 불량 fixture와 실제 비교 sample에서 추정값과
  exclusion provenance가 재현 가능하다. 추정 불확실성 및 satellite DSR 오차를 문서화한다.
- 테스트: DISC/DIRINT input 시간 일치, zenith 경계, 0/null, clip, DQF, raw DSR provenance,
  Open-Meteo 비교 결과가 선택/보정 규칙을 바꾸지 않음을 검증한다.
- 상태: **PARTIAL — DISC derivation과 DQF/DSR/zenith/clear-sky exclusion gate, NetCDF adapter 및 derived snapshot worker는 구현됐다. 지점 응답에는 DQF가 없고 실제 KO SWRAD NetCDF에는 위·경도 배열이 없으므로, companion 좌표 파일을 확보·검증하기 전 production 수집은 안전하게 제외된다**.

## 7. Implementation Step D — DNI 선택과 v0.3 provenance

**목표:** 검증된 Open-Meteo DNI와 GK2A 추정 DNI 중, 동일 `valid_time`에서 freshness,
quality, 시간 의미를 만족하는 후보만 결정론적으로 선택하여 v0.3 provenance로 노출한다.

- 범위: 임의 가중평균·bias correction·“정확도 개선” 주장을 하지 않는다. 동률 해소 순서,
  freshness TTL, quality 우선순위를 명시하고 source가 하나면 그 값만 사용한다.
- API: 기존 v0.2 필드를 삭제·의미 변경하지 않는다. 유효 nowcast run을 사용한 응답만
  `result_version="0.3.0"`으로 반환하며 `prediction_method`, `nowcast_run_id`,
  `nowcast_horizon_seconds`, `clear_sky_dni_W_m2`, `clear_sky_index`,
  `source_snapshots[]`를 additive하게 제공한다.
- fallback: 후보가 없거나 invalid/stale이면 현행 v0.2 선택 결과, 이어서 clear-sky로
  fallback한다. worker 장애는 API 5xx가 되면 안 된다.
- 선행 조건: Step A와 Step C의 snapshot/quality 계약, v0.3 schema 결정.
- 완료 기준: 선택 이유, 제외 reason, snapshot ID, algorithm version을 run과 응답에서
  추적할 수 있고 v0.2 회귀 응답이 동일하게 유지된다.
- 테스트: exact valid-time, stale/invalid/null/0, source 하나, 모든 source 실패, timestamp와
  irradiance 1:1, v0.2/v0.3 호환, provenance additive fields를 검증한다.
- 상태: **PASS — GK2A 우선·Open-Meteo 차순, provider별 freshness TTL과 exact valid-time을 적용한다. 유효한 모든 timestamp 후보가 있을 때만 ready run 및 v0.3 provenance를 만들고, 그 외에는 v0.2 선택 결과로 fallback한다.**

## 8. Implementation Step E — GK2A 2-D crop/advection

**상태: BLOCKED.** 이 단계는 실제 2-D GK2A DSR NetCDF 또는 동등 격자 API 접근이
확보되고 아래 gate를 통과할 때만 시작한다.

- gate: 인증/권한, 실제 파일, UTC observed_at, 좌표계·위경도 배열, scale/fill value/DQF,
  한반도 site 주변 crop, 파일 제공 지연과 최소 두 연속 frame을 검증한다.
- 범위: 검증된 field에서 64 km × 64 km crop을 만들고, 품질 mask를 보존하며 optical flow와
  warp로 0~60분 후보를 만든다. DSR index 또는 DNI field 중 무엇을 advect할지는 실제
  DSR 시간 의미·품질 검증 뒤 별도 설계 결정으로 확정한다.
- 완료 기준: frame 간격, 공간 정렬, 저대비/결측/과속/flow error/warp invalid를 모두
  fallback으로 처리하고 Step D 선택 로직에 후보로 결합한다.
- 테스트: synthetic 2-D field fixture로 이동, crop 좌표, mask, frame 누락, 품질 실패 및
  advection unavailable fallback을 검증한다. 실제 대형 NetCDF는 unit test에 넣지 않는다.

## 9. Implementation Step F — 통합 검증과 FR-04 연계

- 범위: worker·repository·선택/API·선택적 advection의 통합, 장애 대응, FR-04의 비동기
  소비 interface를 검증한다. 이 계획의 worker는 FR-04를 직접 호출하지 않는다.
- 완료 기준: 24시간 mock worker soak에서 lease 중복, DB lock, raw file orphan이 없고,
  최소 7일 실 source 수집에서 latency/fallback/quality 통계를 남긴다. 미래 DNI 기능 비활성화 시
  기존 v0.2 및 FR-01/FR-06 회귀 테스트가 모두 통과해야 한다.
- FR-04 gate: 최신 ready run의 `result_hash`가 바뀌고 target DNI 차이가 5 W/m² 이상일 때만
  scheduler가 재검토한다. hysteresis·hold time·최종 상태 결정은 FR-04 책임이다.
- 테스트: source timeout/auth/no-data/disk-full/DB write failure/lease 경쟁이 request 5xx로
  번지지 않음, 동일 hash 무재스케줄, 변경 hash 재검토를 검증한다.
- 상태: **BLOCKED — Step D 및 FR-04 소비 계약 전**.

## 10. 운영·보존 원칙

- worker는 FastAPI lifespan이 아니라 별도 single process에서 lease를 획득해 실행한다.
- 원본 JSON/NetCDF는 DB BLOB으로 저장하지 않는다. raw payload는 hash와 선택적 debug path만
  기록한다.
- `weather_snapshot`/`weather_point`는 14일, `nowcast_run`/`nowcast_point`는 30일 보존한다.
  참조 중인 snapshot은 run 만료 전 삭제하지 않는다.
- Step E 전에는 GK2A crop cache와 2-D NetCDF 보존 정책을 활성화하지 않는다.
