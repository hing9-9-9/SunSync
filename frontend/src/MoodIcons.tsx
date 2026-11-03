// Web-only line icons and guide illustrations (moodboard style: Senso / Spendex / STEEL).
// 1.5px charcoal strokes with round caps, white or grey rounded tiles, and one accent per drawing:
// iris = selection and protection (light purple fill), orange = sun. Floor plans stay square-cornered.
const ink = "var(--c-protect-ink)";
const block = "var(--c-block)";
const iris = "var(--c-action)";
const sun = "var(--c-sun)";
const protect = "var(--c-protect-fill)";

const line = { stroke: ink, strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, fill: "none" };

export function StepIcon({ kind }: { kind: "scan" | "select" | "time" }) {
  return (
    <svg className="mood-step-icon" viewBox="0 0 48 48" aria-hidden="true">
      {kind === "scan" && (
        <>
          <path d="M10 17v-5a2 2 0 0 1 2-2h5M31 10h5a2 2 0 0 1 2 2v5M38 31v5a2 2 0 0 1-2 2h-5M17 38h-5a2 2 0 0 1-2-2v-5" {...line} />
          <rect x="17" y="17" width="14" height="14" {...line} />
          <rect x="20.5" y="20.5" width="4" height="4" fill={iris} />
          <path d="M27.5 20.5v0M20.5 27.5h7v0" {...line} />
        </>
      )}
      {kind === "select" && (
        <>
          <rect x="9" y="11" width="30" height="26" {...line} />
          <rect x="13" y="15" width="11" height="6" fill={protect} stroke={iris} strokeWidth="1.5" />
          <rect x="27" y="26" width="8" height="7" {...line} />
          <path d="M9 31l6 0" {...line} />
        </>
      )}
      {kind === "time" && (
        <>
          <circle cx="24" cy="25" r="13" {...line} />
          <path d="M24 18v7l5 3" {...line} />
          <circle cx="36" cy="13" r="4" fill={sun} />
        </>
      )}
    </svg>
  );
}

export function GuideIllustration({ step }: { step: number }) {
  return (
    <svg className={`website-guide-art mood-guide-art art-${step}`} viewBox="40 22 200 108" fill="none" aria-hidden="true">
      {step === 0 && (
        <>
          <rect x="56" y="38" width="36" height="36" rx="8" fill={block} />
          <path d="M66 66V50l8-5 8 5v16M66 66h16M71 66v-6h6v6" {...line} />
          <rect x="104" y="40" width="120" height="14" rx="7" fill="var(--c-white)" stroke={block} strokeWidth="1.5" />
          <rect x="104" y="62" width="120" height="14" rx="7" fill="var(--c-white)" stroke={iris} strokeWidth="1.5" />
          <rect x="104" y="84" width="84" height="14" rx="7" fill="var(--c-white)" stroke={block} strokeWidth="1.5" />
          <path d="M112 47h40M112 69h56M112 91h30" stroke="var(--c-9d9d9d)" strokeWidth="1.5" strokeLinecap="round" />
          <rect x="196" y="104" width="28" height="16" rx="8" fill={iris} />
          <path d="M206 112h8m-3-3 3 3-3 3" stroke="var(--c-white)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </>
      )}
      {step === 1 && (
        <>
          <rect x="58" y="36" width="108" height="80" {...line} />
          <path d="M94 36v80M130 36v80M58 76h108" stroke={ink} strokeWidth="1" strokeOpacity=".35" />
          <rect x="134" y="80" width="20" height="20" fill="var(--c-white)" stroke={iris} strokeWidth="1.5" />
          <rect x="138" y="84" width="5" height="5" fill={iris} />
          <rect x="145" y="91" width="5" height="5" fill={iris} />
          <path d="M182 36v80M179 36h6M179 116h6M58 124h108M58 121v6M166 121v6" {...line} />
          <path d="M196 56h26M196 76h18M196 96h22" stroke="var(--c-9d9d9d)" strokeWidth="1.5" strokeLinecap="round" />
        </>
      )}
      {step === 2 && (
        <>
          <rect x="58" y="36" width="108" height="80" {...line} />
          <rect x="74" y="56" width="60" height="32" {...line} />
          <circle cx="74" cy="56" r="4" fill={iris} />
          <circle cx="134" cy="56" r="3" fill="var(--c-white)" stroke={ink} strokeWidth="1.5" />
          <circle cx="134" cy="88" r="3" fill="var(--c-white)" stroke={ink} strokeWidth="1.5" />
          <circle cx="74" cy="88" r="3" fill="var(--c-white)" stroke={ink} strokeWidth="1.5" />
          <rect x="184" y="32" width="40" height="88" rx="9" {...line} />
          <path d="M193 58v-6h6M209 52h6v6M215 76v6h-6M199 82h-6v-6" {...line} />
          <rect x="199" y="61" width="10" height="10" fill={iris} />
          <path d="M200 110h8" {...line} />
        </>
      )}
      {step === 3 && (
        <>
          <rect x="58" y="36" width="108" height="80" {...line} />
          <rect x="68" y="46" width="34" height="14" fill="var(--c-white)" stroke={iris} strokeWidth="1.5" />
          {[[68, 46], [102, 46], [68, 60], [102, 60]].map(([cx, cy]) => (
            <rect key={`${cx}-${cy}`} x={cx - 2.5} y={cy - 2.5} width="5" height="5" fill="var(--c-white)" stroke={iris} strokeWidth="1.5" />
          ))}
          <rect x="114" y="46" width="40" height="14" {...line} />
          <rect x="128" y="92" width="26" height="14" {...line} />
          <path d="M68 70h34M68 67v6M102 67v6" stroke={iris} strokeWidth="1.5" strokeLinecap="round" />
          <path d="M184 52h38M184 72h30M184 92h34" stroke="var(--c-9d9d9d)" strokeWidth="1.5" strokeLinecap="round" />
        </>
      )}
      {step === 4 && (
        <>
          <rect x="58" y="36" width="108" height="80" {...line} />
          <rect x="70" y="48" width="44" height="18" fill={protect} stroke={iris} strokeWidth="1.5" />
          <rect x="124" y="84" width="30" height="20" {...line} strokeDasharray="3 3" />
          {[iris, "var(--c-8c87ec)", "var(--c-bdbaf4)"].map((c, i) => (
            <rect key={c} x="184" y={40 + i * 20} width="14" height="14" fill={c} />
          ))}
          <rect x="184" y="100" width="14" height="14" fill="var(--c-white)" stroke="var(--c-6f6b77)" strokeWidth="1.5" strokeDasharray="2 2" />
          <path d="M206 47h16M206 67h16M206 87h16M206 107h16" stroke="var(--c-9d9d9d)" strokeWidth="1.5" strokeLinecap="round" />
        </>
      )}
      {step >= 5 && (
        <>
          <circle cx="80" cy="63" r="23" {...line} fill="var(--c-white)" />
          <path d="M80 63 95.6 54a18 18 0 0 1-6.6 24.6Z" fill={protect} />
          <path d="M80 44v3m19 16h-3m-16 19v-3m-19-16h3" stroke={ink} strokeWidth="1.2" strokeLinecap="round" />
          <path d="M80 51v12l10-6" {...line} />
          <circle cx="80" cy="63" r="2" fill={iris} />
          <g fill={ink} fontFamily="inherit">
            <text x="116" y="50" fontSize="10" fontWeight="500">차단 시간</text>
            <text x="116" y="71" fontSize="13" fontWeight="700">14:00 — 17:00</text>
          </g>
          <rect x="58" y="98" width="164" height="8" rx="4" fill={block} />
          <rect x="153.67" y="98" width="20.5" height="8" rx="4" fill={iris} />
          <path d="M153.67 93v18m20.5-18v18" stroke={iris} strokeWidth="1.2" />
          <g fill={ink} fontFamily="inherit" fontSize="9">
            <text x="58" y="123">00:00</text>
            <text x="222" y="123" textAnchor="end">24:00</text>
          </g>
        </>
      )}
    </svg>
  );
}
