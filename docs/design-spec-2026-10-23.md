# SunSync 화면 설계 명세 (수상작 레퍼런스 기반) — 2026-10-23

대상: Mac의 Codex (`~/Documents/Codex/SunSync-ui/frontend`)
작성: 클라우드 Claude (레퍼런스 조사·설계 담당). 이 문서는 설계만 담는다. 구현은 Codex가 한다.

기준: 웹 = Awwwards 심사 기준 (디자인 40 · 사용성 30 · 창의성 20 · 콘텐츠 10). 모바일 = Mobbin에 수록된 상위 앱의 검증된 패턴.

---

## 0. Codex 작업 규칙

### 바꾸지 않는 것
- 포인트 색 `#6660e5`, 글꼴 Noto Sans KR(400/500/700만 로드됨), 각진 도면(도면 도형 `border-radius: 0`), 모바일 배경 그라데이션.
- 모든 기능, 데이터 모델(`model.ts`), 서버 설정(`/api/ui/*`, localhost 전용), 화면 라우팅(`navigation.ts`의 page 목록).
- 커밋·푸시·파일 삭제 금지. 기존 클래스 이름은 유지하고, 새 스타일은 덮어쓰는 방식으로 추가.

### 이번 범위에서 빼는 것
- 랜딩용 실사 사진·영상, 실제 데이터로 다시 찍은 화면 캡처, 백엔드 연결(예측 엔진·QR 인식·필름 제어). 백엔드 마무리 단계에서 따로 한다.
- 웹 3D 보기(Three.js)는 의존성 추가가 필요하므로 이번에는 자리(2D/3D 전환 버튼의 3D를 "준비 중" 비활성)만 만든다.

### 작업 순서
1. 토큰 통합 (§2) → 2. 공통 컴포넌트 (§3) → 3. 웹 화면 (§4) → 4. 모바일 화면 (§5) → 5. 검사 (§6).
단계마다 `npm test`, `npm run lint`, `npm run build`를 통과시키고, 짧은 한국어 진행 보고를 남긴다.

### 관련 파일
| 영역 | 파일 |
|---|---|
| 진입·모드 분기 | `src/main.tsx` (`?mode=web` → `App`, `?mode=app` → `MobileApp`) |
| 공통 토큰 | `src/index.css` (`:root`) — 새 파일 `src/tokens.css`로 옮김 |
| 웹 셸·랜딩·목록·가이드 | `src/App.tsx`, `src/AwardSystem.tsx/.css`, `src/AwardPreview.tsx/.css`, `src/Website.tsx/.css` |
| 웹 편집·현황 | `src/App.tsx` (page `review`/`protect`/`detail`), `src/ReferencePreview.css`, `src/WorkspaceDesign.css`, `src/App.css` |
| 모바일 | `src/MobileApp.tsx`, `src/AwardMobile.css`, `src/WorkspaceDesign.tsx` |
| 도면·햇빛·필름 | `src/Plan.tsx`, `src/ShadingPlan.tsx` (`FilmGrid`, `ShadingTimeControl`), `src/sunlight.ts` |
| 촬영 | `src/SpatialCapture.tsx`, `src/captureCheck.ts` |
| 예시 데이터 | `src/exampleSpace.ts` |

---

## 1. 레퍼런스

> 확인 방법: Awwwards 항목은 각 수상 페이지에서 점수·태그를 직접 확인했다. Mobbin은 로그인 없이 화면을 열 수 없어서(클라우드에서 접근 차단), 검색에 잡힌 Mobbin 화면 링크와 해당 앱의 공개된 동작을 근거로 썼다. 링크는 Mobbin 로그인 상태에서 열린다.

### 웹 (Awwwards)
| # | 사이트 | 수상 | 가져올 것 | 적용 화면 |
|---|---|---|---|---|
| W1 | [Lidar Drone Scanning](https://www.awwwards.com/sites/lidar-drone-scanning) (Riotters) · [사이트](https://drone.riotters.com/) | SOTD 2026-10-06, 7.68 (디자인 7.67 · 사용성 7.58 · 창의성 7.71 · 콘텐츠 7.93), 동작 8.40 | 공간 스캔을 실시간 시각화로 보여 주는 첫 화면. 번호 붙은 섹션. 큰 숫자 + 작은 라벨의 계기판형 수치(“±2 cm”, “450 m”). 내용 전환 탭. 색은 밝은 회청(#dfe5e8)과 짙은 남회(#28363e) 두 가지뿐 | 랜딩 첫 화면, 공간 현황 수치 줄 |
| W2 | [Daylight Computer](https://basement.studio/showcase/daylight) (basement.studio) | Awwwards SOTD · Developer Award, FWA SOTD (2024) | “햇빛” 제품을 차분하고 따뜻하게, 군더더기 없이. 빠른 첫 화면 | 랜딩 톤, 햇빛 색 |
| W3 | [Kononenko Architectural Bureau](https://www.awwwards.com/sites/kononenko-architectural-bureau) | SOTD 2026-08-23, 7.27, 동작 8.20 | 흑백 미니멀, 굵은 글씨 위계, 엄격한 격자. 건축 사이트의 구조감 | 내 공간 목록, 편집 화면 틀 |
| W4 | [TOSEN Architecture Studio](https://www.awwwards.com/sites/tosen-architecture-studio) | Honorable Mention 2026-02-22 | 정밀함·공간 스토리텔링, 3D/360 공간 보기 | 편집 화면의 2D/3D 전환 자리, 도면 표현 |
| W5 | [ATMOS® Lamp](https://www.awwwards.com/sites/atmos-r-lamp-by-ayocin) | SOTD 7.58, 동작 8.40 | “빛”이 화면을 지나가는 몰입형 연출 | 랜딩의 스크롤 연동 햇빛 이동 |

### 모바일 (Mobbin)
| # | 앱 화면 | 가져올 것 | 적용 화면 |
|---|---|---|---|
| M1 | [Philips Hue – Rooms List](https://mobbin.com/explore/screens/4be9cd47-e4e5-4bb7-a270-bc1f3e04c490) | 방마다 카드 하나, 카드 안에 현재 상태가 바로 보임. 탭하면 방 상세 | 홈·내 공간 목록 |
| M2 | [(Not Boring) Weather – Hourly Forecast](https://mobbin.com/explore/screens/010e9637-bca5-4ea0-a7fb-2f28c190b1ce) · [Home Flow](https://mobbin.com/explore/flows/a03280f4-2e36-4d8b-bc54-96432699e2fe) | 큰 숫자 하나가 화면을 이끌고, 시간대를 가로로 훑으면 화면 전체가 그 시각으로 바뀜 | 공간 현황의 시간 슬라이더 |
| M3 | [Flighty – Flights Home](https://mobbin.com/explore/screens/f28cca69-a528-4c55-9170-5be260ef981a) · [Flight Alerts](https://mobbin.com/explore/screens/0b1b9d14-b576-44ea-b93b-c40ba046fbfd) | 다음 일정까지 남은 시간을 크게, 상태를 색 점 하나로. 타임라인 위의 현재 위치 | 보호영역별 차단 예정 시각 |
| M4 | [Revolut – Document Scan (Front)](https://mobbin.com/explore/screens/2398046d-b161-4994-8bef-d03dd3da3826) · [Scanning 패턴](https://mobbin.com/explore/mobile/flows/scanning) · [Halide 온보딩](https://mobbin.com/explore/flows/83c079a6-0e31-4167-80e3-edcd519bb94a) | 화면 가득 카메라 + 맞춰 넣을 틀 + 한 줄 지시문 + 결과 즉시 판정(통과/다시 찍기) | QR 모서리 4장 촬영 |
| M5 | [Airbnb – Creating a listing](https://mobbin.com/explore/flows/ac0a721e-274d-4b18-97eb-403b4c59b394) | 한 화면에 질문 하나, 위쪽 진행 막대, 아래쪽 고정 “다음” 막대, 왼쪽 “뒤로” | 공간 등록 |
| M6 | Mobbin UI 요소: [Slider](https://mobbin.com/explore/mobile/ui-elements/slider) · [Stepper](https://mobbin.com/explore/mobile/ui-elements/stepper) · [Progress indicator](https://mobbin.com/explore/mobile/ui-elements/progress-indicator) | 컨트롤 크기·간격 기준 | 공통 컴포넌트 |

---

## 2. 기초 토큰 (새 파일 `src/tokens.css`)

현재 토큰이 `index.css`, `AwardSystem.css`, `AwardMobile.css`에 따로 있고 값이 다르다(예: muted가 `#686d7a`, `#746d7e`, `#807289` 세 가지). 그리고 `AwardSystem.css`에 아직 11px 15곳, 12px 47곳이 남아 있다. 하나로 합친다.

- `src/tokens.css`를 만들고 `main.tsx`에서 `index.css`보다 먼저 import한다.
- `AwardSystem.css`, `AwardMobile.css`, `AwardPreview.css`의 하드코딩 색·글자 크기를 아래 변수로 바꾼다. 기존 변수 이름(`--brand`, `--accent`, `--muted` 등)은 별칭으로 남긴다.

### 2.1 색
| 토큰 | 값 | 용도 | 근거 |
|---|---|---|---|
| `--c-accent` | `#6660e5` | 주요 버튼, 선택, 보호영역, 데이터 강조 | 고정 |
| `--c-accent-hover` | `#5751d3` | 눌림·호버 | 기존 |
| `--c-accent-soft` | `#efeffc` | 선택된 칩·행 배경 | 기존 |
| `--c-ink` | `#1f1d26` | 본문·제목 | W3 흑백 대비 |
| `--c-ink-2` | `#4a4654` | 보조 본문 | |
| `--c-muted` | `#6b6676` | 라벨·캡션 (흰 배경 대비 5.6:1) | 세 가지 값을 하나로 |
| `--c-line` | `#e6e3ec` | 구분선 | |
| `--c-line-strong` | `#c9c4d3` | 입력칸 테두리 | |
| `--c-paper` | `#f7f6fa` | 섹션 배경(웹) | W1 두 톤 원칙 |
| `--c-surface` | `#ffffff` | 카드 | |
| `--c-sun` | `#f2b544` | 햇빛 영역 채움(도면 위 투명도 0.35) | W2 |
| `--c-sun-soft` | `#fff3d9` | 햇빛 관련 배경 | W2 |
| `--c-sun-ink` | `#8a5a00` | 햇빛 관련 글자(대비 5.9:1) | |
| `--c-ok` | `#2f8f5b` | 저장됨·보호 중 | |
| `--c-warn` | `#b7791f` | 곧 차단·예측 오래됨 | |
| `--c-danger` | `#b9473f` | 오류·차단 불가 | |

등급 색(보라 한 계열 + D만 회색, 도면 채움과 칩에 공통):
A `#6660e5` 진하게 채움 · B `#8c87ec` · C `#bdbaf4` · D 회색 점선 테두리(`#9a96a3`, 채움 없음).

### 2.2 글자 (Noto Sans KR, 400/500/700만)
웹:
| 토큰 | 크기/줄높이 | 굵기 | 자간 | 용도 |
|---|---|---|---|---|
| `--t-display` | `clamp(48px, 6.2vw, 104px)` / 1.04 | 700 | -0.035em | 랜딩 첫 문장만 |
| `--t-h1` | 44px / 1.2 | 700 | -0.03em | 페이지 제목 |
| `--t-h2` | 32px / 1.25 | 700 | -0.02em | 섹션 제목 |
| `--t-h3` | 22px / 1.4 | 500 | -0.01em | 카드·패널 제목 |
| `--t-body-lg` | 19px / 1.7 | 400 | -0.005em | 랜딩 본문 |
| `--t-body` | 16px / 1.65 | 400 | 0 | 앱 본문 |
| `--t-label` | 14px / 1.4 | 500 | 0 | 입력 라벨, 버튼 |
| `--t-caption` | 13px / 1.45 | 400 | 0 | 보조 설명 (웹 최소 크기) |
| `--t-metric` | 56px / 1 | 700 | -0.03em | 계기판 숫자 (W1) |

모바일:
| 토큰 | 크기/줄높이 | 굵기 | 용도 |
|---|---|---|---|
| `--m-large-title` | 30px / 1.25 | 700 | 화면 첫 제목 |
| `--m-title` | 22px / 1.35 | 700 | 섹션 |
| `--m-headline` | 17px / 1.45 | 500 | 카드 제목 |
| `--m-body` | 16px / 1.6 | 400 | 본문 |
| `--m-sub` | 14px / 1.5 | 400 | 보조 |
| `--m-caption` | 12px / 1.4 | 500 | 탭바 라벨·단위만 (모바일 최소) |
| `--m-metric` | 48px / 1 | 700 | 남은 시간·현재 시각 (M2, M3) |

숫자는 모두 `font-variant-numeric: tabular-nums;` (시간·치수가 바뀔 때 흔들리지 않게).

### 2.3 간격·크기
- 4px 격자: `--s-1 4` `--s-2 8` `--s-3 12` `--s-4 16` `--s-5 20` `--s-6 24` `--s-8 32` `--s-10 40` `--s-12 48` `--s-16 64` `--s-20 80` `--s-30 120`.
- 웹 섹션 위아래 여백: 랜딩 `--s-30`(120px), 앱 화면 `--s-12`(48px).
- 웹 콘텐츠 최대폭 1280px, 좌우 여백 `clamp(24px, 5vw, 80px)`, 12열 격자 · 열 간격 24px.
- 모바일 좌우 여백 20px, 카드 사이 12px, 섹션 사이 32px.
- 터치·클릭 영역 최소 44×44px. 입력칸·선택칸·버튼 높이: 웹 48px(작은 버튼 40px), 모바일 52px.

### 2.4 모서리·그림자
- 웹: 입력칸·버튼 8px, 카드 12px, 칩 999px. 도면과 도면 위 도형 0.
- 모바일: 입력칸 12px, 카드 16px, 주요 버튼 999px(현재 알약 모양 유지), 바텀시트 위쪽 20px. 도면 0.
- 그림자는 떠 있는 것에만: 바텀시트·토스트·드롭다운 `0 8px 32px rgba(31,29,38,.12)`. 카드에는 그림자 대신 1px `--c-line`.

### 2.5 움직임
| 토큰 | 값 | 용도 |
|---|---|---|
| `--d-press` | 120ms | 눌림 (scale 0.97) |
| `--d-ui` | 200ms | 호버, 칩·탭 전환 |
| `--d-screen` | 320ms | 화면 전환, 바텀시트 |
| `--d-reveal` | 640ms | 랜딩 등장 |
| `--e-out` | `cubic-bezier(.2,.8,.2,1)` | 등장·열림 |
| `--e-inout` | `cubic-bezier(.65,0,.35,1)` | 위치 이동 |

- 화면 전환(모바일): 앞으로 가면 새 화면이 오른쪽 24px에서 들어오며 불투명해짐, 뒤로 가면 반대. 웹은 페이드 200ms만.
- 랜딩 등장: 섹션 제목 → 본문 → 그림 순서로 80ms 간격, 아래 24px에서 올라옴. 한 번만 재생.
- `prefers-reduced-motion: reduce`이면 이동·크기 변화는 끄고, 120ms 이하 페이드만 남긴다.

---

## 3. 공통 컴포넌트

| 컴포넌트 | 명세 | 근거 |
|---|---|---|
| Button | primary(보라 채움, 흰 글자), secondary(흰 바탕 + `--c-line-strong` 1px), ghost(글자만). 높이 웹 48/40, 모바일 52. 글자 `--t-label`. 눌림 scale .97 `--d-press`. 비활성은 투명도 .4가 아니라 배경 `--c-paper` + 글자 `--c-muted` | M6 |
| Field / Select | 라벨은 칸 위 14px/500, 칸 높이 48(웹)·52(모바일), 테두리 `--c-line-strong`, 포커스 시 2px `--c-accent` 링. 선택칸은 기본 모양 없이 직접 그린 화살표(지난 Safari 수정 유지). 같은 줄의 칸·버튼은 높이 동일 | 기존 정렬 규칙 |
| Segmented control | 2~4개 선택(구역, 2D/3D, 가구 선택/영역 그리기). 높이 40, 바탕 `--c-paper`, 선택된 칸만 흰 바탕 + 미세 그림자, 이동 `--d-ui` | M2 |
| Chip (등급) | A~D. 높이 28, 좌측 8px 색 사각형(각진 모양, 도면과 같은 색) + “A 최우선” 형식 | 등급 색 |
| Metric | 위에 라벨(`--t-caption`, `--c-muted`), 아래 큰 숫자(`--t-metric` / `--m-metric`), 단위는 숫자의 45% 크기로 붙여 씀(“14:20”, “3 곳”) | W1, M3 |
| TimeScrubber | 06:00~19:00 가로 막대. 시간별로 햇빛 세기 막대(높이 4~24px, `--c-sun`), 현재 위치 손잡이(지름 24, 흰 바탕 + 보라 테두리 2px), 위에 선택 시각을 `--t-h3`로 표시. 드래그·키보드(←/→ 10분, Shift 1시간) 모두 지원. `ShadingTimeControl`을 이 모양으로 바꾼다 | M2 |
| Countdown row | 보호영역 한 줄: 왼쪽 등급 칩 + 이름, 오른쪽 “14:20 차단” + 그 아래 “2시간 14분 후”(`--c-warn`, 30분 이내면 굵게). 차단 중이면 초록 점 + “차단 중 · 16:05까지” | M3 |
| Status dot | 8px 원 + 한 단어(저장됨/보호 중/예측 오래됨/연결 끊김). 색만으로 구분하지 않게 항상 글자 동반 | M3 |
| Bottom action bar (모바일) | 화면 하단 고정, 높이 52 버튼 + 위아래 12px + 안전영역. 위쪽 1px `--c-line`, 흰 바탕 92% + blur 16px | M5 |
| Bottom sheet (모바일) | 반쯤 열림(높이 40%)·전체 두 단계. 손잡이 36×4px | M2 |
| Toast | 하단 중앙, 3초, 실행취소 버튼 선택 | |
| Empty state | 작은 선 그림 1개(기존 `public/design` SVG) + 한 문장 + 버튼 1개 | |

---

## 4. 웹 화면 (Awwwards 기준, 1440px 기준 설계 · 1024/768/390 대응)

### 4.1 공통 헤더 (`AwardHeader`)
- 높이 72px, 콘텐츠 최대폭에 맞춤. 왼쪽 로고, 가운데 메뉴(소개·내 공간·사용 방법, 16px/500, 현재 메뉴는 아래 2px 보라 선), 오른쪽 “내 계정” 또는 “로그인”.
- 스크롤 8px 이상이면 흰 바탕 92% + blur + 아래 1px 선. 위로 스크롤하면 다시 투명.

### 4.2 랜딩 (`AwardLanding`, `AwardPreview`) — 창의성 20점을 여기서 얻는다
목표: 첫 화면 3초 안에 “창의 칸이 햇빛을 따라 꺼진다”를 보여 준다. (W1의 실시간 스캔 첫 화면, W5의 빛 연출)

1. **첫 화면(높이 100vh)**: 왼쪽 5열에 문장(`--t-display`) “햇빛은 들이고, / 책은 지킨다.” + 한 줄 설명(`--t-body-lg`) + 보라 버튼 “내 공간 설계하기” + 글자 버튼 “사용 방법 보기”. 오른쪽 7열에 **살아 있는 도면**: `Plan` + `FilmGrid`를 `exampleSpace`로 그린다(사진 아님).
   - 스크롤 0~100vh 동안 시각이 06:00 → 18:00으로 흐르고, 햇빛 영역(`sunlightPolygon`)이 바닥을 지나가며, 서가에 닿을 칸만 보라로 채워진다. 스크롤과 연동(`scroll-timeline` 지원 시) 또는 `requestAnimationFrame` + `IntersectionObserver`.
   - 도면 오른쪽 위에 계기판 숫자 2개(W1): “지금 시각 10:40”, “차단 중인 칸 18 / 100”.
   - 움직임 줄이기 설정이면 12:00 고정 화면.
2. **문제 → 해결 (번호 섹션 01~03, W1)**: 각 섹션 왼쪽에 번호(`--t-caption`, `--c-muted`)와 제목(`--t-h2`), 오른쪽에 그림. 01 “직사광선은 책을 바래게 한다” · 02 “창 전체를 가리면 열람실이 어두워진다” · 03 “SunSync는 10cm 칸 단위로 필요한 곳만 가린다”(필름 격자 확대 그림, `FilmGrid` 재사용).
3. **흐름 3단계**: 앱으로 촬영 → 웹에서 보호영역 지정 → 차단 예정 시각 확인. 가로 3열 카드, 각 카드에 실제 컴포넌트를 축소해 넣는다(스크린샷 아님). 예시 이름은 `exampleSpace`의 “SunSync 도서관 · 2층 열람실” 사용(테스트 이름 노출 금지).
4. **마지막 행동 유도**: `--c-paper` 바탕, 가운데 큰 문장 + 보라 버튼. 그 아래 푸터.
- 섹션 배경은 흰색과 `--c-paper`를 번갈아(W1 두 톤). 장식 선·영문 소제목은 다시 넣지 않는다.

### 4.3 내 공간 목록 (`AwardSpaces`) — W3
- 페이지 제목 줄: 왼쪽 “내 공간”(`--t-h1`) + 공간 수, 오른쪽 “+ 공간 등록” primary.
- 필터 줄(높이 48 하나로): 검색칸(남는 폭), 건물 선택(240px). 아래 카드 격자의 좌우 끝선과 정확히 맞춤.
- 카드 3열(1440) / 2열(1024) / 1열(390). 카드 = 위쪽 도면 썸네일(4:3, `--c-paper` 바탕, 각진 도면) + 아래 정보: 공간 이름(`--t-h3`), 건물·층(`--t-caption`), 상태 줄(Status dot + “다음 차단 14:20” 또는 “보호영역 없음”).
- 호버: 카드 테두리 `--c-line-strong`, 썸네일 안 햇빛 영역이 한 번 지나감(600ms). 클릭 영역은 카드 전체.

### 4.4 공간 등록 (page `register`)
- 왼쪽 단계 목록(240px, 고정): 1 공간 정보 · 2 창 정보 · 3 위치 · 4 휴대폰 촬영. 현재 단계는 보라 원 숫자, 완료는 체크.
- 가운데 폼(최대 640px): 묶음마다 제목(`--t-h3`) + 2열 입력칸. 창 정보 옆에는 입력값으로 그린 창·필름 칸 미리보기(입력과 동시에 바뀜).
- 하단 행동 줄: 왼쪽 “이전”, 오른쪽 “다음” primary. 저장 안 된 변경이 있으면 “변경사항 있음” 점 표시.

### 4.5 휴대폰 촬영 안내 (page `capture`, 웹)
- 2열: 왼쪽 안내 3단계(번호 + 한 문장 + 작은 그림), 오른쪽 연결 QR과 링크 복사 버튼, 아래 연결 상태(Status dot: 기다리는 중 / 연결됨 / 사진 2/4 받음). `DeviceHandoff` 재사용.

### 4.6 편집 (page `review` / `protect`) — W3 틀, W4 공간 표현
- 상단 바(높이 64, 화면 폭 전체): 왼쪽 공간 이름 + 저장 상태, 가운데 단계(공간 정보 · 휴대폰 측정 · 가구·보호구역), 오른쪽 “저장” primary.
- 본문 2열: 왼쪽 도면 캔버스(남는 폭, 화면 높이에 맞춤, 스크롤 없음) / 오른쪽 속성 패널 360px(따로 스크롤).
- 캔버스 위 도구 줄(높이 40, 한 줄): 구역 Segmented control · “+ 구역” · 구분선 · “가구 선택 / 영역 그리기” Segmented · 오른쪽 끝 “2D / 3D(준비 중)” Segmented.
- 캔버스: 바탕 `--c-paper`, 방 테두리 2px `--c-ink`, 가구 1px 테두리, 보호영역은 등급 색 채움 25% + 1.5px 테두리, 햇빛은 `--c-sun` 35%. 왼쪽 위 치수 “8 × 6 m”, 오른쪽 위 방위 “N ↑”. 선택된 가구는 2px 보라 테두리 + 네 모서리 8px 손잡이(각진 사각형).
- 속성 패널: 접을 수 있는 묶음(가구 · 위치와 크기 · 보호면 · 등급). 숫자칸은 2열, 단위는 칸 안 오른쪽에 회색. 등급은 A~D 4칸 Segmented + 아래 한 줄 설명(“A: 직사광이 닿는 경로를 전부 막아요”).
- 현재 화면 문제: 패널이 화면 오른쪽 밖으로 잘림(1124px 캡처 기준). 1280 미만에서는 패널을 320px로 줄이고, 1024 미만에서는 캔버스 아래로 내린다.

### 4.7 공간 현황 (page `detail`) — W1 계기판 + M3 타임라인
- 제목 줄: 공간 이름(`--t-h1`), 건물·층, 오른쪽 “편집” secondary + “예측 다시 계산” secondary(누르면 아이콘 회전 + “10:42 갱신” 표시).
- 계기판 줄(4칸, Metric): 보호영역 수 · 다음 차단 시각 · 오늘 차단 시간 합계 · 예측 기준 시각.
- 본문 2열(7:5): 왼쪽 도면(선택 시각의 햇빛 + 보호영역) / 오른쪽 필름 칸 격자(`FilmGrid`, 칸 10cm, 가려질 칸 보라).
- 그 아래 **하루 타임라인**(폭 전체): 보호영역마다 한 줄, 06~19시 가로축에 차단 구간을 보라 막대로. 위에 TimeScrubber 하나, 손잡이를 움직이면 도면·격자·타임라인 세로선이 함께 바뀐다. D 등급 줄은 “차단 안 함” 회색 글자.
- 예외 상태는 같은 자리에서 안내: 창 미입력 → “창 정보를 넣으면 예측할 수 있어요” + “창 정보 입력” 버튼. A 등급인데 다 막을 수 없음 → 해당 줄에 `--c-danger` 점 + “일부 시간 완전 차단 불가”.

### 4.8 사용 방법 (`AwardGuide`)
- 왼쪽 목차 280px(번호 + 제목, 현재 항목 보라 글자 + 왼쪽 2px 보라 선, 배경 채움 없음). 오른쪽 본문: 제목 `--t-h2`, 그림 16:9, 본문 `--t-body-lg`. 아래 “이전 / 다음” 큰 카드 2개(다음 단계 제목 미리보기).
- 현재 화면에서 목차 배경이 꽉 찬 보라라 무겁다 → 선으로 바꾼다.

### 4.9 내 계정
- 가운데 정렬 카드 560px: 원형 프로필 48px, 이름, 이메일, 연결 계정, 로그아웃(ghost, `--c-danger` 글자).

---

## 5. 모바일 화면 (Mobbin 패턴, 390×844 기준, 360~430 대응)

공통: 상단은 큰 제목(Large title, 스크롤하면 17px 가운데 제목으로 줄어듦). 하단 탭바 4개(홈 · 공간 · 가이드 · 더보기, 아이콘 24 + 라벨 12). 주요 행동은 항상 Bottom action bar(엄지 영역). 모든 목록 행 높이 ≥ 56.

### 5.1 홈 / 내 공간 (`WorkspaceHome`, `WorkspaceCards`) — M1 Hue
- 위: 인사 대신 오늘 요약 한 줄 카드 “오늘 3곳 보호 중 · 다음 차단 14:20”(Metric 작게).
- 건물별 묶음 제목(`--m-title`) 아래 공간 카드(폭 전체, 높이 96): 왼쪽 64×64 도면 썸네일(각진), 가운데 공간 이름 `--m-headline` + 층 `--m-sub`, 오른쪽 상태(Status dot + “14:20 차단”). 카드 전체가 눌림 영역, 눌림 scale .98.
- 공간이 없으면 Empty state + “공간 등록”.
- 오른쪽 위 “+” 원형 버튼 44px.

### 5.2 공간 등록 (page `register`) — M5 Airbnb
- 한 화면에 묶음 하나: ① 이름(건물·층·공간) ② 방 크기 ③ 창 정보 ④ 위치(현재 위치 자동, 지도 없이 주소 한 줄 + “다시 찾기”) ⑤ 구역.
- 위쪽 진행 막대 3px(보라, 단계마다 늘어남, `--d-screen`), 왼쪽 위 뒤로 화살표. 제목은 질문 문장(“창은 어떤 크기인가요?”).
- 하단 Bottom action bar: “다음”(필수값이 없으면 비활성). 숫자 입력은 `inputmode="decimal"`.

### 5.3 QR 모서리 촬영 (page `capture`, `SpatialCapture`) — M4 Revolut·Halide
- 화면 가득 카메라(또는 사진 선택). 위쪽 반투명 바: “책장 01 · 모서리 2/4”, 닫기.
- 가운데 맞춤 틀(정사각, 흰 2px 모서리 표시만) + 아래 한 줄 지시 “필름 QR이 틀 안에 다 들어오게 찍어 주세요”.
- 왼쪽 아래 작은 가구 평면(56px)에 지금 찍을 모서리를 보라 점으로 표시(앞 왼쪽 → 앞 오른쪽 → 뒤 오른쪽 → 뒤 왼쪽).
- 촬영 후 0.3초 안에 판정 띠: 통과(초록 체크 “마커 전체 인식”) / 다시 찍기(원인별: 흔들림 · 어두움 · 마커 일부만 보임, `captureCheck.ts` 결과 사용) + “다시 찍기” 버튼.
- 아래 썸네일 4칸(찍은 것은 사진, 남은 것은 점선 칸). 4장 다 되면 Bottom action bar “전송하고 도면 만들기”.

### 5.4 도면 생성 중 (page `processing`)
- 가운데 도면 윤곽이 선부터 그려지는 애니메이션(선 길이 애니메이션 1.2초 반복) + 진행률 숫자(`--m-metric`) + 단계 문구(사진 확인 → 위치 계산 → 도면 생성). 취소는 글자 버튼.

### 5.5 도면 확인 (page `review`, 앱)
- 도면(폭 전체, 각진) + 아래 바텀시트(반쯤 열림)에 가구 목록. 가구를 누르면 도면에서 강조 + 시트에 치수. 하단 “저장하고 웹으로 보내기”.

### 5.6 공간 현황 (page `detail`) — M2 Weather + M3 Flighty
- 맨 위 큰 상태 블록: 선택 시각(`--m-metric`, “10:40”) + 한 줄 “서가 A 차단 중 · 11:25까지”. 아래 TimeScrubber(폭 전체, 엄지로 훑기).
- 그 아래 도면 카드(현재 캡처의 구조 유지) + 범례 한 줄.
- 바텀시트(반쯤 열림): “보호영역” 제목 + Countdown row 목록 + “예측 다시 계산”. 위로 올리면 필름 칸 격자.
- 현재 화면 문제: 하단 버튼이 도면 범례 아래 칩 줄을 가린다(캡처 기준). 스크롤 콘텐츠 아래에 Bottom action bar 높이만큼 여백(`padding-bottom: calc(76px + env(safe-area-inset-bottom))`)을 둔다.
- 앱은 보호영역 조회만(편집 없음, 요구사항 MAR5).

### 5.7 가이드
- 목록: 번호 원 + 제목 + 한 줄 설명, 행 높이 64. 상세: 위 그림 16:10, 제목 `--m-title`, 본문 `--m-body`, 아래 “이전 / 다음” Bottom action bar.

### 5.8 더보기 / 내 계정 / 로그인
- 더보기: 묶음 목록(계정 · 공간 · 도움말), 행 56, 오른쪽 화살표.
- 로그인: 로고 + 한 문장 + “Google로 계속하기”(흰 바탕 테두리 버튼) 하나. 데모임을 작은 글씨로.

---

## 6. 완료 검사 (Codex가 마지막에 실행)

1. `npm test`, `npm run lint`, `npm run build` 통과. 백엔드 테스트도 기존대로 통과.
2. 기존 정렬 측정 스크립트를 웹 1440/1024/390, 앱 390/360에서 모든 page(home, spaces, register, capture, processing, review, protect, detail, guide, more, account)에 대해 실행 → 0건.
3. 추가 측정(스크립트에 넣기):
   - 웹에서 13px 미만 글자 0건, 모바일에서 12px 미만 0건.
   - 화면에 쓰인 글자 크기 종류가 토큰 표의 값만 나오는지(그 외 값 목록 출력).
   - 클릭/터치 대상 44×44 미만 0건.
   - 본문 글자 대비 4.5:1 미만 0건.
   - 하단 고정 막대가 콘텐츠를 가리는 곳 0건(마지막 요소 아래끝 ≤ 막대 위끝).
   - 1440에서 편집 화면 속성 패널이 화면 밖으로 나가지 않음.
4. `prefers-reduced-motion: reduce`로 랜딩·화면 전환 확인: 이동 없음.
5. 화면 캡처를 `docs/spec-pass-2026-10-23/`에 저장(웹 9장, 모바일 9장). 랜딩에 “저장 확인실”, “FR06 연동 테스트” 같은 테스트 이름이 보이지 않을 것.
6. 이 문서의 각 절 번호(예: “4.6 완료”)로 진행 보고.

---

## 7. 참고 출처
- Awwwards 심사 비율: 각 수상 페이지 표기(디자인 40% · 사용성 30% · 창의성 20% · 콘텐츠 10%).
- Lidar Drone Scanning: https://www.awwwards.com/sites/lidar-drone-scanning , https://drone.riotters.com/
- Daylight (basement.studio): https://basement.studio/showcase/daylight-simplicity-in-motion
- Kononenko Architectural Bureau: https://www.awwwards.com/sites/kononenko-architectural-bureau
- TOSEN: https://www.awwwards.com/sites/tosen-architecture-studio
- ATMOS Lamp: https://www.awwwards.com/sites/atmos-r-lamp-by-ayocin
- Mobbin 링크는 §1 표 참고(로그인 필요).
