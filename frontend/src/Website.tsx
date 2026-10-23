import { guideSteps } from "./guideContent";
import type { Account } from "./model";
import { grades } from "./model";
import "./Website.css";

export type WebsiteDestination = "home" | "spaces" | "guide";
type HeaderProps = {
  account: Account | null;
  active: string;
  onNavigate: (page: WebsiteDestination) => void;
  onAccount: () => void;
  onLogin: () => void;
  onStart: () => void;
};
export function WebsiteHeader({
  account,
  active,
  onNavigate,
  onAccount,
  onLogin,
  onStart,
}: HeaderProps) {
  const items: { page: WebsiteDestination; label: string }[] = [
    { page: "home", label: "소개" },
    { page: "spaces", label: "내 공간" },
    { page: "guide", label: "사용 가이드" },
  ];
  return (
    <div className="website-header desktop-only">
      <header className="website-topbar">
        <button
          className="website-brand"
          onClick={() => onNavigate("home")}
          aria-label="SunSync 소개 홈"
        >
          <img src="/design/sun.svg" alt="" width="30" height="30" />
          <span translate="no">SunSync</span>
        </button>
        <div className="website-account">
          {account ? (
            <button onClick={onAccount}>
              <span className="person-icon" aria-hidden="true" />내 계정
              
            </button>
          ) : (
            <>
              <button onClick={onLogin}>로그인</button>
              <span className="account-separator" aria-hidden="true" />
              <button onClick={onLogin}>회원가입</button>
            </>
          )}
        </div>
      </header>
      <div className="website-nav-border">
        <nav className="website-nav" aria-label="웹사이트 메뉴">
          <div className="website-nav-links">
            {items.map((item) => (
              <button
                key={item.page}
                onClick={() => onNavigate(item.page)}
                aria-current={
                  active === item.page ||
                  (item.page === "spaces" && ["detail", "register", "capture", "processing", "review", "protect"].includes(active))
                    ? "page"
                    : undefined
                }
              >
                {item.label}
              </button>
            ))}
          </div>
          <button className="website-start" onClick={onStart}>
            공간 등록 <span aria-hidden="true">＋</span>
          </button>
        </nav>
      </div>
    </div>
  );
}

export function GuideArt({ step }: { step: number }) {
  return (
    <svg
      className={`website-guide-art art-${step}`}
      viewBox="0 0 280 152"
      fill="none"
      aria-hidden="true"
    >
      {step === 0 ? (
        <>
          <rect
            x="64"
            y="20"
            width="152"
            height="112"
            rx="6"
            fill="#fff"
            stroke="#d3d5de"
          />
          <path d="M64 52h152M140 52v80" stroke="#d3d5de" />
          <rect x="77" y="66" width="48" height="47" rx="3" fill="#e5e0fa" />
          <rect x="155" y="66" width="45" height="47" rx="3" fill="#edf0f2" />
          <circle cx="197" cy="35" r="4" fill="#7770c8" />
        </>
      ) : step === 1 || step === 2 ? (
        <>
          <rect x="38" y="25" width="148" height="103" rx="3" fill="#e7e9ed" />
          <path
            d="M87 25v103m50-103v103M38 76h148"
            stroke="#fff"
            strokeWidth="3"
          />
          <rect
            x="198"
            y="40"
            width="48"
            height="83"
            rx="8"
            fill="#fff"
            stroke="#aaa4d1"
            strokeWidth="2"
          />
          <rect x="207" y="61" width="29" height="30" rx="2" fill="#edeaf9" />
          <path
            d="M212 67h7v7h-7zm14 0h5v5h-5zm-14 14h5v5h-5zm12-5h7v10h-7z"
            stroke="#6d64ad"
            strokeWidth="2"
          />
          <path d="M203 49h38m-23 65h10" stroke="#c3bde4" strokeWidth="2" />
        </>
      ) : step === 3 ? (
        <>
          <rect
            x="42"
            y="21"
            width="196"
            height="111"
            rx="5"
            fill="#fff"
            stroke="#d5d8e0"
          />
          <rect
            x="58"
            y="38"
            width="46"
            height="24"
            rx="3"
            fill="#e8e5f9"
            stroke="#9287d1"
          />
          <rect x="117" y="38" width="46" height="24" rx="3" fill="#e7e9ee" />
          <rect x="176" y="38" width="46" height="24" rx="3" fill="#e7e9ee" />
          <path d="M126 84l73 13-10 34h-81z" fill="#fff1cc" />
          <path d="M95 132h91" stroke="#8d80c9" strokeWidth="3" />
        </>
      ) : step === 4 ? (
        <>
          <rect
            x="47"
            y="28"
            width="184"
            height="104"
            rx="5"
            fill="#fff"
            stroke="#d5d8e0"
          />
          <rect
            x="64"
            y="44"
            width="59"
            height="29"
            rx="3"
            fill="#e6e1fa"
            stroke="#887ac8"
            strokeWidth="2"
          />
          <path d="M66 73h55" stroke="#7565b4" strokeWidth="4" />
          <rect
            x="141"
            y="87"
            width="69"
            height="29"
            rx="2"
            fill="#ede9fa"
            stroke="#9d90cd"
            strokeDasharray="4 3"
          />
          <circle cx="225" cy="33" r="20" fill="#fff" stroke="#d6d0e9" />
          <path d="M216 33l6 6 11-13" stroke="#7f70b5" strokeWidth="2" />
        </>
      ) : (
        <>
          <rect
            x="44"
            y="25"
            width="192"
            height="104"
            rx="7"
            fill="#fff"
            stroke="#d5d8e0"
          />
          <path
            d="M65 105l29-32 31 12 32-43 37 20 23-20"
            stroke="#9d8dcc"
            strokeWidth="3"
          />
          <path d="M65 48v60h153" stroke="#e1e2e8" />
          <circle cx="157" cy="42" r="6" fill="#d5cbea" />
        </>
      )}
    </svg>
  );
}

export function WebsiteHome({
  onGuide,
  onStart,
}: {
  onGuide: (index: number | null) => void;
  onStart: () => void;
}) {
  const featuredSteps = [0, 2, 3, 4];
  return (
    <main className="website-home desktop-only" aria-label="SunSync 소개">
      <section className="website-hero" aria-labelledby="website-hero-title">
        <img
          className="website-hero-photo"
          src="/design/library-protection-hero.webp"
          alt="창의 일부만 어둡게 조절해 서가를 그늘로 보호하고 주변 공간에는 햇빛을 유지한 도서관 연출 이미지"
          fetchPriority="high"
          width="1672"
          height="941"
        />
        <div className="website-hero-shade" />
        <div className="website-hero-content">
          <h1 id="website-hero-title">
            햇빛은 필요한 곳에.
            <br />
            보호는 필요한 만큼.
          </h1>
          <p className="website-hero-description">
            선택한 위치 보호
            <br />
            주변의 자연광은 그대로
          </p>
          <div className="website-hero-actions">
            <button className="website-button filled" onClick={onStart}>
              내 공간 시작하기
            </button>
          </div>
        </div>
        <p className="website-image-caption">
          서비스 이해를 돕기 위한 연출 이미지
        </p>
      </section>
      <section
        id="website-guide"
        className="website-guide-section"
        aria-labelledby="website-guide-title"
      >
        <div className="website-section-heading">
          <div>
            <h2 id="website-guide-title">SunSync, 이렇게 사용해요</h2>
          </div>
          <button className="website-inline-link" onClick={() => onGuide(null)}>
            전체 가이드
          </button>
        </div>
        <div className="website-guide-grid">
          {featuredSteps.map((index, i) => (
            <button
              className="website-guide-tile"
              key={index}
              onClick={() => onGuide(index)}
            >
              <div className="website-guide-visual">
                <GuideArt step={index} />
                <span className="website-step-number">0{i + 1}</span>
              </div>
              <div className="website-guide-tile-copy">
                <h3>{guideSteps[index].title}</h3>
                <p>
                  {
                    [
                      "건물과 측정할 공간 등록",
                      "가구 네 모서리에서 QR 마커 촬영",
                      "가구의 가로·세로·높이 확인",
                      "보호할 면·높이·등급 설정",
                    ][i]
                  }
                </p>
                <span className="website-inline-link">
                  방법 알아보기
                </span>
              </div>
            </button>
          ))}
        </div>
      </section>
    </main>
  );
}

export function WebsiteGuide({
  index,
  onIndex,
}: {
  index: number | null;
  onIndex: (index: number | null) => void;
}) {
  return (
    <main className="website-guide-page desktop-only">
      <div className="website-section-heading">
        <div>
          <h1>사용 가이드</h1>
        </div>
        {index !== null && (
          <button className="website-inline-link" onClick={() => onIndex(null)}>
            전체 단계 보기
          </button>
        )}
      </div>
      {index === null ? (
        <div className="website-all-guides">
          {guideSteps.map((step, i) => (
            <button
              className="website-guide-tile"
              key={step.title}
              onClick={() => onIndex(i)}
            >
              <div className="website-guide-visual">
                <GuideArt step={i} />
                <span className="website-step-number">0{i + 1}</span>
              </div>
              <div className="website-guide-tile-copy">
                <h2>{step.title}</h2>
                <p>{step.text}</p>
                <span className="website-inline-link">방법 알아보기</span>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <article className="website-guide-article">
          <aside className="website-guide-steps" aria-label="가이드 단계">
            {guideSteps.map((step, i) => (
              <button
                key={step.title}
                aria-current={index === i ? "step" : undefined}
                onClick={() => onIndex(i)}
              >
                <span>0{i + 1}</span>
                {step.title}
              </button>
            ))}
          </aside>
          <section className="website-guide-body">
            <h2>{guideSteps[index].title}</h2>
            <div className="website-guide-detail-art">
              <GuideArt step={index} />
            </div>
            <p className="website-guide-lead">{guideSteps[index].text}</p>
            <p className="website-guide-detail-text">
              {guideSteps[index].detail}
            </p>
            {index === 4 && (
              <dl className="website-guide-grades">
                {Object.entries(grades).map(([grade, description]) => (
                  <div key={grade}>
                    <dt>{grade}</dt>
                    <dd>{description}</dd>
                  </div>
                ))}
              </dl>
            )}
            <div className="website-guide-pagination">
              <span className="website-guide-count">
                {guideSteps.length}단계 중 {index + 1}단계
              </span>
              <button
                className="website-button outlined"
                disabled={index === 0}
                onClick={() => onIndex(index - 1)}
              >
                ← 이전 단계
              </button>
              <button
                className="website-button filled"
                disabled={index === guideSteps.length - 1}
                onClick={() => onIndex(index + 1)}
              >
                다음 단계 →
              </button>
            </div>
          </section>
        </article>
      )}
    </main>
  );
}

export function WebsiteFooter() {
  return (
    <footer className="website-footer desktop-only">
      <div>
        <strong>SunSync</strong>
        <span>필요한 곳만 보호하는 햇빛 제어</span>
      </div>
    </footer>
  );
}
