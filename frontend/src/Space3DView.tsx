import { useEffect, useMemo, useRef, useState } from "react";
import { filmGrid, furnitureTypes, grades, resolveWindow, timeToMinute, walls } from "./model.ts";
import type { Region, ShadingTime, Space } from "./model.ts";
import { createSpaceScene } from "./Space3DScene.ts";
import type { Level, Lit, SceneApi } from "./Space3DScene.ts";
import { dayOfYear, sunPosition } from "./sun3d.ts";
import type { SunpathPrediction } from "./spaceApi.ts";
import type { ShadingResult } from "./shading.ts";
import { predictionDay } from "./sunlight.ts";
import { cellsAt, compactDay, sampleIndex, sunAt } from "./space3dServer.ts";
import "./Space3DView.css";

const LEVELS: { id: Level; label: string; lead: string }[] = [
  { id: "basic", label: "기본 보기", lead: "저장된 크기와 위치 그대로예요. 가구를 누르면 크기와 위치가 나와요." },
  { id: "sun", label: "햇빛과 그림자", lead: "서버가 계산한 해 위치로 창을 통과한 햇빛과 그림자를 그려요. 주황색은 지금 직사광이 닿는 보호 영역이에요." },
  { id: "film", label: "필름 칸", lead: "서버의 차광 계획대로 10 cm 칸을 닫아요. A는 직사광이 닿으면 늘, B·C는 직사광이 상한을 넘을 때만 닫아요." },
];
const clock = (h: number) => `${String(Math.floor(h)).padStart(2, "0")}:${String(Math.floor((h % 1) * 60 + 1e-6)).padStart(2, "0")}`;
const compassWord = (az: number) => ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][Math.round(az / 45) % 8];
const pct = (v: number) => `${Math.round(v * 100)}%`;
const utcHours = (offset: string) => { const h = Number(offset.slice(0, 3)); return h + Math.sign(h || 1) * Number(offset.slice(4)) / 60; };
const dayPart = (h: number) => Math.min(18, Math.max(6, h));

type Props = {
  space: Space;
  hour: number;
  onHour: (hour: number) => void;
  date: string; // YYYY-MM-DD in the space's time zone, shared with the 2D prediction ("" without a site)
  selected: string | null;
  onSelect: (furnitureId: string) => void;
  shading: ShadingTime; // the user's blocking time, applied to the film exactly like the 2D film card
  // The same FR-01 and FR-02 results the 2D page shows; `planned` is false when the space cannot be planned yet.
  planned: boolean;
  sunpath?: SunpathPrediction;
  film?: ShadingResult;
};
const VIEW_KEY = "sunsync-plan-view";
const savedView = (): "2d" | "3d" => {
  try { return localStorage.getItem(VIEW_KEY) === "3d" ? "3d" : "2d"; } catch { return "2d"; }
};

/** Web-only 3D view of a saved space (issue #102): room, sunlight and film cells from the server's plan. */
export default function Space3DView({ space, hour, onHour, date, selected, onSelect, shading, planned, sunpath, film }: Props) {
  // The scene shows the current zone, like the 2D plan; server results stay indexed by space.regions.
  const zoneSpace = useMemo(() => ({
    ...space,
    furniture: space.furniture.filter(f => f.zone === space.zone),
    regions: space.regions.filter(r => r.zone === space.zone),
  }), [space]);
  const host = useRef<HTMLDivElement>(null);
  const needle = useRef<HTMLDivElement>(null);
  const api = useRef<SceneApi | null>(null);
  const [level, setLevel] = useState<Level>("basic");
  // The plan opens in the view this viewer used last (2D the first time); it is only a convenience.
  const [view, setView] = useState<"2d" | "3d">(savedView);
  const viewRef = useRef(view);
  const changeView = (next: "2d" | "3d") => {
    setView(next);
    viewRef.current = next;
    api.current?.setView(next);
    try { localStorage.setItem(VIEW_KEY, next); } catch { /* not remembered */ }
  };
  const [playing, setPlaying] = useState(false);
  const [lit, setLit] = useState<Lit | null>(null);
  const site = space.site ?? { latitude: 37.57, longitude: 126.98, timezone: "Asia/Seoul", northConfirmed: false };
  const [today] = useState(() => new Intl.DateTimeFormat("en-CA", { timeZone: site.timezone }).format(new Date()));
  date ||= today;
  const offset = predictionDay(site.timezone, new Date(`${date}T12:00:00Z`)).start.slice(19);
  const [, month, dom] = date.split("-").map(Number);
  const day = dayOfYear(month, dom);
  const at = Date.parse(`${date}T${clock(hour)}:00${offset}`);
  const win = resolveWindow(space);
  const serverDay = useMemo(() => (sunpath && film?.plan ? compactDay(sunpath, [film.plan], 0) : null), [sunpath, film]);

  useEffect(() => {
    const scene = createSpaceScene(host.current!, zoneSpace, {
      onSelect: id => id && onSelect(id),
      onAzimuth: deg => needle.current?.style.setProperty("--az", `${deg}deg`),
    }, viewRef.current);
    api.current = scene;
    return () => scene.dispose();
    // onSelect is a fresh function each render; the scene keeps the first one, which only forwards ids.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoneSpace]);
  useEffect(() => api.current?.setLevel(level), [level]);
  useEffect(() => api.current?.select(selected), [selected]);

  // Outside the user's blocking time (or with it off) the film stays open, as in the 2D film card.
  const from = timeToMinute(shading.start), to = timeToMinute(shading.end);
  const gated = (minute: number) => shading.enabled && from !== null && to !== null && minute >= from && minute < to;
  const gateOpen = gated(Math.floor(hour * 60));
  // Keyed on the space: resolveWindow returns a new object every render.
  const grid = useMemo(() => { const w = resolveWindow(space); return w ? filmGrid(w) : null; }, [space]);
  const moment = useMemo(() => {
    const d = serverDay;
    if (!d) return null;
    const cells = gateOpen ? cellsAt(d, at, space.regions.length) : { states: new Uint8Array(d.rows * d.cols), byRegion: new Array<number>(space.regions.length).fill(0) };
    return { sun: sunAt(d, at), ...cells, index: sampleIndex(d, at) };
  }, [serverDay, at, space.regions.length, gateOpen]);
  useEffect(() => {
    const d = serverDay;
    const closed = grid && !gateOpen ? { cells: new Uint8Array(grid.rows * grid.cols), rows: grid.rows, cols: grid.cols } : {};
    setLit(api.current!.setTime(day, hour, d && moment ? { sun: moment.sun, cells: moment.states, rows: d.rows, cols: d.cols } : closed));
  }, [day, hour, serverDay, moment, zoneSpace, gateOpen, grid]);
  // Per region and FR-01 sample between 06:00 and 18:00: sunlit share and whether the plan closes cells for it.
  const series = useMemo(() => {
    const d = serverDay;
    if (!d) return null;
    const midnight = Date.parse(`${date}T00:00:00${offset}`);
    const samples = d.times.map((t, k) => ({ t, k, m: (t - midnight) / 60000 })).filter(s => s.m >= 360 && s.m <= 1080);
    const closed = samples.map(s => (gated(s.m) ? cellsAt(d, s.t, space.regions.length).byRegion : []));
    return space.regions.map((_, i) => ({
      open: samples.map(s => d.exposure[i]?.share[s.k] ?? 0),
      film: closed.map(f => ((f[i] ?? 0) > 0 ? 1 : 0)),
    }));
    // gated depends only on the shading fields listed here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverDay, space.regions, shading.enabled, from, to, date, offset]);

  const hourRef = useRef(hour);
  useEffect(() => { hourRef.current = hour; }, [hour]);
  useEffect(() => {
    if (!playing) return;
    let frame = 0, last = performance.now();
    const tick = (now: number) => {
      const next = hourRef.current + ((now - last) / 1000) * 1.1;
      onHour(next >= 18 || next < 6 ? 6 : next);
      last = now;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, onHour]);

  const sunNow = sunPosition(day, hour, site.latitude, site.longitude, utcHours(offset));
  const info = LEVELS.find(l => l.id === level)!;
  const chosen = space.furniture.find(f => f.id === selected);
  const chosenRegion = space.regions.find(r => r.furnitureId === selected);
  // Row text: server numbers when they have arrived, otherwise the on-screen preview.
  const rowValue = (r: Region, i: number): [string, string] => {
    const d = serverDay, k = moment?.index ?? -1;
    if (d && moment && k >= 0) {
      const share = d.exposure[i]?.share[k] ?? 0;
      if (level === "sun") return share > 0 ? [`햇빛 ${pct(share)}`, "sun"] : ["닿지 않음", ""];
      if (moment.byRegion[i]) return [`칸 ${moment.byRegion[i]}개 닫음`, "film"];
      if (share === 0) return ["닿지 않음", ""];
      if (r.grade === "D") return [`햇빛 ${pct(share)} · 보호 안 함`, "sun"];
      if (!gateOpen) return [`햇빛 ${pct(share)} · ${shading.enabled ? "차단 시간 밖" : "차단 시간 꺼짐"}`, "sun"];
      // B/C cells stay open while the direct sun is under the grade's cap.
      return r.grade === "A" ? ["닫을 칸 없음", ""] : ["약한 햇빛이라 열어 둠", ""];
    }
    if (level === "film" && r.grade === "D") return ["보호 안 함", ""];
    if (level === "film" && !gateOpen) return [shading.enabled ? "차단 시간 밖" : "차단 시간 꺼짐", ""];
    const v = (level === "film" ? lit?.film : lit?.open)?.[r.id] ?? 0;
    return v > 0.005 ? [`햇빛 ${pct(v)}`, "sun"] : [level === "film" && (lit?.open[r.id] ?? 0) > 0.005 ? "막음" : "닿지 않음", ""];
  };
  const rowBars = (i: number) => {
    const s = series?.[i];
    if (!s) return null;
    if (level === "film") return s.film;
    const top = Math.max(...s.open, 0.001);
    return s.open.map(x => x / top);
  };
  // Only say something when the picture is not the server's yet.
  const source = serverDay ? null : !planned
    ? "북쪽 방향과 창문 위치를 확인하고 저장하면 실제 햇빛으로 보여 드려요. 지금은 미리보기예요."
    : film?.error
      ? `${film.error} 지금은 미리보기예요.`
      : "햇빛을 계산하는 중이에요. 잠시만 미리보기로 보여 드려요.";
  const time = dayPart(hour);

  return (
    <div className="s3">
      <header className="s3-head">
        <div className="s3-title">
          <p className="s3-meta">
            {space.width.toFixed(1)} × {space.depth.toFixed(1)} × {space.height.toFixed(1)} m
            {win && ` · ${walls[win.wall]} 창 ${win.width.toFixed(1)} × ${win.height.toFixed(1)} m`}
          </p>
        </div>
        <div className="s3-steps" role="tablist" aria-label="3D 보기 단계">
          {LEVELS.map((l, i) => (
            <button key={l.id} role="tab" aria-selected={level === l.id} className={level === l.id ? "on" : ""} onClick={() => setLevel(l.id)}>
              <span className="n">{String(i + 1).padStart(2, "0")}</span>{l.label}
            </button>
          ))}
        </div>
      </header>

      <div className="s3-body">
        <section className="s3-stage" aria-label="3D 공간">
          <div ref={host} className="s3-canvas" />
          <div className="s3-compass" aria-label="북쪽 방향">
            <div ref={needle} className="s3-needle"><span>N</span></div>
          </div>
          <div className="s3-tools">
            <div className="s3-seg" role="group" aria-label="보기 방식">
              {(["2d", "3d"] as const).map(v => (
                <button key={v} aria-pressed={view === v} className={view === v ? "on" : ""} onClick={() => changeView(v)}>{v.toUpperCase()}</button>
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
                <span>{`${month}월 ${dom}일 · `}{sunNow.altitude > 0 ? `해 높이 ${Math.round(sunNow.altitude)}° · ${compassWord(sunNow.azimuth)}쪽` : "해가 진 시간"}</span>
              </div>
              <input
                className="s3-range" type="range" min={6} max={18} step={1 / 12} value={time} aria-label="시간"
                style={{ ["--p" as string]: `${((time - 6) / 12) * 100}%` }}
                onChange={e => { setPlaying(false); onHour(Number(e.target.value)); }}
              />
            </div>
          )}
        </section>

        <aside className="s3-panel">
          <p className="s3-lead">{info.lead}</p>
          {level !== "basic" && source && <p className="s3-source" role="status">{source}</p>}
          {level === "film" && lit && (
            <div className="s3-stat">
              <div><strong>{lit.blocked}<small> / {lit.cells}칸</small></strong><span>지금 닫은 필름 칸</span></div>
              <div><strong>{pct(lit.blocked / Math.max(1, lit.cells))}</strong><span>창 면적 중</span></div>
            </div>
          )}
          <h2>보호 영역</h2>
          <ul className="s3-regions">
            {space.regions.map((r, i) => {
              if (r.zone !== space.zone) return null;
              const [text, tone] = rowValue(r, i), bars = level === "basic" ? null : rowBars(i);
              return (
                <li key={r.id}>
                  <button className={selected === r.furnitureId ? "on" : ""} onClick={() => r.furnitureId && onSelect(r.furnitureId)}>
                    <span className={`s3-grade g-${r.grade}`}>{r.grade}</span>
                    <span className="s3-rname">{r.name}<small>{grades[r.grade].split(" · ")[0]}</small></span>
                    <span className={`s3-rval ${level === "basic" ? "" : tone}`}>{level === "basic" ? `${r.face} · ${r.bottom}~${r.top} m` : text}</span>
                    {bars && (
                      <span className={`s3-strip ${level === "film" ? "film" : ""}`} aria-hidden="true" style={{ gridTemplateColumns: `repeat(${bars.length}, 1fr)` }}>
                        {bars.map((x, j) => <i key={j} style={{ opacity: 0.08 + x * 0.92 }} />)}
                        <b style={{ left: `${((time - 6) / 12) * 100}%` }} />
                      </span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
          {level !== "basic" && series && <p className="s3-axis"><span>6시</span><span>9</span><span>12</span><span>15</span><span>18시</span></p>}

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
            <span><i className="sw-film" />닫은 필름 칸</span>
          </div>
          <p className="s3-note">{view === "3d" ? "끌어서 돌리기 · 휠로 확대 · 오른쪽 버튼으로 이동" : "휠로 확대 · 끌어서 이동"}</p>
        </aside>
      </div>
    </div>
  );
}
