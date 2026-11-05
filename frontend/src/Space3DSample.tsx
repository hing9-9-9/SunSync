import { useEffect, useMemo, useRef, useState } from "react";
import { blankSpace, furnitureTypes, grades } from "./model.ts";
import type { Furniture, Grade, Region, Space } from "./model.ts";
import { createSpaceScene } from "./Space3DScene.ts";
import type { DayProfile, Level, Lit, SceneApi } from "./Space3DScene.ts";
import { dayOfYear, sunPosition } from "./sun3d.ts";
import "./Space3DSample.css";

// Sample room until the web space screen passes a saved scene in (issue #102).
function sampleSpace(): Space {
  const f = (id: string, name: string, type: Furniture["type"], x: number, y: number, width: number, depth: number, height: number): Furniture =>
    ({ id, name, type, zone: "구역 01", x, y, width, depth, height });
  const furniture = [
    f("f1", "책장 01", "bookcase", 0.4, 0.3, 2.4, 0.5, 2.0),
    f("f2", "책장 02", "bookcase", 3.1, 0.3, 2.4, 0.5, 2.0),
    f("f3", "책장 03", "bookcase", 2.6, 2.5, 2.8, 0.5, 1.6),
    f("f4", "열람 테이블", "table", 0.9, 3.9, 1.8, 0.9, 0.75),
    f("f5", "고서 진열장", "display", 5.4, 4.3, 1.4, 0.6, 1.0),
    f("f6", "사서 책상", "desk", 6.3, 2.2, 1.2, 0.6, 0.75),
    f("f7", "수납장", "cabinet", 6.6, 0.3, 1.0, 0.5, 1.2),
  ];
  const region = (id: string, of: Furniture, grade: Grade, bottom: number, top: number): Region =>
    ({ id, name: of.name, zone: of.zone, furnitureId: of.id, x: of.x, y: of.y, width: of.width, depth: of.depth, face: "남쪽 전면", bottom, top, grade });
  return {
    ...blankSpace(),
    id: "sample-3d",
    building: "SunSync 도서관",
    floor: "2층",
    name: "2층 열람실",
    width: 8,
    depth: 6,
    height: 3,
    windowDimensions: { width: 5, height: 2, wall: "south", offset: 1.5, sill: 0.6 },
    furniture,
    regions: [region("r1", furniture[2], "A", 0, 1.6), region("r2", furniture[4], "B", 0, 1.0), region("r3", furniture[3], "C", 0.7, 0.8), region("r4", furniture[5], "D", 0, 0.75)],
    site: { latitude: 37.57, longitude: 126.98, timezone: "Asia/Seoul", northConfirmed: true },
  };
}

const LEVELS: { id: Level; label: string; lead: string }[] = [
  { id: "basic", label: "기본 보기", lead: "저장된 크기와 위치대로 방, 창, 가구, 보호 영역을 세웠어요. 끌어서 돌려 보고, 가구를 눌러 확인하세요." },
  { id: "sun", label: "햇빛과 그림자", lead: "그날의 실제 해 위치로 창을 통과한 햇빛과 그림자를 그려요. 주황색 테두리는 지금 햇빛이 닿는 보호 영역이에요." },
  { id: "film", label: "필름 칸", lead: "창의 10 cm 칸마다 보호 영역으로 가는 햇빛을 계산해 필요한 칸만 막아요. 진할수록 많이 막는 칸이에요." },
];
const today = new Date();
const DAYS = [
  { id: "today", label: `오늘 ${today.getMonth() + 1}/${today.getDate()}`, day: dayOfYear(today.getMonth() + 1, today.getDate()) },
  { id: "summer", label: "하지", day: dayOfYear(6, 21) },
  { id: "winter", label: "동지", day: dayOfYear(12, 21) },
];
const clock = (h: number) => `${String(Math.floor(h)).padStart(2, "0")}:${String(Math.round((h % 1) * 60) % 60).padStart(2, "0")}`;
const compassWord = (az: number) => ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][Math.round(az / 45) % 8];
const pct = (v: number) => `${Math.round(v * 100)}%`;

export default function Space3DSample() {
  const space = useMemo(() => sampleSpace(), []);
  const host = useRef<HTMLDivElement>(null);
  const needle = useRef<HTMLDivElement>(null);
  const api = useRef<SceneApi | null>(null);
  const [level, setLevel] = useState<Level>("basic");
  const [view, setView] = useState<"3d" | "2d">("3d");
  const [hour, setHour] = useState(12.5);
  const [dayId, setDayId] = useState("today");
  const [playing, setPlaying] = useState(false);
  const [lit, setLit] = useState<Lit | null>(null);
  const [profile, setProfile] = useState<DayProfile | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const day = DAYS.find(d => d.id === dayId)!.day;

  useEffect(() => {
    const scene = createSpaceScene(host.current!, space, {
      onSelect: setSelected,
      onAzimuth: deg => needle.current?.style.setProperty("--az", `${deg}deg`),
    });
    api.current = scene;
    return () => scene.dispose();
  }, [space]);
  useEffect(() => api.current?.setLevel(level), [level]);
  useEffect(() => setLit(api.current!.setTime(day, hour)), [day, hour]);
  // The day's timeline costs a few thousand rays, so it waits until the first frame is drawn.
  useEffect(() => {
    const id = setTimeout(() => setProfile(api.current!.dayProfile(day)), 60);
    return () => clearTimeout(id);
  }, [day]);
  useEffect(() => {
    if (!playing) return;
    let frame = 0, last = performance.now();
    const tick = (now: number) => {
      setHour(h => (h + ((now - last) / 1000) * 1.1 >= 18 ? 6 : h + ((now - last) / 1000) * 1.1));
      last = now;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  const sunNow = sunPosition(day, hour, space.site!.latitude, space.site!.longitude, 9);
  const pick = (id: string | null) => {
    setSelected(id);
    api.current?.select(id);
  };
  const chosen = space.furniture.find(f => f.id === selected);
  const chosenRegion = space.regions.find(r => r.furnitureId === selected);
  const info = LEVELS.find(l => l.id === level)!;
  const values = level === "film" ? lit?.film : lit?.open;
  const series = profile && (level === "film" ? profile.film : profile.open);

  return (
    <div className="s3">
      <header className="s3-head">
        <div className="s3-title">
          <p className="s3-kicker">3D 공간 보기 · 샘플 화면</p>
          <h1>{space.name}</h1>
          <p className="s3-meta">{space.building} · {space.width.toFixed(1)} × {space.depth.toFixed(1)} × {space.height.toFixed(1)} m · 남쪽 창 5.0 × 2.0 m</p>
        </div>
        <div className="s3-steps" role="tablist" aria-label="샘플 단계">
          {LEVELS.map((l, i) => (
            <button key={l.id} role="tab" aria-selected={level === l.id} className={level === l.id ? "on" : ""} onClick={() => setLevel(l.id)}>
              <span className="n">{String(i + 1).padStart(2, "0")}</span>{l.label}
            </button>
          ))}
        </div>
      </header>

      <main className="s3-body">
        <section className="s3-stage" aria-label="3D 공간">
          <div ref={host} className="s3-canvas" />
          <div className="s3-compass" aria-label="북쪽 방향">
            <div ref={needle} className="s3-needle"><span>N</span></div>
          </div>
          <div className="s3-tools">
            <div className="s3-seg" role="group" aria-label="보기 방식">
              {(["2d", "3d"] as const).map(v => (
                <button key={v} aria-pressed={view === v} className={view === v ? "on" : ""} onClick={() => { setView(v); api.current?.setView(v); }}>{v.toUpperCase()}</button>
              ))}
            </div>
            <button className="s3-ghost" onClick={() => api.current?.reset()}>
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8a5 5 0 1 0 1.6-3.7M3 2.5v2.8h2.8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
              처음 시점
            </button>
          </div>
          {level !== "basic" && (
            <div className="s3-time">
              <button className="s3-play" aria-label={playing ? "멈춤" : "하루 재생"} onClick={() => setPlaying(p => !p)}>
                {playing
                  ? <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9M11 3.5v9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
                  : <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.2v9.6L12.6 8z" fill="currentColor" /></svg>}
              </button>
              <div className="s3-clock">
                <strong>{clock(hour)}</strong>
                <span>{sunNow.altitude > 0 ? `해 높이 ${Math.round(sunNow.altitude)}° · ${compassWord(sunNow.azimuth)}쪽` : "해가 진 시간"}</span>
              </div>
              <input
                className="s3-range" type="range" min={6} max={18} step={1 / 12} value={hour} aria-label="시간"
                style={{ ["--p" as string]: `${((hour - 6) / 12) * 100}%` }}
                onChange={e => { setPlaying(false); setHour(Number(e.target.value)); }}
              />
              <div className="s3-days" role="group" aria-label="날짜">
                {DAYS.map(d => (
                  <button key={d.id} aria-pressed={dayId === d.id} className={dayId === d.id ? "on" : ""} onClick={() => setDayId(d.id)}>{d.label}</button>
                ))}
              </div>
            </div>
          )}
        </section>

        <aside className="s3-panel">
          <p className="s3-lead">{info.lead}</p>
          {level === "film" && lit && (
            <div className="s3-stat">
              <div><strong>{lit.blocked}</strong><span>막은 칸</span></div>
              <div><strong>{lit.cells - lit.blocked}</strong><span>열어 둔 칸</span></div>
              <div><strong>{pct(lit.blocked / Math.max(1, lit.cells))}</strong><span>창 면적</span></div>
            </div>
          )}
          <h2>보호 영역</h2>
          <ul className="s3-regions">
            {space.regions.map(r => {
              const v = values?.[r.id] ?? 0;
              return (
                <li key={r.id}>
                  <button className={selected === r.furnitureId ? "on" : ""} onClick={() => pick(selected === r.furnitureId ? null : r.furnitureId)}>
                    <span className={`s3-grade g-${r.grade}`}>{r.grade}</span>
                    <span className="s3-rname">{r.name}<small>{grades[r.grade].split(" · ")[0]}</small></span>
                    {level === "basic"
                      ? <span className="s3-rval">{(r.top - r.bottom).toFixed(2)} m 높이</span>
                      : <span className={`s3-rval ${v > 0.005 ? "sun" : ""}`}>{v > 0.005 ? `햇빛 ${pct(v)}` : level === "film" && (lit?.open[r.id] ?? 0) > 0.005 ? "막음" : "닿지 않음"}</span>}
                    {level !== "basic" && (
                      <span className="s3-strip" aria-hidden="true">
                        {series?.[r.id].map((x, i) => <i key={i} style={{ opacity: 0.08 + x * 0.92 }} />)}
                        <b style={{ left: `${((hour - 6) / 12) * 100}%` }} />
                      </span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
          {level !== "basic" && <p className="s3-axis"><span>6시</span><span>9</span><span>12</span><span>15</span><span>18시</span></p>}

          <h2>선택한 가구</h2>
          {chosen ? (
            <dl className="s3-detail">
              <div><dt>이름</dt><dd>{chosen.name}</dd></div>
              <div><dt>종류</dt><dd>{furnitureTypes[chosen.type ?? "other"]}</dd></div>
              <div><dt>크기</dt><dd>{chosen.width.toFixed(1)} × {chosen.depth.toFixed(1)} × {chosen.height.toFixed(2)} m</dd></div>
              <div><dt>위치</dt><dd>서쪽 벽에서 {chosen.x.toFixed(1)} m · 북쪽 벽에서 {chosen.y.toFixed(1)} m</dd></div>
              <div><dt>보호</dt><dd>{chosenRegion ? `${chosenRegion.grade}등급 · ${grades[chosenRegion.grade].split(" · ")[1]}` : "보호 영역 없음"}</dd></div>
            </dl>
          ) : <p className="s3-empty">3D 화면이나 위 목록에서 가구를 누르면 여기에 크기와 위치가 나와요.</p>}

          <div className="s3-legend" aria-label="범례">
            <span><i className="sw-a" />보호 영역 (진할수록 높은 등급)</span>
            <span><i className="sw-d" />D · 보호 안 함</span>
            <span><i className="sw-sun" />햇빛</span>
            <span><i className="sw-film" />막은 필름 칸</span>
          </div>
          <p className="s3-note">마우스 왼쪽으로 돌리기 · 휠로 확대 · 오른쪽으로 이동. 샘플 데이터이고, 해 위치는 서울 기준 계산값이에요.</p>
        </aside>
      </main>
    </div>
  );
}
