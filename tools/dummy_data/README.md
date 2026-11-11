# 더미 공간 시드 (초안)

3D 화면, FR-01(햇빛 예측), FR-02(차광 계획)를 점검하기 위한 더미 공간을 서버에 넣는 도구입니다.
표준 라이브러리만 사용합니다(Python 3.11 이상). 시나리오는 `scenarios.json`, 넣는 도구는 `seed_dummy_spaces.py`입니다.

## 쓰는 법

```bash
# 어떤 공간이 들어가는지만 확인
python seed_dummy_spaces.py --dry-run

# 로컬 서버에 넣고, FR-01 결과가 기대와 같은지 비교
python seed_dummy_spaces.py --base-url http://127.0.0.1:8000 --suffix v1 --check

# 일부만 / 계산 시간이 긴 S6a 제외
python seed_dummy_spaces.py --suffix v1 --only S1,S3 --check
python seed_dummy_spaces.py --suffix v1 --skip-heavy --check

# 지우기 (서버가 삭제한 ID를 기억해서 같은 ID로 다시 만들 수 없음 -> 다음엔 --suffix v2)
python seed_dummy_spaces.py --suffix v1 --delete
```

배포 서버는 구글 로그인을 쓴다. 넣을 때는 로그인한 브라우저의 세션 쿠키(이름=값, 예: `__Host-sunsync_session=...`)를 환경변수 `SUNSYNC_COOKIE`로만 전달하고 저장소, 이슈, 대화에 적지 않는다. 공간은 그 계정의 것이 된다. 세션은 30분 동안 쓰지 않으면 끝나므로 쿠키를 복사한 뒤 바로 실행한다.

```bash
SUNSYNC_COOKIE='__Host-sunsync_session=...' \
python seed_dummy_spaces.py --base-url https://서버주소 --allow-remote --suffix v1 --skip-heavy
```

로그인 전의 Caddy 기본 인증(`SUNSYNC_USER`, `SUNSYNC_PASSWORD`)도 두 값을 모두 주면 계속 보낸다. 지금 서버에는 그 잠금이 없다.

로컬이 아닌 주소에는 `--allow-remote`가 없으면 거절합니다(모두가 보는 서버에 실수로 쓰지 않기 위함).

## 시나리오

| 키 | 확인하려는 것 | 기대 결과 (FR-01, 하루 24시간 30분 간격) |
|---|---|---|
| S1 | 남쪽 창, 등급 A~C, 보호 없는 가구(D 칩), 상단 보호 | 12/21 햇빛 있음, 6/21 없음 |
| S2 | 북쪽 창, 북쪽 후면 보호 | 6/21 있음, 12/21 없음 |
| S3, S4 | 동쪽, 서쪽 창 (오전, 오후) | 12/21 있음 |
| S5 | 작은 방, 마커보다 작은 창 | 있음 |
| S6a | 규모 시험(서가 4개, 큰 창) | 있음 (summary 약 74 KB, diagnostic은 수십 MB) |
| S6b | 한도 시험(서가 9개) | FR-01 422 |
| S7a, S7b | 시드니(남반구) 남쪽, 북쪽 창 | 없음, 있음 |
| S8a | 북쪽 미확인, 층 "지하 1층" | 서버는 계산함(막는 곳은 화면) |
| S8b | 구역 2개, 창 없음 | 없음, 차광 계획 없음 |
| S9 | 겹치는 가구 | 저장 201, FR-01 422 |

## 로그인과 소유자 (#105, #106)

- `scenarios.json`은 그대로 쓴다.
- 로그인이 켜진 서버에서는 쿠키 없는 요청이 401이다. 쿠키로 넣은 공간은 그 계정의 것이고, 다른 계정에는 보이지 않는다.
- 로그인 전에 넣은 공간은 "소유자 없음"이 되어 어느 목록에도 안 보인다. 옮기지 말고 새 `--suffix`로 다시 넣는다. 한 계정에 한꺼번에 주려면 `python -m app.assign_owner` ([auth.md](../../docs/auth.md))를 쓴다.

## 알려진 한계

- 회전된 측정 가구(등록 서비스로 만든 가구)는 이 도구에 없습니다. 등록 서비스의 합성 예제로 따로 만들어야 합니다.
- 자유 영역 보호면(가구에 붙지 않은 영역)은 포함하지 않았습니다.
- FR-01 확인은 "햇빛이 있는지/없는지/오류인지"만 비교합니다. 차광 칸 수는 비교하지 않습니다.
