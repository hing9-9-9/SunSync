import { useEffect, useState } from "react";
import type { Account, Space } from "./model";
import { resolveWindow, isWidthWall } from "./model";
import type { Page } from "./navigation";
import type { HandoffSession } from "./spaceApi";
import { GuideIllustration } from "./MoodIcons";
import { guideSteps } from "./guideContent";
import AwardPreview, { Arrow, Scribble, SunMark } from "./AwardPreview";
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
          사용 방법
        </button>
      </nav>
      <div className="award-header-actions">
        {account ? (
          <button
            className={`award-account ${["account", "more"].includes(page) ? "is-current" : ""}`}
            onClick={() => onNavigate("account")}
            aria-label={`${account.name} · 내 계정`}
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
        fill="#fff"
        stroke="#343434"
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
            fill="#e9e9e9"
            stroke="#9d9d9d"
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
            fill="#6660e5"
            fillOpacity=".3"
          />
        ))}
      {window && (
        <line
          x1={horizontal ? window.offset : window.wall === "east" ? space.width : 0}
          x2={horizontal ? window.offset + window.width : window.wall === "east" ? space.width : 0}
          y1={horizontal ? window.wall === "south" ? space.depth : 0 : window.offset}
          y2={horizontal ? window.wall === "south" ? space.depth : 0 : window.offset + window.width}
          stroke="#343434"
          strokeWidth=".1"
        />
      )}
    </svg>
  );
}
export function AwardSpaces({
  spaces,
  jobs,
  onSelect,
  onStart,
  onMeasurement,
}: {
  spaces: Space[];
  jobs: HandoffSession[];
  onSelect: (s: Space) => void;
  onStart: () => void;
  onMeasurement: (job: HandoffSession) => void;
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
  const regions = spaces.reduce(
    (n, s) => n + s.regions.filter((r) => r.grade !== "D").length,
    0,
  );
  return (
    <div className="award-spaces">
      <section className="award-space-intro">
        <div>
          <h1>
            각자의 공간,
            <br />
            <span>각자의 빛.</span>
          </h1>
        </div>
        <div className="award-space-stats">
          <div>
            <strong>{String(spaces.length).padStart(2, "0")}</strong>
            <span>등록한 공간</span>
          </div>
          <div>
            <strong>{String(regions).padStart(2, "0")}</strong>
            <span>설정된 보호영역</span>
          </div>
        </div>
      </section>
      <div className="award-list-tools">
        <label className="award-search">
          <span className="award-sr">공간 검색</span>
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
            placeholder="공간·건물 검색"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label className="award-building-filter">
          <span className="award-sr">건물 선택</span>
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
                <button
                  className="award-room-open"
                  onClick={() => onSelect(s)}
                  aria-label={`${s.building} · ${s.name} 공간 현황 열기`}
                >
                  <div className={`award-room-visual award-art-${i % 3}`}>
                    <span
                      className={`award-state ${regionCount ? "configured" : ""}`}
                    >
                      <i />
                      {regionCount ? "보호 설정 완료" : "보호 설정 전"}
                    </span>
                    <SpaceThumbnail space={s} />
                    <span className="award-room-number">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                  </div>
                  <div className="award-room-copy">
                    <span>
                      {s.building} · {s.floor}
                    </span>
                    <h3>{s.name}</h3>
                    <div>
                      <span>
                        {s.zones.length}개 구역 <b>·</b> 보호영역 {regionCount}
                        개
                      </span>
                      <Arrow diagonal />
                    </div>
                  </div>
                </button>
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
                        : "측정 상태 보기"}{" "}
                      →
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
              : "첫 공간을 등록해 보세요."}
          </h3>
          <p>
            {spaces.length
              ? "다른 이름으로 검색하거나 전체 건물을 선택해 보세요."
              : "공간과 창문의 크기를 입력하면 가구와 보호구역을 설정할 수 있습니다."}
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
export function AwardGuide({
  index,
  onIndex,
  onStart,
}: {
  index: number | null;
  onIndex: (i: number) => void;
  onStart: () => void;
}) {
  useEffect(() => {
    if (index !== null)
      document
        .getElementById(`award-guide-${index}`)
        ?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  }, [index]);
  return (
    <main id="award-guide-main" tabIndex={-1} className="ap-guide-page">
      <div className="ap-guide-heading">
        <h1>
          당신의 공간에
          <br />
          <span>빛을 맞추는 방법.</span>
        </h1>
        <p>공간 등록부터 보호 설정까지, 순서대로 확인해 보세요.</p>
      </div>
      <div className="ap-guide-layout">
        <nav className="ap-guide-nav" aria-label="사용 방법 단계">
          {guideSteps.map((step, i) => (
            <button
              key={step.title}
              className={(index ?? 0) === i ? "active" : ""}
              onClick={() => onIndex(i)}
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
              className={`ap-guide-step ${(index ?? 0) === i ? "is-open" : ""}`}
            >
              <h2 className="ap-guide-step-title">
                <button
                  type="button"
                  aria-expanded={(index ?? 0) === i}
                  aria-controls={`award-guide-body-${i}`}
                  onClick={() => onIndex(i)}
                >
                  <span>0{i + 1}</span>
                  {step.title}
                  <i aria-hidden="true" />
                </button>
              </h2>
              <div
                className="ap-guide-step-body"
                id={`award-guide-body-${i}`}
                hidden={(index ?? 0) !== i}
              >
                <div className="ap-guide-art">
                  <GuideIllustration step={i} />
                </div>
                <p>{step.text}</p>
                <p className="ap-guide-detail">{step.detail}</p>
              </div>
            </section>
          ))}
        </div>
      </div>
      <div className="ap-guide-bottom">
        <p>
          준비되셨나요?
          <br />
          <strong>이제 내 공간에서 시작해 보세요.</strong>
        </p>
        <button className="ap-round-link" onClick={onStart}>
          <Scribble />
          <span>내 공간 시작하기</span>
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
      <span>A little shade. A better space.</span>
      <span>© 2026 SunSync</span>

    </footer>
  );
}
