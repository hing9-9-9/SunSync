import { useEffect, useMemo, useRef, useState } from "react";
import { blankSpace, furnitureTypes, grades } from "./model.ts";
import type { Furniture, Grade, Region, Space } from "./model.ts";
import { createSpaceScene } from "./Space3DScene.ts";
import type { DayProfile, Level, Lit, SceneApi } from "./Space3DScene.ts";
import { dayOfYear, sunPosition } from "./sun3d.ts";
import { getServerSpace, predictAdaptivePlan, predictSunpath, saveServerSpace, SpaceApiError, userMessage } from "./spaceApi.ts";
import { validateShadingPlan } from "./shading.ts";
import { cellsAt, compactDay, sampleIndex, sunAt } from "./space3dServer.ts";
import type { ServerDay } from "./space3dServer.ts";
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
    name: "3D 샘플 열람실",
    width: 8,
    depth: 6,
    height: 3,
    windowDimensions: { width: 5, height: 2, wall: "south", offset: 1.5, sill: 0.6 },
    furniture,
    regions: [region("r1", furniture[2], "A", 0, 1.6), region("r2", furniture[4], "B", 0, 1.0), { ...region("r3", furniture[3], "C", 0.7, 0.75), face: "상단" }, region("r4", furniture[5], "D", 0, 0.75)],
    site: { latitude: 37.57, longitude: 126.98, timezone: "Asia/Seoul", northConfirmed: true },
  };
}

const LEVELS: { id: Level; label: string; lead: string }[] = [
  { id: "basic", label: "기본 보기", lead: "저장된 크기와 위치대로 방, 창, 가구, 보호 영역을 세웠어요. 끌어서 돌려 보고, 가구를 눌러 확인하세요." },
  { id: "sun", label: "햇빛과 그림자", lead: "서버가 계산한 해 위치로 창을 통과한 햇빛과 그림자를 그려요. 주황색 테두리는 지금 직사광이 닿는 보호 영역이에요." },
  { id: "film", label: "필름 칸", lead: "서버가 5분마다 세운 계획대로 10 cm 칸을 닫아요. A는 직사광이 닿으면 늘, B·C는 직사광이 상한을 넘을 때만 닫고, 닫은 칸의 위·아래·양옆도 한 칸씩 함께 닫아요." },
];
const today = new Date();
const iso = (m: number, d: number) => `${today.getFullYear()}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
const DAYS = [
  { id: "today", label: `오늘 ${today.getMonth() + 1}/${today.getDate()}`, day: dayOfYear(today.getMonth() + 1, today.getDate()), date: iso(today.getMonth() + 1, today.getDate()) },
  { id: "summer", label: "하지", day: dayOfYear(6, 21), date: iso(6, 21) },
  { id: "winter", label: "동지", day: dayOfYear(12, 21), date: iso(12, 21) },
];
// Same caps as the server's default grade policy (backend/app/ui_scene.py).
const CAPS: Partial<Record<Grade, number>> = { B: 30, C: 70 };
type Server = { status: "ready" | "error"; message?: string; day?: ServerDay }; // absent while loading

// The sample room is saved once like any space, then reused while its shape is unchanged.
let sceneOnce: Promise<{ sceneId: string; revision: number }> | null = null;
function ensureScene(space: Space) {
  const w = (s: Space) => s.windowDimensions;
  const shape = (s: Space) => JSON.stringify([
    s.width, s.depth, s.height, [w(s)?.width, w(s)?.height, w(s)?.wall, w(s)?.offset, w(s)?.sill],
    s.furniture.map(f => [f.id, f.type, f.x, f.y, f.width, f.depth, f.height]),
    s.regions.map(r => [r.id, r.furnitureId, r.x, r.y, r.width, r.depth, r.face, r.bottom, r.top, r.grade]),
  ]);
  return (sceneOnce ??= (async () => {
    let saved = await getServerSpace(space.id).catch((e: unknown) => {
      if (e instanceof SpaceApiError && e.status === 404) return null;
      throw e;
    });
    if (!saved) saved = await saveServerSpace(space);
    else if (shape(saved) !== shape(space)) saved = await saveServerSpace({ ...space, serverRevision: saved.serverRevision });
    return { sceneId: saved.serverSceneId!, revision: saved.serverRevision! };
  })().catch(e => { sceneOnce = null; throw e; }));
}
// FR-01 for 06–18 every 15 minutes; FR-04 in two half days at 5 minutes (one call stays under the server's
// ray budget). One after another: run together they slow each other past the request time limit.
async function loadDay(space: Space, date: string, onStep: (text: string) => void): Promise<ServerDay> {
  const { sceneId, revision } = await ensureScene(space);
  const key = `s3-server:${sceneId}:${revision}:${date}`;
  try {
    const cached = localStorage.getItem(key);
    if (cached) return JSON.parse(cached) as ServerDay;
  } catch { /* storage may be blocked; compute again */ }
  const start = (h: string) => `${date}T${h}:00:00+09:00`;
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
  const { day, date } = DAYS.find(d => d.id === dayId)!;
  const [servers, setServers] = useState<Record<string, Server>>({});
  const [step, setStep] = useState("서버에 공간 저장 중");
  const server = servers[date];
  const at = Date.parse(`${date}T${clock(hour)}:00+09:00`);

  useEffect(() => {
    const scene = createSpaceScene(host.current!, space, {
      onSelect: setSelected,
      onAzimuth: deg => needle.current?.style.setProperty("--az", `${deg}deg`),
    });
    api.current = scene;
    return () => scene.dispose();
  }, [space]);
  useEffect(() => api.current?.setLevel(level), [level]);
  const requested = useRef(new Set<string>());
  useEffect(() => {
    if (requested.current.has(date)) return;
    requested.current.add(date);
    loadDay(space, date, setStep).then(
      d => setServers(s => ({ ...s, [date]: { status: "ready", day: d } })),
      e => setServers(s => ({ ...s, [date]: { status: "error", message: userMessage(e, "서버 계산을 불러오지 못했어요.") } })),
    );
  }, [date, space]);
  const moment = useMemo(() => {
    const d = server?.day;
    if (!d) return null;
    return { sun: sunAt(d, at), ...cellsAt(d, at, space.regions.length), index: sampleIndex(d, at) };
  }, [server, at, space]);
  useEffect(() => {
    const d = server?.day;
    setLit(api.current!.setTime(day, hour, d && moment ? { sun: moment.sun, cells: moment.states, rows: d.rows, cols: d.cols } : undefined));
  }, [day, hour, server, moment]);
  // Per region and FR-01 sample: sunlit share (step 2) and whether the film plan closes cells for it (step 3).
  const serverSeries = useMemo(() => {
    const d = server?.day;
    if (!d) return null;
    const film = d.times.map(t => cellsAt(d, t, space.regions.length).byRegion);
    return space.regions.map((_, i) => ({ open: d.exposure[i].share, film: film.map(f => (f[i] > 0 ? 1 : 0)) }));
  }, [server, space]);
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
  // Row text: server numbers when they have arrived, otherwise the on-screen preview.
  const rowValue = (r: Region, i: number): [string, string] => {
    const d = server?.day, k = moment?.index ?? -1;
    if (d && moment && k >= 0) {
      const share = d.exposure[i]?.share[k] ?? 0, maxW = d.exposure[i]?.maxW[k];
      const watts = maxW == null ? "세기 모름" : `${Math.round(maxW)} W/m²`;
      if (level === "sun") return share > 0 ? [`햇빛 ${pct(share)} · ${watts}`, "sun"] : ["닿지 않음", ""];
      if (moment.byRegion[i]) return [`칸 ${moment.byRegion[i]}개 닫음`, "film"];
      if (share === 0) return ["닿지 않음", ""];
      if (r.grade === "D") return [`햇빛 ${pct(share)} · 보호 안 함`, "sun"];
      return r.grade === "A" ? ["닫을 칸 없음", ""] : [`${watts} · 상한 ${CAPS[r.grade]} 이하라 열어 둠`, ""];
    }
    const v = (level === "film" ? lit?.film : lit?.open)?.[r.id] ?? 0;
    return v > 0.005 ? [`햇빛 ${pct(v)}`, "sun"] : [level === "film" && (lit?.open[r.id] ?? 0) > 0.005 ? "막음" : "닿지 않음", ""];
  };
  const rowSeries = (r: Region, i: number) => {
    const s = serverSeries?.[i];
    if (!s) return profile && (level === "film" ? profile.film : profile.open)[r.id];
    if (level === "film") return s.film;
    const top = Math.max(...s.open, 0.001);
    return s.open.map(x => x / top);
  };

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
          {level !== "basic" && (
            <p className={`s3-source ${server?.status ?? "loading"}`} role="status">
              {server?.status === "ready"
                ? `서버 계산 반영 · 햇빛 FR-01(15분) · 필름 FR-02·04(5분, 가장자리 여유 켬) · B·C 상한 ${CAPS.B}·${CAPS.C} W/m²`
                : server?.status === "error"
                  ? `${server.message} 지금은 화면 미리보기예요.`
                  : `서버 계산: ${step}. 40초쯤 걸리고, 그동안은 화면 미리보기예요.`}
            </p>
          )}
          {level === "film" && lit && (
            <div className="s3-stat">
              <div><strong>{lit.blocked}</strong><span>막은 칸</span></div>
              <div><strong>{lit.cells - lit.blocked}</strong><span>열어 둔 칸</span></div>
              <div><strong>{pct(lit.blocked / Math.max(1, lit.cells))}</strong><span>창 면적</span></div>
            </div>
          )}
          <h2>보호 영역</h2>
          <ul className="s3-regions">
            {space.regions.map((r, i) => {
              const [text, tone] = rowValue(r, i), bars = rowSeries(r, i);
              return (
                <li key={r.id}>
                  <button className={selected === r.furnitureId ? "on" : ""} onClick={() => pick(selected === r.furnitureId ? null : r.furnitureId)}>
                    <span className={`s3-grade g-${r.grade}`}>{r.grade}</span>
                    <span className="s3-rname">{r.name}<small>{grades[r.grade].split(" · ")[0]}</small></span>
                    {level === "basic"
                      ? <span className="s3-rval">{(r.top - r.bottom).toFixed(2)} m 높이</span>
                      : <span className={`s3-rval ${tone}`}>{text}</span>}
                    {level !== "basic" && (
                      <span className={`s3-strip ${level === "film" && serverSeries ? "film" : ""}`} aria-hidden="true" style={{ gridTemplateColumns: `repeat(${bars?.length ?? 49}, 1fr)` }}>
                        {bars?.map((x, j) => <i key={j} style={{ opacity: 0.08 + x * 0.92 }} />)}
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
            <span><i className="sw-film" />닫은 필름 칸</span>
          </div>
          <p className="s3-note">마우스 왼쪽으로 돌리기 · 휠로 확대 · 오른쪽으로 이동. 샘플 데이터이고, 해 위치는 서울 기준 계산값이에요.</p>
        </aside>
      </main>
    </div>
  );
}
