# SunSync Film & ThinQ Hub 제어·동기화 아키텍처 (`:8000` ↔ `:8002`)

SunSync 백엔드(`sunsync-api`, `127.0.0.1:8000`)와 독립형 스마트 필름·ThinQ ON 허브 서버(`sunsync-hub`, `127.0.0.1:8002`) 간의 IoT 제어 및 상태 동기화 설계와 운영 배포 절차를 정리한 문서입니다.

---

## 1. 전체 토폴로지 및 호환성 원칙

```text
Browser / Mobile App ──HTTPS──▶ Caddy (443)
                                 ├── /api/*      ─▶ sunsync-api (127.0.0.1:8000)      [sunsync.db (SQLite WAL)]
                                 ├── /measure/*  ─▶ sunsync-measure (127.0.0.1:8001)  [stateless]
                                 └── (선택) /hub/* ─▶ import login ─▶ sunsync-hub (127.0.0.1:8002)

sunsync-api (:8000) ═══[HMAC-SHA256 REST + Idempotency-Key]═══▶ sunsync-hub (:8002) [sunsync-hub.db (SQLite WAL)]
sunsync-hub (:8002) ───[WS /hub/v1/ws (실시간 시뮬레이터 텔레메트리)]───▶ Hub Inspector / 관제 UI
```

- **100% 하위 호환(Local Fallback):** `SUNSYNC_HUB_BASE_URL` 환경변수가 비어 있거나 미설정이면 `:8000`은 기존 인프로세스 가상 필름 모드(`sync_mode="local"`, `sync_status="local_only"`, `delivery_status="local_only"`, `display_status="virtual_applied"`)로 동작합니다. 따라서 로컬 개발·기존 테스트·단일 서버 배포가 깨지지 않습니다.
- **Zero-Docker 경량 운용:** Mosquitto·Redis 등 외부 브로커 없이 Ubuntu 단일 서버(2 GB RAM 환경)에서 SQLite WAL과 Python stdlib(`urllib.request`, `hmac`, `hashlib`, `secrets`)만으로 동작합니다.

---

## 2. 핵심 제어·동기화 메커니즘

### 2.1 Device Shadow (Desired / Reported / Delta 분리)

AWS IoT Device Shadow 패턴을 적용하여 제어 의도와 실제 구동 상태를 엄격히 분리합니다.

- **백엔드(`:8000`)는 `desired` 상태만 기록**하며, 상태 변경마다 단조 증가하는 `desired_version`을 1씩 올립니다(`film_states.desired_version`).
- **허브(`:8002`)는 액추에이터 구동 후 `reported` 상태만 보고**하며, 반영된 `reported_version`·`effective_cells`·`state_digest`·`ETag`를 반환합니다.
- **표준 상태 다이제스트 (`compute_state_digest`):**
  - 셀 불투명도를 소수 셋째 자리(`round(float(v), 3)`)로 양자화하고, 마커 오버레이(`enabled`, `code_id`, `start_row`, `start_col`)와 함께 키 정렬 JSON(`sort_keys=True, separators=(",", ":")`)의 SHA-256 해시를 산출합니다.
  - `:8000`과 `:8002`가 동일한 정규화 함수를 공유하므로 부동소수점 오차 없이 드리프트를 정밀 감지합니다.
- **역순 전달 및 Ghost-Delta 방지:**
  - 허브는 `incoming.desired_version < current.reported_version` 요청에 `status="superseded"`를 반환하여 오래된 명령이 최신 상태를 덮어쓰지 못하게 막습니다.
  - PDLC 이진 제약 위반 등 잘못된 명령은 허브가 `status="rejected"` + `rejection_reason`을 반환하고, 백엔드는 `sync_status="rejected"`로 기록해 무한 재시도를 방지합니다.

### 2.2 계층형 마커 오버레이 (Layered Marker Overlay — FR-05.2)

- 허브 섀도우는 **기저 차광 매트릭스(`base_cells`)** 와 **일시적 6×6 ArUco 마커 레이어(`marker_overlay`)** 를 분리 저장합니다.
- 실제 구동 셀은 항상 결정적으로 합성됩니다:
  `effective_cells = overlay_aruco(base_cells, marker_overlay) if marker_overlay.enabled else base_cells`
- 촬영 종료(`enabled=False`) 시 백업 손상 없이 `base_cells`가 즉시 복원됩니다.
- **촬영 마커 안전장치 (`POST /api/scenes/{scene_id}/capture-marker`):**
  - 원격 허브 모드에서는 허브가 마커 표시를 실제 ACK(`sync_status="synced"`)한 경우에만 `display_status="hub_applied"`로 세션을 커밋합니다.
  - 허브 오프라인·타임아웃·거부 시 즉시 `503 Service Unavailable`을 반환하고 씬 지오메트리·필름·캡처 세션을 **원자적으로 롤백**하여, 마커가 표시되지 않은 창문을 휴대폰이 촬영하는 사고를 원천 차단합니다.

### 2.3 Transactional Outbox 및 멱등성 (`hub_commands`, Migration v7)

- 모든 원격 제어 요청(`film-commands`, `film-schedule-apply`, `film-marker-mode`, `marker-pairing`, `thinq-hub-sync`)은 단일 SQLite 트랜잭션 안에서 `film_states` + `film_application_logs` + `hub_commands` Outbox 레코드를 함께 커밋한 뒤 허브로 전송합니다.
- 명령마다 시간순 정렬 가능한 UUIDv7 `command_id`를 생성해 요청 본문과 `Idempotency-Key` 헤더에 동시에 싣습니다. 허브는 `command_id` 유니크 영수증으로 재전송을 멱등 처리합니다.
- 동일 `(scene_id, window_id)`에 더 높은 `desired_version` 명령이 적재되면 이전 미완료(`pending`/`dispatched`/`timed_out`) 명령은 자동으로 `superseded`로 병합(coalesce)됩니다.
- 일시적 네트워크 장애·타임아웃 시 백엔드는 의도 상태와 `hub_commands(status="pending"|"timed_out")`를 보존한 채 `sync_status="pending"`으로 우아하게 응답합니다.

### 2.4 서비스 간 인증 (HMAC-SHA256 + 리플레이 방지)

RFC 9421 원칙을 따르는 경량 zero-dependency 서명을 적용합니다.

- **전송 헤더:**
  - `X-Hub-Key-Id`: 활성 키 ID (`SUNSYNC_HUB_KEY_ID`, 기본값 `v1`)
  - `X-Hub-Timestamp`: Unix epoch 초 문자열
  - `X-Hub-Nonce`: 요청별 16바이트 URL-safe 난수
  - `X-Hub-Signature`: `v1=<hex(HMAC-SHA256(secret, canonical_string))>`
  - `Idempotency-Key`: `command_id`
- **Canonical String:**
  ```text
  {METHOD}\n{PATH_WITH_QUERY}\n{TIMESTAMP}\n{NONCE}\n{sha256_hex(raw_body)}
  ```
- **허브 검증 규칙:** 시간 오차 `±60초` 초과 차단(`401`), TTL 기반 논스 리플레이 캐시 중복 차단(`401`), `hmac.compare_digest` 상수시간 검증, `SUNSYNC_HUB_PREV_SECRET`을 통한 무중단 키 로테이션 지원.

### 2.5 상태 조회 및 드리프트 복구 (Reconciliation)

백엔드(`:8000`)는 기존 사용자 인증(`SUNSYNC_AUTH=google`) 및 소유권 검사(`check_ownership`) 아래 두 운영 엔드포인트를 제공합니다.

| 엔드포인트 | 역할 |
|---|---|
| `GET /api/scenes/{scene_id}/hub-status` | 허브 연결 가능 여부(`online`/`degraded`/`offline`/`local_only`), `hub_boot_id`, 대기 중 Outbox 수량, 창문별 `desired_version`/`reported_version`/`state_digest` 조회 |
| `POST /api/scenes/{scene_id}/hub-reconcile` | 오래된 Outbox 명령 `superseded` 정리 → 최신 보류 명령 재전송 → 허브 `POST /hub/v1/reconcile/digests`로 일괄 다이제스트 비교 → `drifted`/`missing` 창문에 `PUT /hub/v1/windows/{scene_id}/{window_id}/shadow` 전체 스냅샷 푸시로 자가 치유 |

### 2.6 LG ThinQ ON / Matter 정합성

- `POST /api/scenes/{scene_id}/thinq-hub-sync`는 원격 허브 모드에서 `POST /hub/v1/thinq/sync`를 호출하며, LG ThinQ Connect 규격에서 착안한 `X-Client-Id`, `X-Message-Id`, `X-Protocol-Version: LG_THINQ_ON_SGF_V1` 헤더를 전송합니다.
- 허브는 CSA Matter Window Covering Cluster(`0x0102`) 기준 `matter_level_pct_x100` (`0..10000`) 텔레메트리를 함께 계산하며, 추후 실물 LG ThinQ / Matter 하드웨어 연동 시 허브 내부 드라이버 어댑터만 교체하면 백엔드 계약은 그대로 유지됩니다.

---

## 3. 백엔드/인프라 담당자 및 사용자 요청 체크리스트

아래 항목들은 **GitHub PR 병합만으로 자동 해결할 수 없으며**, 서버 운영자(SSH/root 권한 보유자) 또는 프로젝트 담당자가 직접 수행해야 하는 작업입니다.

### [필수 1] Ubuntu 서버에서 `sunsync-hub.service` 최초 등록 및 활성화
`deploy/install-units.sh`는 신규 `*.service` 파일을 `/etc/systemd/system/`에 복사하고 `daemon-reload`만 수행하며, **서비스 자동 `enable`은 수행하지 않습니다**(타이머만 자동 enable). 배포 후 서버에서 1회 실행이 필요합니다.

```bash
sudo systemctl enable --now sunsync-hub.service
sudo systemctl status sunsync-hub.service --no-pager
curl -fsS http://127.0.0.1:8002/hub/v1/health
```

- `/var/lib/sunsync` 소유자(`sunsync:sunsync`) 및 권한(`0700`)에서 `sunsync-hub.db`가 정상 생성되는지 확인하세요.
- 2 GB RAM 서버에서 3번째 uvicorn 워커가 추가(~40–60 MB)되므로 활성화 직후 `free -m`으로 여유 메모리를 점검하세요.

### [필수 2] `/etc/sunsync/sunsync.env` 시크릿 및 환경변수 설정 (`chmod 600`, Git 미포함)
운영 시크릿 파일에 아래 값을 추가한 뒤 `sunsync-hub`와 `sunsync-api`를 재시작해야 원격 허브 모드가 활성화됩니다(설정 전까지는 안전하게 Local Fallback으로 동작).

```bash
# 32바이트 이상 랜덤 시크릿 생성 예시:
# python3 -c "import secrets; print(secrets.token_hex(32))"

SUNSYNC_HUB_BASE_URL=http://127.0.0.1:8002
SUNSYNC_HUB_SHARED_SECRET=<생성한_64자리_hex_시크릿>
SUNSYNC_HUB_KEY_ID=v1
SUNSYNC_HUB_TIMEOUT_SECONDS=2.5
SUNSYNC_HUB_DATABASE_URL=sqlite:////var/lib/sunsync/sunsync-hub.db
```

설정 반영:
```bash
sudo chmod 600 /etc/sunsync/sunsync.env
sudo systemctl restart sunsync-hub sunsync-api
```

### [권장 3] `backup.sh` 백업 대상 확인 (`sunsync-hub.db`)
`deploy/backup.sh`가 `/var/lib/sunsync/sunsync.db` 외에 허브 섀도우 DB(`/var/lib/sunsync/sunsync-hub.db`)도 함께 백업하도록 구성이 필요한지 확인하고 반영하세요. (참고: 허브 DB가 유실되더라도 백엔드 `POST /api/scenes/{scene_id}/hub-reconcile` 호출로 `sunsync.db` 기준 완전 복구가 가능합니다.)

### [선택 4] `/etc/caddy/Caddyfile` 수동 반영 (허브 경로 외부 노출 시에만)
- **기본 권장:** `:8002`는 **루프백(`127.0.0.1:8002`) 전용**으로 유지하고, 모든 브라우저는 `:8000`의 `/api/*`를 통해서만 접근합니다. 이 경우 기존 Google OAuth 로그인 및 `check_ownership` 격리가 그대로 유지되며 **Caddyfile·AWS Security Group 수정이 전혀 필요 없습니다**.
- 만약 별도 관제 UI나 WebSocket(`/hub/v1/ws`)을 외부 HTTPS로 노출해야 한다면, `deploy/Caddyfile`은 `update.sh`가 자동 덮어쓰지 않으므로 서버에서 직접 `/etc/caddy/Caddyfile`에 `handle_path /hub/*` 블록과 `import login`(`forward_auth 127.0.0.1:8000 /api/auth/check`)을 추가한 뒤 아래 명령으로 검증·리로드해야 합니다.
  ```bash
  caddy validate --config /etc/caddy/Caddyfile
  sudo systemctl reload caddy
  ```
- AWS Security Group에서는 포트 `8002`를 외부에 절대 개방하지 마세요(80/443만 개방).

### [선택 5] 실제 LG ThinQ Cloud 연동이 추후 필요해질 경우 (현재는 불필요)
현재 자체 호스팅 `sunsync-hub` 시뮬레이터(`:8002`)는 **외부 계정 로그인이나 서드파티 API 키가 전혀 필요 없습니다**.
단, 향후 실제 LG ThinQ Connect 클라우드 및 실물 ThinQ ON 허브와 직접 연동하고자 할 경우에는 PR로 해결할 수 없으며 사용자가 직접 다음을 준비해야 합니다.
1. LG ThinQ / LG Developer 일반 계정 로그인(소셜 SSO 불가) 후 `https://connect-pat.lgthinq.com/login`에서 **Personal Access Token(`thinqpat_...`)** 발급
2. 실물 ThinQ ON 허브 및 Matter/ThinQ 장치 등록, 필요 시 `x-api-key` 및 AWS IoT Core mTLS 인증서 발급
3. 발급받은 자격증명을 `/etc/sunsync/sunsync.env`에 저장(절대 저장소에 커밋 금지)
4. 참고: 현재 공개된 LG ThinQ Connect API에는 건축용 스마트 글라스 필름(SGF) 공식 디바이스 프로필이 없으므로, 실물 제어 시 Matter Window Covering(`0x0102`) / Level Control(`0x0008`) 브리지 어댑터를 허브 드라이버에 연결하는 방식을 권장합니다.

### [운영 6] 배포 타이밍 및 자동 업데이트 스위치 확인
- 데모 전날 코드 프리즈 일정과 `/etc/sunsync/autoupdate` 파일 유무, `/var/lib/sunsync/deployed-commit` 버전을 확인한 뒤 배포를 진행하세요.
