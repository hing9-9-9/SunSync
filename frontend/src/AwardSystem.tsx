import { SpaceMenu } from "./SpaceMenu";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Account, Space } from "./model";
import { resolveWindow, isWidthWall } from "./model";
import type { Page } from "./navigation";
import type { HandoffSession } from "./spaceApi";
import { GuideIllustration } from "./MoodIcons";
import { guideSteps } from "./guideContent";
import AwardPreview, { Arrow, Scribble, SunMark } from "./AwardPreview";
import { SyncBadge, SyncRetry } from "./ShellParts";
import "./AwardSystem.css";

type Destination = "home" | "spaces" | "guide" | "account";
export function AwardHeader({
  account,
  page,
  editing,
  onNavigate,
  onLogin,
}: {
  account: Account | null;
  page: Page;
  editing: boolean;
  onNavigate: (p: Destination) => void;
  onLogin: () => void;
}) {
  return (
    <header className="ap-header award-header">
      <button
        className="ap-logo"
        onClick={() => onNavigate("home")}
        aria-label="SunSync 홈"
      >
        <SunMark />
        <span translate="no">SunSync</span>
        <sup>®</sup>
      </button>
      <nav aria-label="주요 메뉴">
        <button
          className={page === "home" ? "is-current" : ""}
          onClick={() => onNavigate("home")}
        >
          소개
        </button>
        <button
          className={
            [
              "spaces",
              "detail",
              "register",
              "capture",
              "processing",
              "review",
              "protect",
            ].includes(page)
              ? "is-current"
              : ""
          }
          onClick={() => onNavigate("spaces")}
        >
          내 공간
        </button>
        <button
          className={page === "guide" ? "is-current" : ""}
          onClick={() => onNavigate("guide")}
        >
          사용 가이드
        </button>
      </nav>
      <div className="award-header-actions">
        {account ? (
          <button
            className={`award-account ${["account", "more"].includes(page) ? "is-current" : ""}`}
            onClick={() => onNavigate("account")}
            aria-label={`${account.name}, 내 계정`}
          >
            <span>{account.name.slice(0, 1)}</span>
            <span className="award-account-label">내 계정</span>
          </button>
        ) : (
          <button className="award-login" onClick={onLogin}>
            로그인·회원가입
          </button>
        )}
        {editing && (
          <button
            className="ap-small-cta"
            onClick={() => onNavigate("spaces")}
          >
            내 공간으로
            <Arrow diagonal />
          </button>
        )}
      </div>
    </header>
  );
}
export function AwardLanding({
  onStart,
  onGuide,
}: {
  onStart: () => void;
  onGuide: () => void;
}) {
  return <AwardPreview embedded onStart={onStart} onGuide={onGuide} />;
}

function SpaceThumbnail({ space }: { space: Space }) {
  const window = resolveWindow(space);
  const horizontal = window ? isWidthWall(window.wall) : false;
  return (
    <svg
      className="award-space-thumbnail"
      viewBox={`-.5 -.5 ${space.width + 1} ${space.depth + 1}`}
      aria-label={`${space.name} 실제 저장 도면`}
      role="img"
    >
      <rect
        x="0"
        y="0"
        width={space.width}
        height={space.depth}
        rx=".07"
        fill="var(--c-white)"
        stroke="var(--c-protect-ink)"
        strokeWidth=".04"
      />
      {space.furniture
        .filter((f) => f.zone === space.zone)
        .map((f) => (
          <rect
            key={f.id}
            x={f.x}
            y={f.y}
            width={f.width}
            height={f.depth}
            rx=".03"
            fill="var(--c-block)"
            stroke="var(--c-9d9d9d)"
            strokeWidth=".025"
          />
        ))}
      {space.regions
        .filter((r) => r.zone === space.zone && r.grade !== "D")
        .map((r) => (
          <rect
            key={r.id}
            x={r.x}
            y={r.y}
            width={r.width}
            height={r.depth}
            rx=".03"
            fill="var(--c-action)"
            fillOpacity=".3"
          />
        ))}
      {window && (
        <line
          x1={horizontal ? window.offset : window.wall === "east" ? space.width : 0}
          x2={horizontal ? window.offset + window.width : window.wall === "east" ? space.width : 0}
          y1={horizontal ? window.wall === "south" ? space.depth : 0 : window.offset}
          y2={horizontal ? window.wall === "south" ? space.depth : 0 : window.offset + window.width}
          stroke="var(--c-protect-ink)"
          strokeWidth=".1"
        />
      )}
    </svg>
  );
}
export function AwardSpaces({
  status,
  spaces,
  jobs,
  onSelect,
  onStart,
  onMeasurement,
  syncFailed,
  onRetrySync,
  onEdit,
  onDelete,
}: {
  status?: ReactNode;
  spaces: Space[];
  jobs: HandoffSession[];
  syncFailed: string[];
  onRetrySync: (id: string) => void;
  onSelect: (s: Space) => void;
  onStart: () => void;
  onMeasurement: (job: HandoffSession) => void;
  onEdit?: (s: Space, at?: "register") => void;
  onDelete?: (s: Space) => void;
}) {
  const [query, setQuery] = useState(""),
    [building, setBuilding] = useState("all");
  const buildings = [...new Set(spaces.map((s) => s.building))];
  const filtered = spaces.filter(
    (s) =>
      (building === "all" || s.building === building) &&
      `${s.name} ${s.building} ${s.floor}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  return (
    <div className="award-spaces">
      <section className="award-space-intro">
        <div>
          <h1>내 공간</h1>
        </div>
      </section>
      {status}
      <div className="award-list-tools">
        <label className="award-search">
          <span className="award-control-label">공간 검색</span>
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <circle
              cx="10"
              cy="10"
              r="6"
              stroke="currentColor"
              strokeWidth="1.5"
            />
            <path d="m15 15 5 5" stroke="currentColor" strokeWidth="1.5" />
          </svg>
          <input
            type="search"
            placeholder="예: 열람실"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label className="award-building-filter">
          <span className="award-control-label">건물 선택</span>
          <select
            value={building}
            onChange={(e) => setBuilding(e.target.value)}
          >
            <option value="all">전체 건물</option>
            {buildings.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <button className="primary" onClick={onStart}>
          ＋ 공간 등록
        </button>
      </div>
      {filtered.length ? (
        <div className="award-room-grid">
          {filtered.map((s, i) => {
            const regionCount = s.regions.filter((r) => r.grade !== "D").length,
              job = jobs.find((j) => j.space_id === s.id);
            return (
              <article className="award-room-card" key={s.id}>
                {onEdit && onDelete && (
                  <SpaceMenu label={`${s.name} 더보기`} items={[
                    { label: "배치 편집", icon: "layout", onSelect: () => onEdit(s) },
                    { label: "공간 정보 수정", icon: "info", onSelect: () => onEdit(s, "register") },
                    { label: "공간 삭제", icon: "trash", danger: true, onSelect: () => onDelete(s) },
                  ]} />
                )}
                <button
                  className="award-room-open"
                  onClick={() => onSelect(s)}
                  aria-label={`${s.building}, ${s.name} 공간 현황 열기`}
                >
                  <div className={`award-room-visual award-art-${i % 3}`}>
                    <span
                      className={`award-state ${regionCount ? "configured" : ""}`}
                    >
                      {regionCount ? "보호 설정 완료" : "보호 설정 전"}
                    </span>
                    <SpaceThumbnail space={s} />
                  </div>
                  <div className="award-room-copy">
                    <span>
                      {s.building} {s.floor}
                    </span>
                    <h2>{s.name}</h2>
                    <SyncBadge space={s} />
                    <div>
                      <span>
                        구역 {s.zones.length}개, 보호 영역 {regionCount}개
                      </span>
                    </div>
                  </div>
                </button>
                <SyncRetry space={s} failed={syncFailed} onRetry={onRetrySync} />
                {job && (
                  <div className="award-measurement-row">
                    <span>
                      {job.status === "completed"
                        ? "측정 완료"
                        : job.status === "connected"
                          ? "측정 진행 중"
                          : "측정 대기"}
                    </span>
                    <button onClick={() => onMeasurement(job)}>
                      {job.status === "completed"
                        ? "도면 확인"
                        : "측정 상태 보기"}
                    </button>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      ) : (
        <section className="award-empty">
          <SunMark />
          <h3>
            {spaces.length
              ? "검색 결과가 없습니다."
              : "등록한 공간이 없습니다."}
          </h3>
          <p>
            {spaces.length
              ? "다른 이름으로 검색하거나 전체 건물을 선택해 보세요."
              : "공간과 창문의 크기를 입력하면 가구와 보호 영역을 설정할 수 있습니다."}
          </p>
          <button
            className="primary"
            onClick={() => {
              if (spaces.length) {
                setQuery("");
                setBuilding("all");
              } else onStart();
            }}
          >
            {spaces.length ? "전체 공간 보기" : "공간 등록하기"}
          </button>
        </section>
      )}
    </div>
  );
}
// Steps that have a real screen to show get the app's own fragment; the rest are text only.
export function AwardGuide({
  index,
  onIndex,
  onStart,
}: {
  index: number | null;
  onIndex: (i: number) => void;
  onStart: () => void;
}) {
  const [activeSection, setActiveSection] = useState(index ?? 0);
  const activeRef = useRef(index ?? 0);
  const openStep = (step: number) => {
    activeRef.current = step;
    setActiveSection(step);
    if (matchMedia("(max-width: 700px)").matches) {
      const nav = document.querySelector<HTMLElement>(".guide-refresh .ap-guide-nav");
      const button = nav?.querySelectorAll<HTMLButtonElement>("button")[step];
      if (nav && button) nav.scrollTo({
        left: Math.max(0, button.offsetLeft - (nav.clientWidth - button.offsetWidth) / 2),
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
    }
  };
  const scrollToStep = (step: number) => {
    document.getElementById(`award-guide-${step}`)?.scrollIntoView({
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  };
  useEffect(() => {
    if (index === null) return;
    let scrollFrame = 0;
    const frame = requestAnimationFrame(() => {
      openStep(index);
      scrollFrame = requestAnimationFrame(() => scrollToStep(index));
    });
    return () => { cancelAnimationFrame(frame); cancelAnimationFrame(scrollFrame); };
  }, [index]);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      const sections = guideSteps.map((_, i) => document.getElementById(`award-guide-${i}`));
      const header = document.querySelector(".award-header")?.getBoundingClientRect().height ?? 64;
      const headerBottom = document.querySelector(".award-header")?.getBoundingClientRect().bottom ?? 0;
      document.getElementById("award-guide-main")?.style.setProperty("--guide-header-offset", `${Math.max(8, headerBottom + 8)}px`);
      const readingLine = Math.max(header + 32, window.innerHeight * .4);
      let current = 0;
      sections.forEach((section, i) => {
        if (section && section.getBoundingClientRect().top <= readingLine) current = i;
      });
      if (activeRef.current !== current) openStep(current);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    update();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, []);
  return (
    <main id="award-guide-main" tabIndex={-1} className="ap-guide-page guide-refresh">
      <div className="ap-guide-heading">
        <h1>사용 가이드</h1>
      </div>
      <div className="ap-guide-layout">
        <nav className="ap-guide-nav" aria-label="사용 가이드 단계">
          {guideSteps.map((step, i) => (
            <button
              key={step.title}
              className={activeSection === i ? "active" : ""}
              aria-current={activeSection === i ? "step" : undefined}
              onClick={() => { openStep(i); if (index === i) requestAnimationFrame(() => scrollToStep(i)); else onIndex(i); }}
            >
              <span>0{i + 1}</span>
              {step.title}
              <Arrow diagonal />
            </button>
          ))}
        </nav>
        <div className="ap-guide-sections">
          {guideSteps.map((step, i) => (
            <section
              id={`award-guide-${i}`}
              key={step.title}
              className={`ap-guide-step is-open ${activeSection === i ? "is-current" : ""}`}
            >
              <h2 className="ap-guide-step-title">
                <span className="ap-guide-step-head">
                  <span>0{i + 1}</span><span className="guide-step-name">{step.title}</span>
                </span>
              </h2>
              <div
                className="ap-guide-step-body"
                id={`award-guide-body-${i}`}
              >
                <div className="ap-guide-art">
                  <GuideIllustration step={i} />
                </div>
                <div className="guide-step-copy">
                  <p>{step.text}</p>
                  <ul className="guide-step-notes">
                    {step.detail.split(/(?<=\.)\s+/u).map(note => <li key={note}>{note}</li>)}
                  </ul>
                </div>
              </div>
            </section>
          ))}
        </div>
      </div>
      <div className="ap-guide-bottom">
        <p>
          <span>공간을 등록하고 보호 영역을 설정하세요.</span>
        </p>
        <button className="ap-round-link" onClick={onStart}>
          <Scribble />
          <span>내 공간 열기</span>
          <b>
            <Arrow diagonal />
          </b>
        </button>
      </div>
    </main>
  );
}
export function AwardFooter({ onHome }: { onHome: () => void }) {
  return (
    <footer className="ap-footer">
      <button className="ap-logo" onClick={onHome}>
        <SunMark />
        <span translate="no">SunSync</span>
      </button>
      <span>© 2026 SunSync</span>

    </footer>
  );
}
