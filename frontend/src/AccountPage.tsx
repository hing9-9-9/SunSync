import type { CSSProperties, ReactNode } from "react";
import type { Account, Grade, Space } from "./model";
import { gradeCounts, grades } from "./model";
import { Arrow, Scribble, SunMark } from "./AwardPreview";
import { AuthLead } from "./AuthNotice";
import { GoogleButton } from "./GoogleMark";

const gradeOrder: Grade[] = ["A", "B", "C", "D"];

export function AccountOverview({
  account,
  spaces,
  serverState,
  onOpen,
  onRegister,
  onGuide,
  onRefresh,
  onLogout,
}: {
  account: Account;
  spaces: Space[];
  serverState: "loading" | "online" | "offline";
  onOpen: (s: Space) => void;
  onRegister: () => void;
  onGuide: () => void;
  onRefresh: () => void;
  onLogout: () => void;
}) {
  const regions = spaces.flatMap((s) => s.regions.filter((r) => r.grade !== "D"));
  const zones = spaces.reduce((n, s) => n + s.zones.length, 0);
  const buildings = [...new Set(spaces.map((s) => s.building || "건물 미지정"))];
  return (
    <div className="acct">
      <header className="acct-page-heading"><h1>내 계정</h1></header>
      <section className="acct-hero" aria-label="계정 정보">
        <div className="acct-id">
          <span className="acct-avatar" aria-hidden="true">{account.name.slice(0, 1)}</span>
          <div>
            <span className="acct-kicker">로그인 계정</span>
            <h2>{account.name}</h2>
            <p>{account.email}</p>
          </div>
        </div>
        <button type="button" className="acct-logout" onClick={onLogout}>
          로그아웃
        </button>
      </section>

      <dl className="ap-spec acct-spec">
        <div>
          <dt>등록한 공간</dt>
          <dd>{spaces.length}곳</dd>
        </div>
        <div>
          <dt>구역</dt>
          <dd>{zones}개</dd>
        </div>
        <div>
          <dt>보호 영역</dt>
          <dd>{regions.length}곳</dd>
        </div>
      </dl>

      <section className="acct-spaces" aria-labelledby="acct-spaces-title">
        <div className="acct-head">
          <h2 id="acct-spaces-title">
            공간과 보호 정보
          </h2>
          <div className="acct-sync">
            {serverState !== "online" && <span role="status">{serverState === "loading" ? "불러오는 중…" : "서버에 연결되지 않음"}</span>}
            <button type="button" className="text-button" disabled={serverState === "loading"} onClick={onRefresh}>
              새로고침
            </button>
          </div>
        </div>

        {spaces.length ? (
          buildings.map((b) => (
            <div className="acct-building" key={b}>
              <h3>{b}</h3>
              <ul>
                {spaces
                  .filter((s) => (s.building || "건물 미지정") === b)
                  .map((s) => {
                    const perGrade = gradeCounts(s);
                    const counts = gradeOrder.map((g) => [g, perGrade[g]] as const);
                    return (
                      <li className="acct-space" key={s.id}>
                        <div className="acct-space-open">
                          <span className="acct-space-name">
                            <strong>{s.name || "이름 없는 공간"}</strong>
                            <small>
                              {s.floor} · 구역 {s.zones.length}개
                            </small>
                          </span>
                          <span className="acct-grades" aria-label="등급별 가구·영역 수">
                            {counts.map(([g, n]) => (
                              <span key={g} className={`acct-grade grade-${g} ${n ? "" : "is-empty"}`} title={grades[g]}>
                                <i aria-hidden="true" />
                                <span>{g}</span><span className="acct-grade-count">{n}</span>
                              </span>
                            ))}
                          </span>
                        </div>
                        <button type="button" className="secondary acct-edit" onClick={() => onOpen(s)} aria-label={`${s.name} 공간 열기`}>
                          공간 열기
                        </button>
                      </li>
                    );
                  })}
              </ul>
            </div>
          ))
        ) : (
          <div className="acct-empty">
            <SunMark />
            <p>아직 등록한 공간이 없어요. 공간을 등록하면 보호 정보가 여기에 모입니다.</p>
            <button type="button" className="primary" onClick={onRegister}>
              공간 등록하기
            </button>
          </div>
        )}
      </section>

      <div className="acct-foot">
        <button type="button" className="ap-round-link" onClick={onGuide}>
          <Scribble />
          <span>사용 가이드</span>
          <b>
            <Arrow diagonal />
          </b>
        </button>
      </div>
    </div>
  );
}

export function AuthPanel({ hidden, brand, notice, onLogin }: { hidden: boolean; brand: ReactNode; notice?: string; onLogin: () => void }) {
  return (
    <main className={`auth acct-auth ${hidden ? "landing-auth-hidden" : ""}`}>
      <div className="acct-auth-copy">
        <span className="acct-kicker">
          {brand}
          SunSync 계정
        </span>
        <h1>내 공간을 확인하세요.</h1>
        <AuthLead description="Google 계정으로 로그인하고 저장한 공간을 확인하세요." message={notice ?? ""} />
        <GoogleButton className="acct-google" onClick={onLogin} />
      </div>
      <div className="acct-auth-visual" aria-hidden="true">
        <div className="acct-auth-glow" style={{ "--sun": 0.62 } as CSSProperties} />
        <div className="acct-wordmark">SunSync</div>
        <dl className="ap-spec acct-auth-spec">
          <div>
            <dt>상태</dt>
            <dd>
              <span className="ap-state-chip on">보호 중</span>
            </dd>
          </div>
          <div>
            <dt>다음 차단</dt>
            <dd className="is-sun">14:20</dd>
          </div>
          <div>
            <dt>등급</dt>
            <dd className="acct-grades">
              {gradeOrder.map((g) => (
                <span key={g} className={`acct-grade grade-${g}`}>
                  <i />
                  {g}
                </span>
              ))}
            </dd>
          </div>
        </dl>
      </div>
    </main>
  );
}
