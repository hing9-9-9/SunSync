import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import "./AwardPreview.css";
import { GuideIllustration } from "./MoodIcons";

const objects = [
  { name: "책장", type: "SHELVING", width: 2, depth: 0.6, height: 1.8 },
  { name: "책상", type: "DESK", width: 1.6, depth: 0.8, height: 0.75 },
  { name: "라운지 체어", type: "LOUNGE", width: 0.8, depth: 0.8, height: 0.9 },
];
export function Arrow({ diagonal = false }: { diagonal?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d={diagonal ? "M5 19 19 5M5 5h14v14" : "M4 12h16m-6-6 6 6-6 6"}
        stroke="currentColor"
        strokeWidth="1.5"
      />
    </svg>
  );
}
// Hand-drawn ellipse that draws itself around a primary link on hover.
export function Scribble() {
  return (
    <svg className="ap-scribble" viewBox="0 0 220 72" fill="none" aria-hidden="true" preserveAspectRatio="none">
      <path d="M150 8C96 2 22 10 8 34c-12 22 40 34 104 32 62-2 104-14 100-34C208 12 160 4 110 6" pathLength="1" />
    </svg>
  );
}
export function SunMark() {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <circle cx="16" cy="16" r="6" stroke="currentColor" strokeWidth="1.5" />
      {Array.from({ length: 8 }, (_, i) => (
        <path
          key={i}
          d="M16 1v5"
          transform={`rotate(${i * 45} 16 16)`}
          stroke="currentColor"
          strokeWidth="1.5"
        />
      ))}
    </svg>
  );
}
// A shared 32px drawing grid keeps the three functional marks optically consistent.
function HomeStepIcon({ kind }: { kind: "scan" | "select" | "time" }) {
  return <svg className={`home-action-icon icon-${kind}`} viewBox="0 0 32 32" fill="none" aria-hidden="true"
    stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    {kind === "scan" && <>
      <path d="M10 6H6v4m16-4h4v4M6 22v4h4m16-4v4h-4" />
      <rect x="11" y="11" width="4" height="4" rx=".6" />
      <rect x="18" y="11" width="3" height="4" rx=".6" />
      <path d="M11 18v3h4m3-3h3v3" />
      <path className="home-scan-line" d="M8 16h16" />
    </>}
    {kind === "select" && <>
      <path d="M16 4 26 8v8c0 6-5 9-10 12C11 25 6 22 6 16V8Z" fill="currentColor" fillOpacity=".07" />
      <path className="home-shield-check" d="m11 16 3.5 3.5L21 13" />
    </>}
    {kind === "time" && <>
      <circle cx="16" cy="16" r="11" />
      <path d="M16 8v1m8 7h-1m-7 8v-1m-8-7h1" opacity=".45" />
      <path className="home-clock-hand" d="M16 10v6l4 2" />
    </>}
  </svg>;
}
// Autoplay is a staged effect, not a sun path: one progress value runs 0 -> 1 over 6s on a single
// ease-in-out curve, moving the sun from the left end to the right end and stopping there.
const PLAY_MS = 6000;
const sceneAt = (ms: number) => {
  const u = Math.min(1, ms / PLAY_MS);
  const eased = (1 - Math.cos(Math.PI * u)) / 2;
  return { hour: 5 + 15 * eased, progress: u };
};
// From the moment the beam reaches the shelf (about 16:30) the film stays on, so the last frame shows it.
const beamOnShelf = (hour: number) => hour >= 16.5;
function Room({
  hour,
  protectedOn,
  selected,
  onSelect,
  dimensions,
}: {
  hour: number;
  protectedOn: boolean;
  selected: number;
  onSelect: (i: number) => void;
  dimensions?: { width: number; depth: number; height: number }[];
}) {
  const shift = (hour - 12) * 13;
  const transform = (i: number, x: number, y: number) => {
    const d = dimensions?.[i];
    const sx = d
      ? Math.min(1.15, Math.max(0.7, d.width / objects[i].width))
      : 1;
    const sy = d
      ? Math.min(1.15, Math.max(0.7, d.height / objects[i].height))
      : 1;
    const [dx, dy] = [
      [5, -94],
      [-10, -10],
      [-55, -75],
    ][i];
    return `translate(${dx} ${dy}) translate(${x} ${y}) scale(${sx} ${sy}) translate(${-x} ${-y})`;
  };
  return (
    <svg
      className="ap-room"
      viewBox="0 0 800 590"
      role="group"
      aria-label="가구를 선택할 수 있는 공간 도면"
    >
      <defs>
        <linearGradient id="ap-floor" x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="var(--c-ecebe6)" />
          <stop offset="1" stopColor="var(--c-faf9f6)" />
        </linearGradient>
        <linearGradient id="ap-wall">
          <stop stopColor="var(--c-e3e1dc)" />
          <stop offset="1" stopColor="var(--c-f3f1ed)" />
        </linearGradient>
        <linearGradient id="ap-ray" x1="0" y1="0" x2="0" y2="1">
          <stop stopColor="var(--c-sun)" stopOpacity=".34" />
          <stop offset="1" stopColor="var(--c-sun)" stopOpacity=".05" />
        </linearGradient>
        <filter id="ap-shadow">
          <feGaussianBlur stdDeviation="12" />
        </filter>
      </defs>
      <ellipse
        cx="408"
        cy="536"
        rx="257"
        ry="43"
        fill="var(--c-343040)"
        opacity=".09"
        filter="url(#ap-shadow)"
      />
      <path
        d="m104 354 303-168 294 168-303 174Z"
        fill="var(--c-d4d0c9)"
        transform="translate(0 24)"
      />
      <path
        d="M104 343 407 170l294 173-303 173Z"
        fill="url(#ap-floor)"
        stroke="var(--c-d5d1ca)"
        transform="translate(0 24)"
      />
      <path
        d="M104 343V150L407 0v170Z"
        fill="url(#ap-wall)"
        transform="translate(0 24)"
      />
      <path d="M407 24 701 193v174L407 194Z" fill="var(--c-eeece7)" />
      <g
        transform="translate(0 24)"
        stroke="var(--c-d8d5ce)"
        strokeWidth=".7"
        opacity=".7"
      >
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <path key={i} d={`M${150 + i * 46} ${369 + i * 26}l303-173`} />
        ))}
      </g>
      <path
        d="m168 180 182-91v135l-182 99Z"
        fill="var(--c-fafbff)"
        stroke="var(--c-c5c1b8)"
        strokeWidth="5"
      />
      <g transform="matrix(1 -.5 0 1 171 183)">
        {Array.from({ length: 100 }, (_, i) => (
          <rect
            key={i}
            x={(i % 10) * 17.5}
            y={Math.floor(i / 10) * 12.6}
            width="16"
            height="11.4"
            fill={
              protectedOn && (
                selected === 0 ? i % 10 >= 4 && i % 10 <= 7 && Math.floor(i / 10) >= 3 :
                selected === 1 ? i % 10 >= 2 && i % 10 <= 5 && Math.floor(i / 10) >= 5 :
                i % 10 <= 2 && Math.floor(i / 10) >= 6
              )
                ? "var(--c-8075b7)"
                : "var(--c-e0e5eb)"
            }
            opacity={protectedOn ? ".9" : ".48"}
            style={{ transition: `fill 800ms ${i * 3}ms, opacity 800ms ${i * 3}ms` }}
          />
        ))}
      </g>
      <path
        d={`M176 325 351 229 ${572 + shift} 358 ${389 + shift} 465Z`}
        fill="url(#ap-ray)"
        className="ap-light"
      />
      <g
        transform={transform(0, 550, 370)}
        className="ap-furniture"
        role="button"
        tabIndex={0}
        aria-label="책장 선택"
        aria-pressed={selected === 0}
        onClick={() => onSelect(0)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(0);
          }
        }}
      >
        <path d="m452 362 40-23 150 87-40 23Z" fill="var(--c-343040)" opacity=".08" />
        <path d="m458 226 34-19 144 83-34 19Z" fill="var(--c-c3b8a5)" />
        <path
          d="M458 226v134l144 84V309Z"
          fill={selected === 0 ? "var(--c-b5acd1)" : "var(--c-bdb29f)"}
          stroke={selected === 0 ? "var(--c-b9b5ee)" : "var(--c-a79c8a)"}
        />
        <path d="m602 309 34-19v134l-34 20Z" fill="var(--c-8e8679)" />
        {[0, 1, 2].map((n) => (
          <g key={n}>
            <path
              d={`m470 ${245 + n * 39} 118 68v25l-118-68Z`}
              fill="var(--c-777169)"
            />
            {[0, 1, 2, 3, 4, 5, 6].map((j) => (
              <path
                key={j}
                d={`m${476 + j * 16} ${251 + n * 39 + j * 9.2} 9 5.2v20l-9-5.2Z`}
                fill={["var(--c-efe9df)", "var(--c-777e75)", "var(--c-b59479)", "var(--c-9389b4)"][j % 4]}
              />
            ))}
          </g>
        ))}
        <path
          d="M456 224v139l147 85V308Z"
          fill="var(--c-action)"
          opacity={protectedOn && selected === 0 ? 0.18 : 0}
          style={{ transition: "opacity 800ms ease" }}
        />
        <circle
          cx="552"
          cy="242"
          r="13"
          fill={selected === 0 ? "var(--c-action)" : "white"}
          stroke="var(--c-bcb5cc)"
        />
        <text
          x="552"
          y="246"
          textAnchor="middle"
          fontSize="10"
          fill={selected === 0 ? "var(--c-white)" : "var(--c-524b61)"}
        >
          01
        </text>
      </g>
      <g
        transform={transform(1, 365, 405)}
        className="ap-furniture"
        role="button"
        tabIndex={0}
        aria-label="책상 선택"
        aria-pressed={selected === 1}
        onClick={() => onSelect(1)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(1);
          }
        }}
      >
        <path
          d="m265 343 106-59 99 57-105 61Z"
          fill={selected === 1 ? "var(--c-cbc4e5)" : "var(--c-d7c9b4)"}
          stroke={selected === 1 ? "var(--c-b9b5ee)" : "var(--c-bcae98)"}
        />
        <path
          d="M265 343v9l100 58v-8Zm100 59 105-61v9l-105 60Z"
          fill="var(--c-baab95)"
        />
        <path
          d="M279 359v64m73-22v62m101-105v62"
          stroke="var(--c-8e887d)"
          strokeWidth="7"
        />
        <path d="m337 321 24-13 29 17-24 14Z" fill="var(--c-eeeae2)" />
        <path d="m337 321 29 17v5l-29-17Z" fill="var(--c-9d9aa3)" />
        <circle
          cx="366"
          cy="355"
          r="13"
          fill={selected === 1 ? "var(--c-action)" : "white"}
          stroke="var(--c-bcb5cc)"
        />
        <text
          x="366"
          y="359"
          textAnchor="middle"
          fontSize="10"
          fill={selected === 1 ? "var(--c-white)" : "var(--c-524b61)"}
        >
          02
        </text>
      </g>
      <g
        transform={transform(2, 240, 435)}
        className="ap-furniture"
        role="button"
        tabIndex={0}
        aria-label="라운지 체어 선택"
        aria-pressed={selected === 2}
        onClick={() => onSelect(2)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(2);
          }
        }}
      >
        <path
          d="m190 386 43-24 55 32v44l-43 24-55-32Z"
          fill={selected === 2 ? "var(--c-a99ec9)" : "var(--c-a5ad9d)"}
          stroke={selected === 2 ? "var(--c-b9b5ee)" : "var(--c-8d9687)"}
        />
        <path d="m190 386 55 32 43-24v44l-43 24v-44" fill="var(--c-8a9584)" />
        <path d="M202 435v30m72-20v28" stroke="var(--c-756d63)" strokeWidth="5" />
        <circle
          cx="232"
          cy="402"
          r="13"
          fill={selected === 2 ? "var(--c-action)" : "white"}
          stroke="var(--c-bcb5cc)"
        />
        <text
          x="232"
          y="406"
          textAnchor="middle"
          fontSize="10"
          fill={selected === 2 ? "var(--c-white)" : "var(--c-524b61)"}
        >
          03
        </text>
      </g>
      <g className="home-room-measurements" fill="var(--c-494250)" fontSize="22" fontFamily="inherit" fontWeight="500">
        <text x="105" y="510">
          6.0 m
        </text>
        <text x="638" y="483">
          8.0 m
        </text>
        <path d="M701 125v-32m-5 7 5-7 5 7" stroke="var(--c-action)" />
        <text x="697" y="83">
          N
        </text>
      </g>
    </svg>
  );
}
function ProtectionPlan() {
  return <figure className="home-protection-plan" aria-label="창의 일부를 가려 책장을 보호하고 옆 테이블에는 자연광을 남긴 공간">
    <svg viewBox="0 0 400 320" fill="none" aria-hidden="true">
      <path d="M22 282h356" stroke="var(--c-66616e)" />
      <path d="m210 50-8 230h174L308 50" fill="var(--c-f8dca2)" fillOpacity=".13" />
      <rect x="90" y="24" width="220" height="154" rx="3" fill="var(--c-e8e6ed)" />
      <rect x="98" y="32" width="204" height="138" fill="var(--c-eeece6)" />
      <path d="M98 32h100v138H98Z" fill="var(--c-9584cb)" />
      <path d="M98 78h204M98 124h204M149 32v138M200 32v138M251 32v138" stroke="var(--c-c6c1d2)" strokeWidth="2" />
      <path d="M90 178h220" stroke="var(--c-b7b2c0)" strokeWidth="5" />
      <rect x="51" y="181" width="112" height="99" rx="2" fill="var(--c-756a89)" />
      <rect x="59" y="188" width="96" height="84" fill="var(--c-38313f)" />
      {[0,1].map(row => <g key={row}>
        {[0,1,2,3,4,5].map(col => <rect key={col} x={66+col*14} y={194+row*40} width={col%2 ? 9 : 7} height={col%2 ? 28 : 31} rx="1" fill={["var(--c-a89abf)","var(--c-d0c6da)","var(--c-837893)"][col%3]} />)}
        <path d={`M59 ${229+row*40}h96`} stroke="var(--c-9b8eab)" strokeWidth="3" />
      </g>)}
      <path d="M224 223h132v9H224Z" fill="var(--c-d6b88c)" />
      <path d="M234 232v48m112-48v48" stroke="var(--c-ac9474)" strokeWidth="5" />
      <path d="m269 214 18-5 27 7-18 5Z" fill="var(--c-f2eee3)" />
      <path d="M329 199v15h10v-15" fill="var(--c-e6d7bf)" />
      <path d="M339 202h4v7h-4" stroke="var(--c-e6d7bf)" strokeWidth="2" />
      <circle cx="76" cy="304" r="3" fill="var(--c-ae9ae1)" /><text x="86" y="308">선택 영역 보호</text>
      <circle cx="248" cy="304" r="3" fill="var(--c-f3d59b)" /><text x="258" y="308">자연광 유지</text>
    </svg>
  </figure>;
}
// Marketing illustration only. Registration, editing, authentication and saves live in App.
export default function AwardPreview({
  onStart,
  onGuide,
  embedded = true,
}: {
  onStart: () => void;
  onGuide: () => void;
  embedded?: boolean;
}) {
  // Reduced motion keeps a still frame with the shelf protected.
  const [scene, setScene] = useState<{ hour: number; progress: number } | null>(() =>
      matchMedia("(prefers-reduced-motion: reduce)").matches ? null : sceneAt(0),
    ),
    [photoProtected, setPhotoProtected] = useState(true),
    [selected, setSelected] = useState(0),
    [activeStep, setActiveStep] = useState(0),
    visualRef = useRef<HTMLDivElement>(null);
  // Reduced motion shows the last frame: sun at the right end, shelf protected.
  const hour = scene?.hour ?? 20,
    protectedOn = scene ? beamOnShelf(hour) : true;
  const studio = onStart,
    openGuide = onGuide;
  useEffect(() => {
    const el = visualRef.current;
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    if (!el) return;
    let frame = 0,
      elapsed = 0,
      last = 0,
      onScreen = false;
    const tick = (now: number) => {
      elapsed = Math.min(PLAY_MS, elapsed + Math.min(100, now - (last || now)));
      last = now;
      setScene(sceneAt(elapsed));
      frame = elapsed < PLAY_MS ? requestAnimationFrame(tick) : 0;
    };
    // Plays once per visit. Scrolling away only pauses it, and once finished it rests on the last
    // frame with a slow glow. Leaving the browser tab rewinds it, so returning plays it again.
    const sync = () => {
      if (document.hidden && elapsed > 0 && !reduced.matches) {
        elapsed = 0;
        setScene(sceneAt(0));
      }
      const run = onScreen && !document.hidden && !reduced.matches && elapsed < PLAY_MS;
      el.classList.toggle("is-unseen", !onScreen || document.hidden);
      if (reduced.matches) setScene(null);
      if (run && !frame) {
        last = 0;
        frame = requestAnimationFrame(tick);
      } else if (!run && frame) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
    };
    const observer = new IntersectionObserver(([e]) => {
      onScreen = e.isIntersecting;
      sync();
    });
    observer.observe(el);
    document.addEventListener("visibilitychange", sync);
    reduced.addEventListener("change", sync);
    return () => {
      observer.disconnect();
      document.removeEventListener("visibilitychange", sync);
      reduced.removeEventListener("change", sync);
      cancelAnimationFrame(frame);
    };
  }, []);
  return (
    <div className={`ap home-design ${embedded ? "ap-embedded" : ""}`}>
      <main tabIndex={-1} id="ap-main">
        <section className="ap-hero">
          <div className="ap-hero-type">
            <h1>
              가릴 곳만 골라,
              <br />
              <span>햇빛 차단.</span>
            </h1>
            <div className="ap-hero-note">
              <p>
                가구와 보호 영역을 지정하고,
                <br />햇빛을 막을 시간을 설정하세요.
              </p>
              <button className="ap-round-link" onClick={studio}>
                <Scribble />
                <span>내 공간 열기</span>
                <b>
                  <Arrow diagonal />
                </b>
              </button>
            </div>
          </div>
          <div className="ap-hero-visual" ref={visualRef}>
            <div
              className={`ap-sun-glow ${scene?.progress === 1 ? "is-resting" : ""}`}
              style={{ "--sun": (hour - 5) / 15 } as CSSProperties}
              aria-hidden="true"
            />

            <div className="ap-visual-label">
              <span>가구를 눌러 보호할 곳을 골라 보세요</span>
            </div>
            <div className="ap-room-stage">

              <Room
                hour={hour}
                protectedOn={protectedOn}
                selected={selected}
                onSelect={setSelected}
              />
              <div className="ap-object-label">
                <span className="ap-dot" />
                <span>
                  {objects[selected].name}
                  <small key={String(protectedOn)} className="ap-fade-up">
                    {protectedOn
                      ? "선택한 영역 보호 중"
                      : "자연광이 들어오는 중"}
                  </small>
                </span>
              </div>
            </div>
          </div>
        </section>
        <section className="ap-statement" id="ap-experience">
          <h2>
            창문 일부만 가려,
            <br />보호 영역에 그늘을.
          </h2>
          <div className="ap-statement-bottom">
            <SunMark />
            <p>
              보호가 필요한 곳만 가리고,
              <br />나머지 공간에는 자연광을 남깁니다.
            </p>
            <button className="ap-text-link" onClick={studio}>
              내 공간 열기 <Arrow />
            </button>
          </div>
        </section>
        <section className={`ap-photo-section ${photoProtected ? "is-protected" : ""}`}>
          <div className="home-photo-media">
            <img src="/design/library-protection-hero.webp"
              alt="창가의 햇빛과 그늘이 함께 있는 서가와 라운지" loading="lazy" />
            <div className="home-photo-shade" aria-hidden="true" />
          </div>
          <div className="ap-photo-copy">
            <h2>
              책장은 그늘로,
              <br />주변은 자연광 그대로.
            </h2>
            <button onClick={() => setPhotoProtected(!photoProtected)}
              className="ap-photo-tag" role="switch" aria-checked={photoProtected} aria-label="사진 속 선택 영역 보호">
              <span className="home-switch-track" aria-hidden="true"><i /></span>
              <span className="home-switch-label">{photoProtected ? "선택 영역 보호" : "자연광 유지"}</span>
            </button>
            <span className="home-photo-caption">공간 적용 연출 이미지</span>
          </div>
        </section>
        <section className="ap-process" id="ap-process">
          <div className="ap-process-heading">
            <h2>
              공간 등록부터,
              <br />차단 시간 설정까지.
            </h2>
            <button className="ap-text-link home-guide-link" onClick={openGuide}>가이드 전체 보기 <Arrow /></button>
            <div className="home-step-preview" aria-hidden="true">
              {[2, 4, 5].map((step, i) => <div key={step} className={`home-step-layer ${activeStep === i ? "is-active" : ""}`}>
                <GuideIllustration step={step} />
              </div>)}
            </div>
          </div>
          <div className="ap-process-list">

            {[
              {
                title: "QR로 공간 인식",
                text: "상판 형태에 맞춰 각 지점에서 창문 QR 마커를 3장씩 촬영합니다.",
                art: "scan",
              },
              {
                title: "보호 영역 지정",
                text: "보호할 곳을 선택하고 위치·치수·등급을 설정합니다.",
                art: "select",
              },
              {
                title: "차단 시간 설정",
                text: "햇빛을 막을 시간을 설정합니다.",
                art: "time",
              },
            ].map((step, i) => (
              <button
                key={step.title}
                className="ap-process-row"
                onPointerEnter={() => setActiveStep(i)}
                onFocus={() => setActiveStep(i)}
                onClick={() => {
                  openGuide();
                }}
              >
                <span>0{i + 1}</span>
                <div>
                  <h3>{step.title}</h3>
                  <p>{step.text}</p>
                </div>
                <div
                  className={`ap-process-art mood-art ${step.art}`}
                  aria-hidden="true"
                >
                  <HomeStepIcon kind={step.art as "scan" | "select" | "time"} />
                </div>
                <Arrow diagonal />
              </button>
            ))}
          </div>
        </section>
        <section className="ap-final">
          <h2>
            보호 영역과
            <br />
            <span>차단 시간 설정</span>
          </h2>
          <button className="ap-round-link" onClick={studio}>
            <Scribble />
            <span>내 공간 열기</span>
            <b>
              <Arrow diagonal />
            </b>
          </button>
          <ProtectionPlan />
        </section>
      </main>
    </div>
  );
}
