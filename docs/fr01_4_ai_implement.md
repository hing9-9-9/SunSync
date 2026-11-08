# FR-01.4 KMA AI 초기 구현 계획 기록

> **문서 역할:** 개발 초기에 작성한 수집·실험 계획과 의사결정 기록이다. Phase 0~2의
> 수집기 이해에 사용한다. 현재 모델 계약은
> [`fr01_4_ai_model_implementation.md`](fr01_4_ai_model_implementation.md), 실제 결과는
> [`fr01_4_ai_kma_analysis.md`](fr01_4_ai_kma_analysis.md)를 따른다.

## 현재 진행 상태 — 2026-11-08

| 범위 | 상태 | 현재 산출물 |
|---|---|---|
| Phase 0~2 수집·정규화·queue | 완료 | KMA raw/CSV/manifest와 durable queue |
| Phase 3 Open-Meteo·정답 결합 | 완료 | 28일 개발 dataset |
| Phase 4~5 모델 학습·누수 방지 | 완료 | 10·30·60분 P10·P50·P90 동결 모델 |
| Phase 6 지표·EDA | 완료 | Validation 전체·구간별 JSON 평가 |
| Phase 7 테스트 | 완료 | artifact·누수·회귀 테스트 |
| Phase 8 로컬 문서 | 완료 | FR-01.4 문서 묶음 정리 |
| GitHub Issue #86 반영 | 별도 작업 | PR·artifact 경로 확정 후 갱신 |
| Final test | 미실행 | Open-Meteo final-test run 준비 필요 |

초기 계획의 `synthetic|real|both` 통합 CLI, PNG 그래프, ASOS, CLL·CT, GK2A patch는
현재 V1에 필요하지 않아 구현하지 않았다. 삭제된 요구가 아니라 후속 후보 또는 초기 설계 기록이다.

## 1. 목표

GitHub Issue `#86`의 수용 기준을 재현 가능한 코드와 산출물로 충족한다.

최종 사용 흐름:

```text
KMA·Open-Meteo 데이터 수집
        ↓
원본 보존 및 단위·시간 정규화
        ↓
표준 CSV 생성
        ↓
시간순 학습·검증·테스트
        ↓
Open-Meteo vs KMA AI 비교
        ↓
지표 CSV/JSON + EDA 그래프 + 문서
```

분석과 개념 설명은 [`fr01_4_ai_doc.md`](fr01_4_ai_doc.md)를 따른다.

---

## 2. 이번 이슈의 범위

포함:

- 합성 시나리오 벤치마크 유지
- 일회성 backfill CLI와 재사용 가능한 KMA adapter/parser
- Open-Meteo worker와 같은 `run_once()` 기반 KMA snapshot worker
- KMA 실데이터 수집 또는 수집 파일 import
- 표준 CSV 정규화 및 검증
- `--kma-csv` 실험 CLI
- 시간순 학습·검증
- 실제 Open-Meteo baseline 비교
- 전체·시간대·기상 구간별 지표
- EDA 그래프와 문서 갱신
- 단위·시간·누수 방지 테스트

기본 제외:

- 배포 환경의 cron, 서비스 관리자 등 실제 스케줄러·프로세스 감독 설정
- 프론트엔드 기능 추가
- 새 DB migration
- 전체 GK2A 동아시아 NetCDF 영구 저장
- 모델 성능을 특정 수치 이상으로 만들기 위한 임의 튜닝

실험 결과를 운영 API에 자동 배포하는 작업은 실측 검증이 끝난 뒤 별도 결정한다.

---

## 3. 확정된 결정과 API 확인 게이트

### Gate A: reference DNI — 확정

- [x] DSR 기반 proxy DNI를 사용한다.
- [x] 결과 표와 artifact에 `reference_type=dsr_proxy`를 표시한다.
- [x] 독립 실측 DNI 정확도로 표현하지 않는다.
- [x] 예측 대상 시각의 DSR이 입력 feature에 포함되지 않는지 자동 테스트한다.

### Gate B: GK2A 입력 — API 동작 확인 완료

- [x] `nph_sun_sat_txt` 천리안2A 기상산출물 지점 조회서비스를 사용한다.
- [x] API 최소 간격인 `int=10`을 요청한다.
- [x] 필수 변수는 `DSR`, `CA`로 정한다.
- [x] KMA API 키를 발급받고 해당 서비스 활용신청을 완료한다.
- [x] 직접 query로 HTTP 200과 실제 10분 간격을 확인한다.
- [x] 응답의 기본 필드와 wide-format 구조를 확인한다.
- [x] 국가기상위성센터 공식 정의에서 DSR 범위와 단위 `0~1500 W/m²`를 확인했다.
- [x] provider 결측 토큰 `nan`, `6553.5`를 실제 표본에서 확인했다.
- [x] V1에서는 `CLL`, `CT`를 사용하지 않고 DSR·CA만 사용하기로 확정했다.

공식 endpoint:

```text
https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph_sun_sat_txt
```

공식 명세: [KMA API Hub 산업특화 태양광 API](https://apihub.kma.go.kr/specialApiList.do)

### Gate C: API 키 설정 및 직접 query — 완료

KMA API Hub 회원가입 후 마이페이지에서 인증키를 발급받고, 산업특화 → 에너지(태양광) → **천리안2A호 기상산출물(복사, 구름, 안개) 지점 조회서비스**의 API 활용신청을 완료했다.

키는 repository root의 `.env`에 다음 이름으로 저장한다.

```dotenv
KMA_API_KEY=발급받은_인증키
```

`.env`와 `.env.*`는 `.gitignore`에 등록되어 있다. 수집기는 `python-dotenv`로 이 파일을 읽고 키 값 자체는 로그, 예외 메시지, 요청 URL, artifact에 기록하지 않는다.

키 설정 여부는 값을 출력하지 않고 다음처럼 확인한다.

```python
from dotenv import load_dotenv
import os

load_dotenv()
print("KMA_API_KEY is set" if os.getenv("KMA_API_KEY") else "KMA_API_KEY is not set")
```

첫 probe는 최근 과거의 주간 UTC 구간을 1시간만 요청한다. 아래 예시는 좌표와 기간을 실제 실험 대상으로 바꿔 실행한다. `Invoke-WebRequest`의 `-Body`를 사용해 인증키가 URL 출력에 직접 포함되지 않도록 한다.

```powershell
$envLine = Get-Content -LiteralPath ".env" |
  Where-Object { $_ -match "^KMA_API_KEY=" } |
  Select-Object -First 1
$apiKey = $envLine.Substring("KMA_API_KEY=".Length).Trim()

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$endpoint = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph_sun_sat_txt"
$common = @{
  tm1 = "202611050300"
  tm2 = "202611050400"
  int = "10"
  lat = "37.5714"
  lon = "126.9658"
  authKey = $apiKey
}

$dsrParams = $common.Clone()
$dsrParams.varn = "DSR"
$dsrResponse = Invoke-WebRequest -UseBasicParsing -Method Get `
  -Uri $endpoint -Body $dsrParams -TimeoutSec 90
$dsrResponse.Content

$cloudParams = $common.Clone()
$cloudParams.varn = "CA"
$cloudResponse = Invoke-WebRequest -UseBasicParsing -Method Get `
  -Uri $endpoint -Body $cloudParams -TimeoutSec 90
$cloudResponse.Content
```

### 2026-11-06 API probe 결과

요청 조건:

| 항목 | 값 |
|---|---|
| 조회 좌표 | 위도 `37.5714`, 경도 `126.9658` |
| 조회 기간 | `2026-11-05 03:00~04:00 UTC` |
| 요청 간격 | `int=10` |
| 변수 | `DSR`, `CA` |
| HTTP 결과 | 두 요청 모두 `200 OK` |
| Content-Type | `text/plain; charset=utf-8` |

추가 확인 결과 한 요청의 최대 출력 개수는 24개다. 10분 자료를 하루 수집할 때 수집기가 최대 24개 시점씩 자동 분할하고, DSR과 CA chunk를 제한된 병렬 요청으로 가져온다.

반환된 값:

| UTC | DSR | CA |
|---|---:|---:|
| 03:00 | 505.7 | 0.8 |
| 03:10 | 451.2 | 1.0 |
| 03:20 | 451.4 | 0.9 |
| 03:30 | 526.6 | 0.4 |
| 03:40 | 538.8 | 0.2 |
| 03:50 | 546.2 | 0.0 |
| 04:00 | 548.5 | 0.0 |

응답은 JSON이나 일반 CSV가 아니라 다음 형태의 **wide-format 고정폭 텍스트 테이블**이다.

```text
| TMFC | VAR | LON | LAT | 202611050300 | 202611050310 | ... |
| ...  | DSR | ... | ... |         505.7 |         451.2 | ... |
```

parser는 timestamp 열들을 행으로 풀어 다음 long format으로 정규화한다.

```text
timestamp_utc,variable,longitude,latitude,value
2026-11-05T03:00:00Z,DSR,126.9658,37.5714,505.7
```

확인 결과와 남은 확인사항:

- [x] `tm1`, `tm2`가 UTC 기준으로 반환된다.
- [x] 실제 timestamp가 10분 간격이다.
- [x] DSR 공식 범위와 단위는 `0~1500 W/m²`다.
- [x] CA 표본은 `0.0~1.0` 범위로 반환됐다.
- [x] 장기 표본에서 `1.1`이 확인되어 원문은 보존하고 모델 값은 null 처리한다.
- [x] 결측값은 `nan`과 DSR fill sentinel `6553.5`로 반환될 수 있다.
- [x] 이번 표본에서는 요청 좌표와 반환 좌표가 같았다.
- [ ] 자료 생산 후 조회 가능해지기까지 지연이 얼마나 되는가?
- [x] 헤더·데이터 행이 `|`로 구분된 wide-format 텍스트임을 확인했다.
- [x] 응답이 수십 초 걸릴 수 있어 client timeout은 최소 90초로 설정한다.
- [x] timeout·5xx·연결 실패에 지수 backoff 재시도를 적용한다.

24시간 표본에서 provider 결측 토큰 `nan`과 DSR fill sentinel `6553.5`를 확인했다. parser는 이 값을 null로 보존하고 실제 `0.0`과 구분한다.

DSR 값은 주간 일사량의 `W/m²` 범위와 일치하지만 응답 본문에는 단위가 포함되지 않았다. 공식 첨부 명세 또는 추가 검증 전까지 metadata에서 `unit_confirmed=false`로 다룬다. CA는 확인된 표본에서 `0~1` fraction이었으며, 정규화 과정에서 범위를 검증한 뒤 `cloud_cover_pct = CA × 100`으로 변환한다.

---

## 4. Phase 0 — 수집 파이프라인 구조와 실제 데이터 확인

### 4.1 아키텍처 결정

KMA API는 사용자 또는 SunSync API 요청이 들어올 때마다 호출하지 않는다. Open-Meteo worker와 같은 **사전 자동 수집형 snapshot 파이프라인**으로 구현한다.

```text
KMA API
   ↓  외부 스케줄러가 worker의 run_once() 실행
KMA adapter/parser
   ↓
원본 TXT + 정규화 CSV + SQLite immutable snapshot
   ↓
모델 학습·예측 및 SunSync API
```

SunSync API 요청 경로는 이미 저장된 snapshot만 읽는다. KMA API의 timeout, 장애, 호출 제한이 사용자 요청 지연이나 실패로 직접 전파되지 않게 한다.

구현은 다음 순서로 진행한다.

1. 과거 기간을 지정할 수 있는 일회성 backfill CLI를 먼저 만든다.
2. 1시간 표본, 24시간 표본, 3~7일 표본 순으로 수집해 실제 데이터를 확인한다.
3. 응답 포맷과 정규화 계약이 확정되면 같은 adapter/parser를 `kma_worker.run_once()`에서 재사용한다.
4. worker는 Scene의 고유 좌표별로 수집하고 immutable snapshot을 저장한다.
5. 실제 실행 주기는 자료 생산 지연을 측정한 뒤 10분 또는 20분으로 확정한다.
6. cron, Windows Task Scheduler, 컨테이너 scheduler 등 프로세스 실행 책임은 FastAPI lifespan이 아니라 배포 환경에 둔다.

권장 모듈 경계:

```text
backend/app/nowcast/kma_adapter.py
  - KMA 요청 생성
  - wide-format TXT parser
  - 값과 시간 검증

backend/app/nowcast/kma_worker.py
  - run_once()
  - Scene 좌표별 수집
  - lease와 immutable snapshot 저장

tools/ai_experiments/collect_fr01_kma_data.py
  - 임의 과거 기간 backfill
  - 사람이 확인할 TXT/CSV 생성
```

### 4.2 로컬 파일 저장 계약

KMA API로 받은 결과는 로컬 개발 환경에서 **직접 열어볼 수 있는 파일**로 `data/fr01/` 아래에 저장한다.

```text
data/fr01/
  raw/kma_gk2a/
    20261105T000000Z_20261105T235000Z_DSR_part001.txt
    20261105T000000Z_20261105T235000Z_CA_part001.txt
  normalized/
    20261105T030000Z_20261105T040000Z_kma_gk2a.csv
  manifests/
    20261105T030000Z_20261105T040000Z_collection.json
```

각 파일의 역할:

| 파일 | 내용 | 용도 |
|---|---|---|
| `raw/..._DSR.txt` | KMA가 반환한 DSR 원문 전체 | parser 오류, 단위, provider 변경 확인 |
| `raw/..._CA.txt` | KMA가 반환한 CA 원문 전체 | 운량 범위와 결측값 확인 |
| `normalized/*.csv` | timestamp별로 합친 DSR·CA long-format 데이터 | VS Code, Excel, pandas로 직접 확인 및 모델 입력 |
| `manifests/*.json` | endpoint, 요청 좌표·기간, 수집 시각, HTTP 상태, hash, 변환 버전 | 재현성과 provenance 확인 |

정규화 CSV 기본 형태:

```csv
timestamp_utc,requested_latitude,requested_longitude,returned_latitude,returned_longitude,dsr_raw,ca_fraction,cloud_cover_pct,dsr_unit,unit_confirmed,quality_flag
2026-11-05T03:00:00Z,37.5714,126.9658,37.5714,126.9658,505.7,0.8,80.0,unknown,false,ok
```

저장 규칙:

- 원본 `.txt`는 decoding 또는 열 재배치 없이 provider 응답을 그대로 저장한다.
- 정규화 `.csv`는 UTF-8과 ISO-8601 UTC timestamp를 사용한다.
- DSR과 CA 요청은 timestamp로 결합한다.
- 실제 0과 결측값을 구분하며 결측값을 0으로 채우지 않는다.
- API 키와 전체 요청 URL은 어떤 파일에도 저장하지 않는다.
- 파일은 임시 파일에 완전히 쓴 뒤 최종 이름으로 교체해 중간 결과가 남지 않게 한다.
- 동일 요청을 다시 수행하면 hash를 비교해 중복 원본과 snapshot을 만들지 않는다.
- `data/fr01/`은 Git에 올리지 않고 로컬에서만 보관한다.

모델 학습 전에 `normalized/*.csv`를 직접 열고 다음을 확인한다.

- 낮과 밤의 DSR 변화
- CA가 전체 기간에도 `0~1` 범위인지
- 10분 timestamp 연속성
- DSR·CA 결측과 이상치
- 같은 시각의 DSR과 CA 정렬
- 실제 자료 제공 지연

### 4.3 Phase 0 완료 조건

2026-11-06 로컬 실행으로 다음 파일 생성을 확인했다.

- `00:00~03:50 UTC` 4시간 표본: DSR·CA 원본 TXT와 24행 정규화 CSV
- `00:00~23:50 UTC` 하루 범위: 변수별 6개 raw chunk와 144행 정규화 CSV
- 하루 CSV의 timestamp 간격: 전체 구간에서 정확히 10분
- 하루 CSV 수집 당시 `16:10~23:50 UTC`는 아직 미래 시각이어서 null로 보존됨
- 확인된 유효 범위: DSR `0.0~548.5`, CA `0.0~1.0`

따라서 하루 파일은 parser와 결측 보존 검증에는 사용하되, 완전한 과거 하루의 결측률 평가 자료로 해석하지 않는다.

- [x] KMA adapter가 `.env`의 키를 노출하지 않고 DSR·CA를 조회한다.
- [x] 원본 DSR·CA가 각각 `.txt`로 저장된다.
- [x] 두 원본이 하나의 timestamp별 `.csv`로 정규화된다.
- [x] 24시간 범위 144개 timestamp를 수집해 직접 확인했다.
- [x] provider 결측값 `nan`, `6553.5`와 CA `0~1` 범위를 기록했다.
- [ ] DSR 공식 단위와 실제 생산 지연을 추가 확인한다.
- [x] SunSync API 요청 중에는 KMA 외부 호출이 발생하지 않는다.
- [x] 같은 adapter/parser를 backfill CLI와 worker가 함께 사용한다.
- [x] 데이터 확인 후에만 모델 feature와 학습 pipeline 구현을 시작한다.

---

## 5. Phase 1 — 데이터 계약과 디렉터리

### 작업

- [ ] 정규화 CSV schema를 코드로 정의한다.
- [ ] 필수/선택 컬럼, 단위, nullable 규칙을 정의한다.
- [ ] UTC ISO-8601 timestamp만 내부 표준으로 허용한다.
- [ ] 원본값과 변환값의 provenance metadata schema를 정의한다.
- [ ] 데이터 품질 flag를 정의한다.
- [ ] API 키와 원본 대용량 파일이 Git에 포함되지 않도록 확인한다.

권장 파일 구조:

```text
data/fr01/                    # 기본적으로 Git 제외
  raw/
    kma/
    open_meteo/
  normalized/
artifacts/fr01/               # 실험 산출물
```

권장 정규화 컬럼:

```text
timestamp_utc
station_id
latitude
longitude
dsr_W_m2
cloud_cover_pct
temperature_c
humidity_pct
open_meteo_issued_at
open_meteo_dni_W_m2
reference_dni_W_m2
reference_type
patch_size_km
mean_cloud_fraction
upwind_cloud_gradient
motion_u_m_s
motion_v_m_s
quality_flag
```

### 완료 조건

- [ ] 예제 CSV 한 건이 schema validation을 통과한다.
- [ ] 필수 컬럼 누락, 잘못된 timestamp, 음수 DSR을 명확한 오류로 거절한다.
- [ ] 실제 0과 결측값을 구분한다.

---

## 6. Phase 2 — KMA 수집 및 정규화

### 대상 파일

신규 파일 권장:

```text
tools/ai_experiments/collect_fr01_kma_data.py
backend/app/nowcast/kma_dataset.py
tests/test_fr01_kma_dataset.py
```

수집 스크립트는 외부 API와 원본 파일만 담당하고, `kma_dataset.py`는 파싱·단위 변환·정렬·검증을 담당하도록 분리한다.

### CLI 초안

```powershell
.\.venv\Scripts\python.exe tools\ai_experiments\collect_fr01_kma_data.py `
  --latitude 37.5714 `
  --longitude 126.9658 `
  --start 2026-11-05T03:00:00Z `
  --end 2026-11-06T03:00:00Z `
  --interval-minutes 10 `
  --variables DSR CA `
  --output-dir data\fr01
```

인증키는 repository root의 `.env`에서 읽는다.

```dotenv
KMA_API_KEY=발급받은_인증키
```

### 작업

- [x] 기본 timeout 90초, 지수 backoff 재시도, HTTP 오류 처리를 구현한다.
- [x] 원본 응답을 수정 없이 chunk별 파일로 저장한다.
- [x] GK2A의 UTC timestamp를 timezone-aware datetime으로 파싱한다.
- [ ] ASOS를 추가할 때만 KST timestamp를 UTC로 변환한다.
- [x] wide-format timestamp 열을 long-format 행으로 변환한다.
- [ ] 일사량 원본 단위가 순간 `W/m²`인지 누적 `MJ/m²`인지 metadata에 기록한다.
- [ ] 누적값이면 관측 간격 기준으로 `W/m²`로 변환한다.
- [x] 빈 문자열, `nan`, `-9` 계열, `6553.5`를 null로 변환한다.
- [x] DSR 비음수와 CA `0~1` 범위 검사를 적용한다.
- [x] 요청 좌표와 provider 반환 좌표를 기록한다.
- [x] 파일은 원자적으로 교체하고 DB snapshot은 payload hash로 중복을 막는다.
- [x] 원본 행 수, 유효 행 수, hash를 manifest JSON으로 출력한다.
- [x] 키와 인증 파라미터를 로그와 저장된 요청 metadata에서 제외한다.

### GK2A 10분 지점 자료 및 patch 작업

- [x] `DSR`, `CA`를 같은 좌표·기간에 각각 조회해 timestamp로 결합한다.
- [x] sample query에서 실제 10분 생산 간격을 확인했다.
- [ ] 수집 결과의 실제 생산 간격을 metadata와 보고서에 자동 기록한다.
- [x] 가장 가까운 단일 격자점 응답과 요청 좌표를 함께 보존한다.
- [ ] 단일 지점 조회만으로 patch 이동 벡터를 만들 수 없으면 `cloud_patch_used=false`로 둔다.
- [ ] patch가 필요하면 중심 주변 여러 위·경도를 별도로 조회하는 방식의 호출량과 격자 중복을 먼저 검증한다.
- [ ] patch 반경과 실제 반환 격자 해상도를 metadata에 기록한다.
- [ ] 주변 격자 CA로 평균 구름 비율과 공간 gradient를 계산한다.
- [ ] 연속 10분 프레임에서 이동 벡터를 추정한다.
- [x] 자료가 없는 시각은 0이 아니라 null로 유지한다.

### 완료 조건

- [x] 하루치 144개 timestamp를 수집해 TXT·CSV·manifest 생성을 확인한다.
- [ ] 최소 3일, 권장 7일의 정규화 CSV가 생성된다.
- [x] 키가 로그·CSV·Git diff와 객체 repr에 노출되지 않는다.

---

<details>
<summary>초기 Phase 3~8 상세 계획 보기 — 현재 구현은 모델 구현 문서를 우선</summary>

## 7. Phase 3 — Open-Meteo와 reference DNI 결합

### 작업

- [ ] 같은 좌표·기간의 Open-Meteo DNI snapshot을 준비한다.
- [ ] `issued_at`과 `valid_time`을 구분한다.
- [ ] 각 검증 시각에 당시 이용 가능했던 snapshot만 선택한다.
- [ ] KMA보다 낮은 시간 해상도는 적용 구간 의미에 따라 정렬한다.
- [ ] forward-looking 보간으로 미래값이 유입되지 않게 한다.
- [ ] 평가 대상 시각의 DSR로 proxy DNI를 생성해 같은 UTC timestamp에 결합한다.
- [ ] `reference_type=dsr_proxy`를 모든 행 또는 run metadata에 기록한다.
- [ ] 입력 cutoff 뒤의 DSR은 proxy target 생성에만 사용한다.
- [ ] join 전후 행 수와 탈락 사유를 기록한다.

시간 정렬 허용오차는 데이터 출처별로 명시적으로 설정한다. 무조건 nearest join을 사용하지 않는다.

### 완료 조건

- [ ] 임의 표본 10개에서 KMA, Open-Meteo, reference timestamp를 수동 확인한다.
- [ ] prediction time보다 뒤에 발행된 Open-Meteo snapshot이 선택되지 않는다.
- [ ] reference가 없는 행은 metric 계산에서 제외되지만 예측 결과에는 남길 수 있다.

---

## 8. Phase 4 — 실험 CLI 구현

### 대상 파일

기존 파일 확장:

```text
tools/ai_experiments/run_fr01_kma_nowcast_experiment.py
```

필요하면 공통 로직을 다음 파일로 분리한다.

```text
backend/app/nowcast/kma_experiment.py
```

### CLI 계약

```powershell
.\.venv\Scripts\python.exe tools\ai_experiments\run_fr01_kma_nowcast_experiment.py `
  --mode both `
  --kma-csv data\fr01\normalized\kma_openmeteo.csv `
  --output-dir artifacts\fr01 `
  --site-lat 37.5714 `
  --site-lon 126.9658 `
  --timezone Asia/Seoul `
  --seed 42
```

필수 옵션:

- [ ] `--mode synthetic|real|both`
- [ ] `--kma-csv`
- [ ] `--output-dir`
- [ ] `--site-lat`, `--site-lon`
- [ ] `--timezone`
- [ ] `--seed`

선택 옵션:

- [ ] `--train-end`
- [ ] `--validation-end`
- [ ] `--min-solar-elevation`
- [ ] `--cloud-patch-enabled`

### 작업

- [ ] 기존 합성 데이터 생성과 출력 결과를 유지한다.
- [ ] real mode에서 CSV를 validation한 뒤 읽는다.
- [ ] 명령행 설정과 Git commit을 run metadata에 기록한다.
- [ ] 고정 seed로 같은 입력에서 같은 결과를 만든다.
- [ ] 에러 시 원인을 알 수 있는 메시지와 non-zero exit code를 반환한다.

### 완료 조건

- [ ] `--mode synthetic`가 기존 세 시나리오를 실행한다.
- [ ] `--mode real --kma-csv ...`가 실제 CSV로 끝까지 실행된다.
- [ ] `--help`에 각 옵션과 단위가 표시된다.

---

## 9. Phase 5 — 학습·예측 파이프라인 수정

### 작업

- [ ] CSV 행에서 현재 5개 feature를 생성하는 단일 함수를 만든다.
- [ ] 학습과 추론이 같은 feature 정의를 사용한다.
- [ ] feature 정규화 분모가 0인 경우를 처리한다.
- [ ] 시간을 기준으로 train/validation/test를 분리한다.
- [ ] prediction timestamp 이전 관측만 사용하는 rolling-origin 평가를 구현한다.
- [ ] baseline과 ensemble 입력에 같은 Open-Meteo 값을 사용한다.
- [ ] 학습된 quantile weight와 학습 metadata를 JSON으로 저장한다.
- [ ] 저장된 weight를 다시 로드해 동일 예측이 나오는지 검증한다.

권장 분할 기본값:

```text
train      앞 60%
validation 다음 20%
test       마지막 20%
```

데이터가 매우 짧을 때는 분할별 최소 유효 표본 수를 검사하고 부족하면 성능 결과 대신 명확한 경고를 출력한다.

### 누수 방지 조건

- [ ] 시간 `t` 예측에 `timestamp > t`인 KMA 관측이 없다.
- [ ] 시간 `t` 예측에 `issued_at > t`인 Open-Meteo 예보가 없다.
- [ ] test target은 model fitting에 사용되지 않는다.
- [ ] 기상 regime 임계값은 test 결과를 본 뒤 바꾸지 않는다.

### 완료 조건

- [ ] 의도적으로 미래 관측을 넣은 테스트가 실패를 검출한다.
- [ ] split 경계 timestamp가 train/test 양쪽에 중복되지 않는다.
- [ ] 같은 실제 Open-Meteo 값으로 baseline과 ensemble을 비교한다.

---

## 10. Phase 6 — 지표와 EDA 산출물

### 계산 지표

- [ ] Open-Meteo RMSE
- [ ] Open-Meteo MAE
- [ ] KMA AI q50 RMSE
- [ ] KMA AI q50 MAE
- [ ] RMSE·MAE 개선율
- [ ] q10/q50/q90별 Pinball Loss
- [ ] q10~q90 PICP
- [ ] 평균·중앙 uncertainty spread
- [ ] 유효 표본 수와 결측률

밤 데이터와 낮 데이터의 결과를 분리한다. 주간 기본 기준은 태양 고도 임계값으로 정의한다.

### 구간별 결과

- [ ] 전체 주간
- [ ] 시간대별
- [ ] 태양 고도별
- [ ] 맑음
- [ ] 급격한 구름 이동
- [ ] 흐림
- [ ] cloud patch 사용 여부별

각 표에는 반드시 `sample_count`를 포함한다. 표본이 설정한 최소 개수보다 적으면 수치를 표시하더라도 `insufficient_sample`로 표시한다.

### 산출 파일

```text
artifacts/fr01/<run-id>/
  run_metadata.json
  dataset_summary.json
  model_weights.json
  predictions.csv
  metrics_overall.csv
  metrics_by_regime.csv
  metrics_by_hour.csv
  timeseries_comparison.png
  residual_by_hour.png
  residual_by_cloud_variability.png
  quantile_calibration.png
```

### 그래프 요구사항

- [ ] reference DNI, Open-Meteo, q50를 한 시간축에 표시한다.
- [ ] q10~q90 band를 음영으로 표시한다.
- [ ] 결측 구간을 선으로 연결해 실제 값처럼 보이지 않게 한다.
- [ ] 단위와 timezone을 축에 표시한다.
- [ ] 그래프 제목에 실측/proxy reference를 표시한다.

### 완료 조건

- [ ] 한 번의 CLI 실행으로 모든 산출 파일이 생성된다.
- [ ] metrics CSV를 predictions에서 다시 계산해 같은 값이 나온다.
- [ ] 합성 결과와 실측 결과가 서로 다른 run-id에 저장된다.

---

## 11. Phase 7 — 테스트

### 단위 테스트

- [ ] KMA 원본 parser
- [ ] `MJ/m²`→`W/m²` 변환
- [ ] KST→UTC 변환과 날짜 경계
- [x] provider 결측 코드 처리
- [ ] 중복 timestamp 처리
- [ ] CSV schema validation
- [ ] 시간 정렬
- [ ] feature 생성
- [ ] regime 분류
- [ ] 지표 계산
- [ ] model weight save/load

### 회귀·통합 테스트

- [ ] 기존 `tests/test_fr01_kma_ai_nowcast.py` 전체 통과
- [ ] synthetic CLI smoke test
- [ ] 작은 fixture CSV를 사용한 real CLI smoke test
- [ ] 미래 KMA 관측 및 proxy target DSR 누수 방지 테스트
- [ ] 미래 Open-Meteo snapshot 누수 방지 테스트
- [ ] q10 ≤ q50 ≤ q90 및 물리 상한 테스트
- [ ] 산출물 생성 테스트

권장 실행 명령:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_fr01_kma_ai_nowcast.py `
  tests\test_fr01_kma_dataset.py `
  tests\test_fr01_kma_experiment.py
```

---

## 12. Phase 8 — 문서 반영

### `docs/fr01_4_kma_ai_nowcast.md`

- [ ] 기존 결과를 `Synthetic Benchmark`로 명시한다.
- [ ] Windows와 POSIX 실행 명령을 모두 적는다.
- [ ] 실측 dataset 기간, 지점, 출처, 단위, 결측률을 기록한다.
- [ ] reference DNI를 `DSR-based proxy`로 표시한다.
- [ ] 시간 분할과 rolling-origin 방식을 기록한다.
- [ ] 실제 결과 표와 EDA 그래프를 추가한다.
- [ ] 위성 patch 사용 여부를 기록한다.
- [ ] 한계와 후속 수집 계획을 적는다.

### Issue `#86`

구현 결과에 따라 다음 불일치를 정리한다.

- [ ] `nph_sun_sat_txt?int=10` 직접 query 결과를 근거로 Issue의 “10분” 수용 기준을 확인한다.
- [ ] `--kma-csv` 재현 명령을 댓글 또는 본문에 추가한다.
- [ ] 결과 artifact 또는 문서 링크를 추가한다.
- [ ] 네 가지 acceptance criteria별 증거를 연결한다.

GitHub Issue 수정은 로컬 구현·검증 완료 후 별도 승인하에 수행한다.

---

## 13. 권장 구현 순서

1. KMA API 키를 발급받고 천리안2A 기상산출물 서비스 활용신청을 완료한다.
2. `DSR`, `CA`를 `int=10`으로 직접 query한다.
3. 응답의 필드, 단위, 결측값, 격자 좌표와 실제 간격을 기록한다.
4. schema와 작은 fixture CSV를 만든다.
5. parser·단위 변환·시간 변환 테스트를 먼저 작성한다.
6. KMA 수집기를 구현해 하루치 small sample을 확인한다.
7. 3~7일 자료를 수집하고 Open-Meteo·DSR proxy reference를 결합한다.
8. 실험 CLI에 `--kma-csv` real mode를 추가한다.
9. 시간순 분할과 rolling-origin 평가를 구현한다.
10. 지표와 산출 파일을 생성한다.
11. 구간별 EDA와 ablation을 실행한다.
12. 문서와 Issue 증거를 정리한다.

데이터 수집 전에 모델 튜닝을 먼저 하지 않는다. 실제 단위, 시간 간격, 결측 패턴이 확정된 뒤 feature와 학습 방식을 조정한다.

---

## 14. 이번 스프린트 완료 정의

- [ ] KMA 3~7일 실데이터가 원본과 정규화본으로 보존된다.
- [ ] 데이터 출처, 단위, 간격, timezone, 결측률이 기록된다.
- [ ] `--kma-csv` 명령으로 재현 가능한 실측 실험이 실행된다.
- [ ] 시간순 분할과 rolling-origin으로 미래 데이터 누수를 막는다.
- [ ] 실제 Open-Meteo snapshot과 동일 timestamp에서 비교한다.
- [ ] RMSE, MAE, quantile Pinball Loss, PICP가 계산된다.
- [ ] 전체·시간대·구름 변동 구간별 결과와 표본 수가 생성된다.
- [ ] EDA 그래프와 실행 metadata가 artifact로 남는다.
- [ ] 합성 결과와 실측 결과가 문서에서 분리된다.
- [ ] 모든 결과에 `reference_type=dsr_proxy`와 독립 DNI 평가가 아니라는 한계가 명시된다.
- [ ] 관련 테스트와 기존 FR-01.4 테스트가 통과한다.

성능 향상 자체는 완료 조건으로 고정하지 않는다. 데이터와 방법을 바꾸지 않고 얻은 결과를 정직하게 보고하는 것이 이 실험의 완료 조건이다.

</details>

현재 V1은 위 초기 완료 정의 중 PNG 그래프와 통합 `--mode` CLI를 제외한 데이터 수집,
누수 방지, 실제 Open-Meteo 비교, 분위수 지표, 구간 평가, artifact 저장과 테스트를 완료했다.
그래프 대신 재계산 가능한 CSV·JSON을 남겼으며 final test와 운영 API 연결은 다음 작업이다.
