import { useEffect, useMemo, useRef, useState } from "react";
import { filmGrid, grades, resolveWindow, timeToMinute } from "./model.ts";
import type { Grade, Region, ShadingTime, Space } from "./model.ts";
import { createSpaceScene } from "./Space3DScene.ts";
import type { Level, Lit, SceneApi } from "./Space3DScene.ts";
import { dayOfYear, sunPosition } from "./sun3d.ts";
import { predictAdaptivePlan, predictSunpath, userMessage } from "./spaceApi.ts";
import { validateShadingPlan } from "./shading.ts";
import { predictionDay } from "./sunlight.ts";
import { cellsAt, compactDay, sampleIndex, sunAt } from "./space3dServer.ts";
import type { ServerDay } from "./space3dServer.ts";
import "./Space3DView.css";

const LEVELS: { id: Level; label: string; lead: string }[] = [
  { id: "basic", label: "기본 보기", lead: "저장된 크기와 위치대로 방, 창, 가구, 보호 영역을 세웠어요. 끌어서 돌려 보고, 가구를 눌러 확인하세요." },
  { id: "sun", label: "햇빛과 그림자", lead: "서버가 계산한 해 위치로 창을 통과한 햇빛과 그림자를 그려요. 주황색은 지금 직사광이 닿는 보호 영역이에요." },
  { id: "film", label: "필름 칸", lead: "서버가 5분마다 세운 계획대로 10 cm 칸을 닫아요. A는 직사광이 닿으면 늘, B·C는 직사광이 상한을 넘을 때만 닫고, 닫은 칸의 위·아래·양옆도 한 칸씩 함께 닫아요." },
];
// Same caps as the server's default grade policy (backend/app/ui_scene.py).
const CAPS: Partial<Record<Grade, number>> = { B: 30, C: 70 };
const clock = (h: number) => `${String(Math.floor(h)).padStart(2, "0")}:${String(Math.floor((h % 1) * 60 + 1e-6)).padStart(2, "0")}`;
const compassWord = (az: number) => ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][Math.round(az / 45) % 8];
const pct = (v: number) => `${Math.round(v * 100)}%`;
const utcHours = (offset: string) => { const h = Number(offset.slice(0, 3)); return h + Math.sign(h || 1) * Number(offset.slice(4)) / 60; };
type Server = { status: "ready" | "error"; message?: string; day?: ServerDay }; // absent while loading

// FR-01 for 06–18 every 15 minutes; FR-04 in two half days at 5 minutes (one call stays under the server's
// ray budget). One after another: run together they slow each other past the request time limit.
async function loadDay(sceneId: string, revision: number, date: string, offset: string, onStep: (text: string) => void): Promise<ServerDay> {
  const key = `s3-server:${sceneId}:${revision}:${date}`;
  try {
    const cached = localStorage.getItem(key);
    if (cached) return JSON.parse(cached) as ServerDay;
  } catch { /* storage may be blocked; compute again */ }
  const start = (h: string) => `${date}T${h}:00:00${offset}`;
  onStep("햇빛 경로 계산 중 (1/3)");
  const sunpath = await predictSunpath(sceneId, revision, start("06"), 720, 900);
  onStep("오전 필름 계획 계산 중 (2/3)");
  const morning = await predictAdaptivePlan(sceneId, start("06"), 360);
  onStep("오후 필름 계획 계산 중 (3/3)");
  const afternoon = await predictAdaptivePlan(sceneId, start("12"), 360);
  if (sunpath.scene_revision !== revision || morning.scene_revision !== revision || afternoon.scene_revision !== revision)
    throw new Error("server result is for another revision");
  const plans = [validateShadingPlan(morning.stabilized_plan, sceneId, revision, start("06")), validateShadingPlan(afternoon.stabilized_plan, sceneId, revision, start("12"))];
  const day = compactDay(sunpath, plans, (morning.metrics.flicker_reduction_pct + afternoon.metrics.flicker_reduction_pct) / 2);
  try { localStorage.setItem(key, JSON.stringify(day)); } catch { /* the view still works without the cache */ }
  return day;
}

type Props = {
  space: Space;
  hour: number;
  onHour: (hour: number) => void;
  date: string; // YYYY-MM-DD in the space's time zone, shared with the 2D prediction ("" without a site)
  selected: string | null;
  onSelect: (furnitureId: string) => void;
  shading: ShadingTime; // the user's blocking time, applied to the film exactly like the 2D film card
};

/** Web-only 3D view of a saved space (issue #102): room, sunlight and film cells from the server's plan. */
export default function Space3DView({ space, hour, onHour, date, selected, onSelect, shading }: Props) {
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
  const [playing, setPlaying] = useState(false);
  const [lit, setLit] = useState<Lit | null>(null);
  const [servers, setServers] = useState<Record<string, Server>>({});
  const [step, setStep] = useState("계산 준비 중");
  const site = space.site ?? { latitude: 37.57, longitude: 126.98, timezone: "Asia/Seoul", northConfirmed: false };
  const [today] = useState(() => new Intl.DateTimeFormat("en-CA", { timeZone: site.timezone }).format(new Date()));
  date ||= today;
  const offset = predictionDay(site.timezone, new Date(`${date}T12:00:00Z`)).start.slice(19);
  const [, month, dom] = date.split("-").map(Number);
  const day = dayOfYear(month, dom);
  const at = Date.parse(`${date}T${clock(hour)}:00${offset}`);
  // Server planning needs what FR-01 needs: a saved scene, confirmed north, a window and protected areas.
  const sceneId = space.serverSceneId, revision = space.serverRevision;
  const planReady = !!(sceneId && revision && space.site?.northConfirmed && resolveWindow(space) && space.regions.some(r => r.grade !== "D"));
  const serverKey = `${sceneId}:${revision}:${date}`;
  const server = planReady ? servers[serverKey] : undefined;

  useEffect(() => {
    const scene = createSpaceScene(host.current!, zoneSpace, {
      onSelect: id => id && onSelect(id),
      onAzimuth: deg => needle.current?.style.setProperty("--az", `${deg}deg`),
    });
    api.current = scene;
    return () => scene.dispose();
    // onSelect is a fresh function each render; the scene keeps the first one, which only forwards ids.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoneSpace]);
  useEffect(() => api.current?.setLevel(level), [level]);
  useEffect(() => api.current?.select(selected), [selected]);

  // Ask the server only once someone looks at the sunlight steps; the plain view needs nothing from it.
  const requested = useRef(new Set<string>());
  useEffect(() => {
    if (level === "basic" || !planReady || requested.current.has(serverKey)) return;
    requested.current.add(serverKey);
    loadDay(sceneId!, revision!, date, offset, setStep).then(
      d => setServers(s => ({ ...s, [serverKey]: { status: "ready", day: d } })),
      e => setServers(s => ({ ...s, [serverKey]: { status: "error", message: userMessage(e, "서버 계산을 불러오지 못했어요.") } })),
    );
  }, [level, planReady, serverKey, sceneId, revision, date, offset]);

  // Outside the user's blocking time (or with it off) the film stays open, as in the 2D film card.
  const minute = Math.floor(hour * 60), from = timeToMinute(shading.start), to = timeToMinute(shading.end);
  const gateOpen = shading.enabled && from !== null && to !== null && minute >= from && minute < to;
  const grid = useMemo(() => { const w = resolveWindow(space); return w ? filmGrid(w) : null; }, [space]);
  const moment = useMemo(() => {
    const d = server?.day;
    if (!d) return null;
    const cells = gateOpen ? cellsAt(d, at, space.regions.length) : { states: new Uint8Array(d.rows * d.cols), byRegion: new Array<number>(space.regions.length).fill(0) };
    return { sun: sunAt(d, at), ...cells, index: sampleIndex(d, at) };
  }, [server, at, space.regions.length, gateOpen]);
  useEffect(() => {
    const d = server?.day;
    const closed = grid && !gateOpen ? { cells: new Uint8Array(grid.rows * grid.cols), rows: grid.rows, cols: grid.cols } : {};
    setLit(api.current!.setTime(day, hour, d && moment ? { sun: moment.sun, cells: moment.states, rows: d.rows, cols: d.cols } : closed));
  }, [day, hour, server, moment, zoneSpace, gateOpen, grid]);
  // Per region and FR-01 sample: sunlit share (step 2) and whether the film plan closes cells for it (step 3).
  const series = useMemo(() => {
    const d = server?.day;
    if (!d) return null;
    const open = (t: number) => {
      const m = Math.floor((t - Date.parse(`${date}T00:00:00${offset}`)) / 60000);
      return shading.enabled && from !== null && to !== null && m >= from && m < to;
    };
    const film = d.times.map(t => (open(t) ? cellsAt(d, t, space.regions.length).byRegion : []));
    return space.regions.map((_, i) => ({ open: d.exposure[i]?.share ?? [], film: film.map(f => (f[i] > 0 ? 1 : 0)) }));
  }, [server, space.regions, shading.enabled, from, to, date, offset]);

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
  // Row text: server numbers when they have arrived, otherwise the on-screen preview.
  const rowValue = (r: Region, i: number): [string, string] => {
    const d = server?.day, k = moment?.index ?? -1;
    if (d && moment && k >= 0) {
      const share = d.exposure[i]?.share[k] ?? 0, maxW = d.exposure[i]?.maxW[k];
      const watts = maxW == null ? "세기 모름" : `${Math.round(maxW)} W/m²`;
      if (level === "sun") return share > 0 ? [`햇빛 ${pct(share)} · ${watts}`, "sun"] : ["닿지 않음", ""];
      if (moment.byRegion[i]) return [`칸 ${moment.byRegion[i]}개 닫음`, "film"];
      if (share === 0) return ["닿지 않음", ""];
      if (!gateOpen && r.grade !== "D") return [`햇빛 ${pct(share)} · ${shading.enabled ? "차단 시간 밖이라" : "차단 시간 설정이 꺼져"} 열어 둠`, "sun"];
      if (r.grade === "D") return [`햇빛 ${pct(share)} · 보호 안 함`, "sun"];
      return r.grade === "A" ? ["닫을 칸 없음", ""] : [`${watts} · 상한 ${CAPS[r.grade]} 이하라 열어 둠`, ""];
    }
    if (level === "film" && !gateOpen) return [shading.enabled ? "차단 시간 밖이라 열어 둠" : "차단 시간 설정이 꺼져 열어 둠", ""];
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
  const source = !planReady
    ? "서버 계산 조건(저장된 공간, 북쪽 확인, 창문, 보호 영역)이 모자라 화면 미리보기로 보여 드려요."
    : server?.status === "ready"
      ? `서버 계산 반영 · ${date} · 햇빛 15분, 필름 5분 간격 · B·C 상한 ${CAPS.B}·${CAPS.C} W/m²`
      : server?.status === "error"
        ? `${server.message} 지금은 화면 미리보기예요.`
        : `서버 계산: ${step}. 40초쯤 걸리고, 그동안은 화면 미리보기예요.`;

  return (
    <div className="s3v">
      <div className="s3-steps" role="tablist" aria-label="3D 보기 단계">
        {LEVELS.map((l, i) => (
          <button key={l.id} role="tab" aria-selected={level === l.id} className={level === l.id ? "on" : ""} onClick={() => setLevel(l.id)}>
            <span className="n">{String(i + 1).padStart(2, "0")}</span>{l.label}
          </button>
        ))}
      </div>
      <p className="s3-lead">{info.lead}</p>
      <section className="s3-stage" aria-label="3D 공간">
        <div ref={host} className="s3-canvas" />
        <div className="s3-compass" aria-label="북쪽 방향">
          <div ref={needle} className="s3-needle"><span>N</span></div>
        </div>
        <button className="s3-ghost s3-reset" onClick={() => api.current?.reset()}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8a5 5 0 1 0 1.6-3.7M3 2.5v2.8h2.8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
          처음 시점
        </button>
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
              className="s3-range" type="range" min={6} max={18} step={1 / 12} value={Math.min(18, Math.max(6, hour))} aria-label="시간"
              style={{ ["--p" as string]: `${((Math.min(18, Math.max(6, hour)) - 6) / 12) * 100}%` }}
              onChange={e => { setPlaying(false); onHour(Number(e.target.value)); }}
            />
          </div>
        )}
      </section>
      {level !== "basic" && <p className={`s3-source ${server?.status === "ready" ? "is-ready" : ""}`} role="status">{source}</p>}
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
                    <b style={{ left: `${((Math.min(18, Math.max(6, hour)) - 6) / 12) * 100}%` }} />
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      {level !== "basic" && series && <p className="s3-axis"><span>6시</span><span>9</span><span>12</span><span>15</span><span>18시</span></p>}
      {level === "film" && lit && <p className="s3-note">지금 닫은 칸 {lit.blocked} / {lit.cells}칸 ({pct(lit.blocked / Math.max(1, lit.cells))})</p>}
    </div>
  );
}
