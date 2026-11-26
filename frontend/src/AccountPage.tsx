import type { CSSProperties, ReactNode } from "react";
import type { Account, Grade, Space } from "./model";
import { guideExtras, guideSteps } from "./guideContent";
import { Arrow, SunMark } from "./AwardPreview";
import { AuthLead } from "./AuthNotice";
import { GoogleButton } from "./GoogleMark";
import { BUILD_LABEL } from "./buildLabel";

const gradeOrder: Grade[] = ["A", "B", "C", "D"];

export function AccountOverview({
  account,
  spaces,
  onOpen,
  onRegister,
  onGuide,
  onLogout,
  onSpaces,
}: {
  account: Account;
  spaces: Space[];
  serverState?: "loading" | "online" | "offline";
  onOpen: (s: Space) => void;
  onRegister: () => void;
  onGuide: (index?: number) => void;
  onRefresh?: () => void;
  onLogout: () => void;
  onSpaces: () => void;
}) {
  const protectedSpaces = spaces.filter((s) => s.regions.some((r) => r.grade !== "D"));
  const regions = spaces.flatMap((s) => s.regions.filter((r) => r.grade !== "D"));
  // Reference topics open the matching section of the guide: 등급 기준, 차광 필름 (after the six steps), 촬영 방법 (step 3).
  const help = [
    { title: guideExtras[0].title, text: "가구마다 햇빛을 막는 정도를 A~D로 정해요.", index: guideSteps.length, grades: true },
    { title: guideExtras[1].title, text: "창문 필름이 칸마다 햇빛을 막는 방식이에요.", index: guideSteps.length + 1, grades: false },
    { title: "촬영 방법", text: "창문 마커와 가구를 정확히 찍는 법이에요.", index: 2, grades: false },
  ];
  return (
    <div className="acct acct-v2">
      <header className="acct-page-heading"><h1>내 계정</h1></header>
      <div className="acct-layout">
        <aside className="acct-profile" aria-label="계정 정보">
          <span className="acct-avatar" aria-hidden="true">{account.name.slice(0, 1)}</span>
          <h2>{account.name}</h2>
          <p>{account.email}</p>
          <button type="button" className="acct-logout" onClick={onLogout}>
            로그아웃
          </button>
        </aside>

        <div className="acct-main">
          <dl className="ap-spec acct-spec">
            <div><dt>등록한 공간</dt><dd>{spaces.length}곳</dd></div>
            <div><dt>보호 설정 완료</dt><dd>{protectedSpaces.length}곳</dd></div>
            <div><dt>보호 영역</dt><dd>{regions.length}곳</dd></div>
          </dl>

          <section className="acct-spaces" aria-labelledby="acct-spaces-title">
            <div className="acct-head">
              <h2 id="acct-spaces-title">내 공간</h2>
              {spaces.length > 0 && <button type="button" className="acct-more-link" onClick={onSpaces}>전체 보기<Arrow /></button>}
            </div>
            {spaces.length ? (
              <ul className="acct-rooms">
                {spaces.slice(0, 3).map((s) => {
                  const count = s.regions.filter((r) => r.grade !== "D").length;
                  return (
                    <li key={s.id}>
                      <button type="button" className="acct-room" onClick={() => onOpen(s)} aria-label={`${s.name || "이름 없는 공간"} 열기`}>
                        <span className="acct-room-name">
                          <strong>{s.name || "이름 없는 공간"}</strong>
                          <small>{[s.building, s.floor].filter(Boolean).join(" · ")}{" · "}보호 영역 {count}곳</small>
                        </span>
                        <span className={`acct-pill ${count ? "done" : "todo"}`}>{count ? "보호 중" : "설정 전"}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
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

          <section className="acct-help" aria-labelledby="acct-help-title">
            <div className="acct-head">
              <h2 id="acct-help-title">사용 도움말</h2>
              <button type="button" className="acct-more-link" onClick={() => onGuide()}>사용 가이드 열기<Arrow /></button>
            </div>
            <ul className="acct-help-list">
              {help.map((item) => (
                <li key={item.title}>
                  <button type="button" onClick={() => onGuide(item.index)}>
                    <span className="acct-help-top">
                      <strong>{item.title}</strong>
                      {item.grades && (
                        <span className="acct-help-grades" aria-hidden="true">
                          {gradeOrder.map((g) => <span key={g} className={`furniture-grade grade-${g}`}>{g}</span>)}
                        </span>
                      )}
                    </span>
                    <small>{item.text}</small>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
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
      <p className="acct-version">{BUILD_LABEL}</p>
    </main>
  );
}
