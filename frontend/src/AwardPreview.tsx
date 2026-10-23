import { useEffect, useState } from "react";
import "./AwardPreview.css";

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
      [-45, -65],
      [-10, -10],
      [85, -38],
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
          <stop stopColor="#ecebe6" />
          <stop offset="1" stopColor="#faf9f6" />
        </linearGradient>
        <linearGradient id="ap-wall">
          <stop stopColor="#e3e1dc" />
          <stop offset="1" stopColor="#f3f1ed" />
        </linearGradient>
        <linearGradient id="ap-ray" x1="0" y1="0" x2="0" y2="1">
          <stop stopColor="#fbdf98" stopOpacity=".65" />
          <stop offset="1" stopColor="#fff0c4" stopOpacity=".15" />
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
        fill="#343040"
        opacity=".09"
        filter="url(#ap-shadow)"
      />
      <path
        d="m104 354 303-168 294 168-303 174Z"
        fill="#d4d0c9"
        transform="translate(0 24)"
      />
      <path
        d="M104 343 407 170l294 173-303 173Z"
        fill="url(#ap-floor)"
        stroke="#d5d1ca"
        transform="translate(0 24)"
      />
      <path
        d="M104 343V150L407 0v170Z"
        fill="url(#ap-wall)"
        transform="translate(0 24)"
      />
      <path d="M407 24 701 193v174L407 194Z" fill="#eeece7" />
      <g
        transform="translate(0 24)"
        stroke="#d8d5ce"
        strokeWidth=".7"
        opacity=".7"
      >
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <path key={i} d={`M${150 + i * 46} ${369 + i * 26}l303-173`} />
        ))}
      </g>
      <path
        d="m168 180 182-91v135l-182 99Z"
        fill="#fafbff"
        stroke="#c5c1b8"
        strokeWidth="5"
      />
      <g transform="matrix(1 -.5 0 1 171 183)">
        {Array.from({ length: 60 }, (_, i) => (
          <rect
            key={i}
            x={(i % 10) * 17.5}
            y={Math.floor(i / 10) * 21}
            width="16"
            height="19.5"
            fill={
              protectedOn && i % 10 > 3 && i % 10 < 8 && Math.floor(i / 10) > 1
                ? "#8075b7"
                : "#e0e5eb"
            }
            opacity={protectedOn ? ".9" : ".48"}
            style={{ transition: `fill 600ms ${i * 3}ms` }}
          />
        ))}
      </g>
      <path
        d={`M176 325 351 229 ${572 + shift} 358 ${389 + shift} 465Z`}
        fill="url(#ap-ray)"
        opacity={hour < 6 || hour >= 18 ? 0 : 1}
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
        <path d="m458 226 34-19 144 83-34 19Z" fill="#c3b8a5" />
        <path
          d="M458 226v134l144 84V309Z"
          fill={selected === 0 ? "#c7c0df" : "#c7bba7"}
          stroke={selected === 0 ? "#6660e5" : "#a79c8a"}
        />
        <path d="m602 309 34-19v134l-34 20Z" fill="#aca08d" />
        {[0, 1, 2].map((n) => (
          <g key={n}>
            <path
              d={`m470 ${245 + n * 39} 118 68v25l-118-68Z`}
              fill="#918577"
            />
            {[0, 1, 2, 3, 4, 5, 6].map((j) => (
              <path
                key={j}
                d={`m${476 + j * 16} ${251 + n * 39 + j * 9.2} 9 5.2v20l-9-5.2Z`}
                fill={["#e6e0d4", "#a6a68e", "#d5c9b4", "#ece8df"][j % 4]}
              />
            ))}
          </g>
        ))}
        {protectedOn && (
          <path d="M456 224v139l147 85V308Z" fill="#6660e5" opacity=".18" />
        )}
        <circle
          cx="552"
          cy="242"
          r="13"
          fill={selected === 0 ? "#6660e5" : "white"}
          stroke="#bcb5cc"
        />
        <text
          x="552"
          y="246"
          textAnchor="middle"
          fontSize="10"
          fill={selected === 0 ? "white" : "#524b61"}
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
          fill={selected === 1 ? "#cbc4e5" : "#d7c9b4"}
          stroke={selected === 1 ? "#6660e5" : "#bcae98"}
        />
        <path
          d="M265 343v9l100 58v-8Zm100 59 105-61v9l-105 60Z"
          fill="#baab95"
        />
        <path
          d="M279 359v64m73-22v62m101-105v62"
          stroke="#b2a38d"
          strokeWidth="7"
        />
        <path d="m337 321 24-13 29 17-24 14Z" fill="#eeeae2" />
        <path d="m337 321 29 17v5l-29-17Z" fill="#9d9aa3" />
        <circle
          cx="366"
          cy="355"
          r="13"
          fill={selected === 1 ? "#6660e5" : "white"}
          stroke="#bcb5cc"
        />
        <text
          x="366"
          y="359"
          textAnchor="middle"
          fontSize="10"
          fill={selected === 1 ? "white" : "#524b61"}
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
          fill={selected === 2 ? "#a99ec9" : "#a5ad9d"}
          stroke={selected === 2 ? "#6660e5" : "#8d9687"}
        />
        <path d="m190 386 55 32 43-24v44l-43 24v-44" fill="#8a9584" />
        <path d="M202 435v30m72-20v28" stroke="#756d63" strokeWidth="5" />
        <circle
          cx="232"
          cy="402"
          r="13"
          fill={selected === 2 ? "#6660e5" : "white"}
          stroke="#bcb5cc"
        />
        <text
          x="232"
          y="406"
          textAnchor="middle"
          fontSize="10"
          fill={selected === 2 ? "white" : "#524b61"}
        >
          03
        </text>
      </g>
      <g fill="#3f3d47" fontSize="11" fontFamily="monospace">
        <text x="105" y="510">
          6.0 m
        </text>
        <text x="638" y="483">
          8.0 m
        </text>
        <path d="M701 125v-32m-5 7 5-7 5 7" stroke="#6660e5" />
        <text x="697" y="83">
          N
        </text>
      </g>
    </svg>
  );
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
  const [hour, setHour] = useState(14),
    [protectedOn, setProtected] = useState(true),
    [selected, setSelected] = useState(0);
  const studio = onStart,
    openGuide = onGuide;
  const time = `${String(hour).padStart(2, "0")}:00`;
  useEffect(() => {
    const elements = document.querySelectorAll(
      ".ap-embedded .ap-statement,.ap-embedded .ap-photo-section,.ap-embedded .ap-process,.ap-embedded .ap-screens,.ap-embedded .ap-final",
    );
    const observer = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("ap-revealed");
            observer.unobserve(e.target);
          }
        }),
      { threshold: 0.12 },
    );
    elements.forEach((el) => {
      el.classList.add("ap-reveal");
      observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);
  return (
    <div className={`ap ${embedded ? "ap-embedded" : ""}`}>
      <main tabIndex={-1} id="ap-main">
        <section className="ap-hero">
          <div className="ap-hero-type">
            <h1>
              빛은 그대로.
              <br />
              <span>보호는 섬세하게.</span>
            </h1>
            <div className="ap-hero-note">
              <p>
                창 전체를 가릴 필요 없이.
                <br />
                소중한 곳에 닿는 햇빛만 조절하는
                <br />
                새로운 공간 경험, SunSync.
              </p>
              <button className="ap-round-link" onClick={studio}>
                <span>내 공간 설계하기</span>
                <b>
                  <Arrow diagonal />
                </b>
              </button>
            </div>
          </div>
          <div className="ap-hero-visual">
            <div className="ap-visual-label">
              <span>가구를 눌러 보호할 곳을 골라 보세요</span>
            </div>
            <div className="ap-room-stage">
              <div className="ap-orbit ap-orbit-one" />
              <div className="ap-orbit ap-orbit-two" />
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
                  <small>
                    {protectedOn
                      ? "선택한 영역 보호 중"
                      : "자연광이 들어오는 중"}
                  </small>
                </span>
              </div>
            </div>
            <div className="ap-visual-controls">
              <div className="ap-time">
                <SunMark />
                <strong>{time}</strong>
                <span>햇빛 미리보기</span>
              </div>
              <label className="ap-slider">
                <span className="ap-sr">햇빛 확인 시간</span>
                <span>0시</span>
                <input
                  type="range"
                  min="0"
                  max="23"
                  value={hour}
                  onChange={(e) => setHour(Number(e.target.value))}
                />
                <span>23시</span>
              </label>
              <button
                className={`ap-toggle ${protectedOn ? "on" : ""}`}
                role="switch"
                aria-checked={protectedOn}
                onClick={() => setProtected(!protectedOn)}
              >
                <span>부분 차단</span>
                <i>
                  <b />
                </i>
              </button>
            </div>
          </div>
          <div className="ap-hero-foot">
            <span>필요한 빛은 남기고, 소중한 공간은 지키고.</span>
          </div>
        </section>
        <section className="ap-statement" id="ap-experience">
          <h2>
            빛을 끄는 대신,
            <br />
            <span>빛을 디자인합니다.</span>
          </h2>
          <div className="ap-statement-bottom">
            <SunMark />
            <p>
              책장에는 그늘을, 테이블에는 햇살을.
              <br />
              같은 공간에서도 필요한 빛은 다르니까.
              <br />
              SunSync는 창문의 작은 영역을 조절해
              <br />
              공간마다 다른 일상을 만듭니다.
            </p>
            <button className="ap-text-link" onClick={studio}>
              직접 조절해 보기 <Arrow />
            </button>
          </div>
        </section>
        <section className="ap-photo-section">
          <img
            src="/design/library-protection-hero.webp"
            alt="창가의 햇빛과 그늘이 함께 있는 서가와 라운지"
            loading="lazy"
          />
          <div className="ap-photo-copy">
            <h2>
              햇살이 머무는 곳.
              <br />
              당신이 정하는 방식.
            </h2>
            <span>공간 적용을 표현한 연출 이미지</span>
          </div>
          <button
            onClick={() => setProtected(!protectedOn)}
            className="ap-photo-tag"
          >
            {protectedOn ? "선택한 영역 보호" : "자연광 유지"}
            <span className="ap-dot" />
          </button>
        </section>
        <section className="ap-process" id="ap-process">
          <div className="ap-process-heading">
            <h2>
              당신의 공간에서,
              <br />세 번의 간단한 시작.
            </h2>
          </div>
          <div className="ap-process-list">
            {[
              {
                title: "공간을 담고",
                text: "앱에서 가구 네 모서리의 QR을 촬영해 2D 공간 인식.",
                art: "scan",
              },
              {
                title: "보호할 곳을 고르고",
                text: "가구와 영역을 선택하고 위치·치수·보호 등급 조정.",
                art: "select",
              },
              {
                title: "시간을 맞추면",
                text: "원하는 차단 시간을 설정하고, 웹과 앱에서 함께 확인.",
                art: "time",
              },
            ].map((step, i) => (
              <button
                key={step.title}
                className="ap-process-row"
                onClick={() => {
                  openGuide();
                }}
              >
                <div>
                  <h3>{step.title}</h3>
                  <p>{step.text}</p>
                </div>
                <div
                  className={`ap-process-art ${step.art}`}
                  aria-hidden="true"
                >
                  {i === 0 ? (
                    <>
                      <i />
                      <i />
                      <i />
                      <i />
                      <b>⌗</b>
                    </>
                  ) : i === 1 ? (
                    <>
                      <i />
                      <i />
                      <i />
                      <b />
                    </>
                  ) : (
                    <>
                      <i />
                      <b>09—18</b>
                    </>
                  )}
                </div>
                <Arrow diagonal />
              </button>
            ))}
          </div>
        </section>
        <section className="ap-screens" aria-labelledby="ap-screens-title">
          <h2 id="ap-screens-title">
            휴대폰 하나로,
            <br />
            측정부터 차단 시간까지.
          </h2>
          <div className="ap-screens-row">
            {[
              ["app-capture", "가구 모서리의 QR을 찍어 공간을 담습니다"],
              ["app-review", "도면에서 가구를 옮기고 치수를 고칩니다"],
              ["app-detail", "보호구역과 차단 시간을 한눈에 확인합니다"],
            ].map(([file, caption]) => (
              <figure key={file}>
                <img
                  src={`/design/${file}.jpg`}
                  alt={`SunSync 앱 화면: ${caption}`}
                  width="390"
                  height="844"
                  loading="lazy"
                />
                <figcaption>{caption}</figcaption>
              </figure>
            ))}
          </div>
        </section>
        <section className="ap-final">
          <h2>
            이제, 당신의 빛을
            <br />
            <span>설계할 차례.</span>
          </h2>
          <button className="ap-round-link" onClick={studio}>
            <span>공간 스튜디오 열기</span>
            <b>
              <Arrow diagonal />
            </b>
          </button>
          <div className="ap-final-disc" aria-hidden="true">
            <SunMark />
          </div>
        </section>
      </main>
    </div>
  );
}
