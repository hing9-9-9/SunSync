import BurstCamera from "./BurstCamera";
import type { CapturePhase } from "./BurstCamera";
import { useEffect, useMemo, useRef, useState } from "react";
import { analyseImage, photoIssue } from "./captureCheck";
import { pointFailure } from "./captureGuide";
import type { PhotoIssue } from "./captureCheck";
import { shapes, validPolygon, nextCapturePoint, photosPerPoint } from "./capturePlan";
import type { Shape, CapturePoint } from "./capturePlan";
import { fromSceneFurnitureType, furnitureTypes } from "./model";
import type { Space } from "./model";
import VisionPreview from "./VisionPreview";
import VisionCamera from "./VisionCamera";
import type { CapturedFurniture, FurnitureType } from "./model";
import { browserCaptureEnabled } from "./captureEnvironment";
import { measurementBoard, checkMeasurement, selectFrames, detectFrame, captureSelection, resumeCaptureMarker, cancelStoredCapture, startCaptureMarker, endCaptureMarker, prepare, savedCamera, estimateVisionFurniture } from "./measureApi";
import FilmModulePicker from "./FilmModulePicker";
import type { CheckedMeasurement, Camera, Frame, CaptureMarker, FilmTargetWindow, VisionEstimatedItem } from "./measureApi";

type Photo = { file: File; url: string; issue: PhotoIssue | null; metrics: { brightness: number; sharpness: number }; frame?: Frame };

// MAR3.2: stand at each of the four corners of one piece of furniture and photograph the window marker.
type SpatialCaptureProps = { sceneId?: string; markerSizeOverride?: { sessionId: string; sideM: number }; count: number; onFinish: (items: CapturedFurniture[]) => void; onMarkerChange?: (session: CaptureMarker | null) => void; onVisionRegistered?: (addedCount: number) => Promise<void> | void; space?: Space };

export default function SpatialCapture(props: SpatialCaptureProps) {
  if (!browserCaptureEnabled) return <section role="status">
    <h3>촬영은 SunSync 앱에서 진행해 주세요</h3>
    <p>앱에서 같은 공간을 선택해 촬영하면 측정 결과를 웹에서 확인할 수 있어요.</p>
  </section>;
  if (!props.sceneId) return <p role="alert">공간을 먼저 서버에 저장해 주세요.</p>;
  return <SessionSpatialCapture key={props.sceneId} {...props} />;
}

function SessionSpatialCapture(props: SpatialCaptureProps) {
  const [session, setSession] = useState<CaptureMarker | null>(null);
  const { onMarkerChange } = props;
  useEffect(() => {
    onMarkerChange?.(session);
    return () => onMarkerChange?.(null);
  }, [session, onMarkerChange]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [retry, setRetry] = useState(0);
  const [windows,setWindows]=useState<FilmTargetWindow[]>([]);
  const [windowId,setWindowId]=useState("");
  const [savedSelection,setSavedSelection]=useState(false);
  const [row,setRow]=useState(0), [col,setCol]=useState(0);
  useEffect(() => {
    let live = true;
    if (!props.sceneId) return;
    void Promise.all([resumeCaptureMarker(props.sceneId),captureSelection(props.sceneId)]).then(([value,targets]) => {
      if (live) {
        const selected = targets.selected_target;
        setWindows(targets.windows);setSavedSelection(!!selected);
        if (value && selected && (value.window_id !== selected.window_id ||
            value.film_module?.row !== selected.module_row || value.film_module?.col !== selected.module_col)) {
          setError("선택한 필름과 진행 중인 촬영이 달라요. 이전 촬영을 끝낸 뒤 다시 시작해 주세요.");
        } else setSession(value);
        setWindowId(selected?.window_id ?? targets.windows[0]?.window_id ?? "");
        setRow(selected?.module_row ?? 0);setCol(selected?.module_col ?? 0);
      }
    })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : "촬영 마커를 준비하지 못했어요."); })
      .finally(() => { if (live) setBusy(false); });
    // A remount must not end a server session or rotate its marker.
    return () => { live = false; };
  }, [props.sceneId, retry]);
  async function begin() {
    if (!props.sceneId || busy || !windowId) return;
    setBusy(true);setError("");
    try {setSession(await startCaptureMarker(props.sceneId,{window_id:windowId,module_row:row,module_col:col}));}
    catch(e) {setError(e instanceof Error ? e.message : "필름을 선택하지 못했어요.");}
    finally {setBusy(false);}
  }
  async function cancel() {
    if (!session || busy) return;
    setBusy(true); setError("");
    try { await endCaptureMarker(session); setSession(null); }
    catch (e) { setError(e instanceof Error ? e.message : "촬영을 종료하지 못했어요."); }
    finally { setBusy(false); }
  }
  function endPrevious() {
    if (!props.sceneId) return;
    setBusy(true); setError("");
    void cancelStoredCapture(props.sceneId).then(() => setRetry(n => n + 1))
      .catch(() => { setError("이전 촬영을 끝내지 못했어요. 잠시 뒤 다시 시도해 주세요."); setBusy(false); });
  }
  if (busy) return <p role="status">촬영 마커를 준비하고 있어요…</p>;
  if (!session) {
    const win=windows.find(w=>w.window_id===windowId);
    return <section className="film-select">
      <h3>{savedSelection ? "선택한 필름으로 촬영" : "마커를 띄울 필름 선택"}</h3>
      {!windows.length && <p className="muted small">촬영할 필름이 없어요. 창문 크기와 위치를 먼저 저장해 주세요.</p>}
      {!savedSelection && windows.length > 1 && <label className="field"><span>창문</span><select value={windowId} onChange={e=>{setWindowId(e.target.value);setRow(0);setCol(0);}}>
        {windows.map((w,i)=><option key={w.window_id} value={w.window_id}>창 {i+1}</option>)}
      </select></label>}
      {win && <>
        {!savedSelection && <FilmModulePicker rows={win.rows} cols={win.cols} topRemainder={win.top_remainder_m} rightRemainder={win.right_remainder_m} row={row} col={col} onChange={(r,c)=>{setRow(r);setCol(c);}} />}
        {savedSelection && <p>창문 입력 단계에서 선택한 필름 정보를 가져왔어요.</p>}
        <p className="film-select-choice" role="status">아래에서 {row+1}번째 · 왼쪽에서 {col+1}번째 필름</p>
      </>}
      {error && <div className="film-error" role="alert">
        <p>{error}</p>
        {/다른 촬영이 진행 중|진행 중인 촬영이 달라요|촬영 마커가 바뀌었|필름이 변경됐|촬영을 시작하지 못했/.test(error) && <button type="button" className="secondary" onClick={endPrevious}>이전 촬영 끝내기</button>}
      </div>}
      <div className="capture-action"><button type="button" className="primary" disabled={!win} onClick={()=>void begin()}>촬영 시작</button></div>
    </section>;
  }
  const testMarkerSideM = props.markerSizeOverride?.sessionId === session.session_id
    ? props.markerSizeOverride.sideM : undefined;
  return <section className="capture-session">
    {error && <p className="error" role="alert">{error}</p>}
    <TestSpatialCapture key={`${session.session_id}:${testMarkerSideM ?? ""}`} {...props} session={session} testMarkerSideM={testMarkerSideM} />
    <div className="capture-end">
      <button type="button" className="secondary" onClick={() => void cancel()}>촬영 취소</button>
    </div>
  </section>;
}

function TestSpatialCapture({ count, onFinish, session, testMarkerSideM, onVisionRegistered, space }: {
  count: number; onFinish: (items: CapturedFurniture[]) => void; session: CaptureMarker; testMarkerSideM?: number; onVisionRegistered?: (addedCount: number) => Promise<void> | void; space?: Space;
}) {
  const board = useMemo(() => measurementBoard(session, testMarkerSideM), [session, testMarkerSideM]);
  const [items, setItems] = useState<CapturedFurniture[]>([]);
  const [captureCamera,setCaptureCamera]=useState<Camera|null>(null);
  const [actionTarget,setActionTarget]=useState<HTMLDivElement|null>(null);
  const [rawFrames,setRawFrames]=useState<Record<number,Frame[]>>({});
  const version=useRef(0);
  const [name, setName] = useState(`가구 ${count + 1}`);
  const [furnitureType, setFurnitureType] = useState<FurnitureType | undefined>();
  const [shape, setShape] = useState<Shape>('rectangle');
  const [custom, setCustom] = useState<CapturePoint[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const visionInput = useRef<HTMLInputElement>(null);
  const [visionBusy, setVisionBusy] = useState(false);
  const [visionError, setVisionError] = useState("");
  const [visionItems, setVisionItems] = useState<VisionEstimatedItem[]>([]);
  const [pickedVision, setPickedVision] = useState<number | null>(null);
  const [visionCamera, setVisionCamera] = useState(false);
  const [lastVisionImage, setLastVisionImage] = useState<{ width: number; height: number; base64: string } | null>(null);
  const [activeGuidance, setActiveGuidance] = useState("");
  const points = shape === 'polygon' ? custom : shapes[shape].points;
  const [photos, setPhotos] = useState<(Photo | null)[][]>([]);
  const [capturePhase, setCapturePhase] = useState<CapturePhase>("idle");
  const checking = capturePhase !== "idle";
  const [error, setError] = useState("");
  // Points that must be retaken, with the reason; only those points are cleared and shot again.
  const [problems, setProblems] = useState<Record<number,string>>({});
  const [verifying, setVerifying] = useState(false);
  const [checkedKey, setCheckedKey] = useState("");
  const [checkedMeasurement, setCheckedMeasurement] = useState<CheckedMeasurement>();
  // The check itself failed (network/server); wait for the user instead of retrying in a loop or unlocking the finish buttons.
  const [verifyFailed, setVerifyFailed] = useState(false);
  const [surfaceProblem, setSurfaceProblem] = useState("");
  useEffect(()=>()=>{version.current++;},[]);
  const latest = useRef(photos);
  useEffect(() => { latest.current = photos; }, [photos]);
  useEffect(() => () => latest.current.flat().forEach(p => p && URL.revokeObjectURL(p.url)), []);
  const pendingPoint = nextCapturePoint(points.map((_,i)=>(photos[i]??[]).map(p=>!!p&&!p.issue)));
  const corner = pendingPoint ?? points.length - 1;
  const complete = confirmed && pendingPoint === null;
  function reset() { version.current++;setVerifying(false);setVerifyFailed(false);setProblems({});setCheckedKey("");setCheckedMeasurement(undefined);setSurfaceProblem("");setRawFrames({}); photos.flat().forEach(p=>p&&URL.revokeObjectURL(p.url)); setPhotos([]); setError(''); }

  async function takeBatch(files:File[],profile:Camera,ticket:number) {
    if(pendingPoint===null||!confirmed||ticket!==version.current)return;
    const target=pendingPoint;
    if(JSON.stringify(profile)!==JSON.stringify(captureCamera)&&photos.some(row=>row.some(Boolean))) {
      reset();setCaptureCamera(profile);setError("카메라 설정이 바뀌어 이전 사진을 비웠어요. P1부터 다시 촬영해 주세요.");return;
    }
    setCaptureCamera(profile);setError("");
    const candidates:Photo[]=[];
    try {
      for(const file of files.slice(0,20)) {
        if(ticket!==version.current)return;
        const metrics=await analyseImage(file);
        if(photoIssue({...metrics,marker:"visible"}))continue;
        const frame=await detectFrame(file,board,session,new Date(file.lastModified).toISOString());
        if(frame.width!==profile.width||frame.height!==profile.height)throw new Error("카메라 준비 때와 영상 크기가 달라요. 다시 준비해 주세요.");
        candidates.push({file,url:"",frame,metrics,issue:frame.detections.length===1?null:"missing"});
      }
      if(!candidates.length)throw new Error("사용할 수 있는 사진이 없어요. 밝기와 흔들림을 확인해 주세요.");
      const result=await selectFrames(candidates.map(p=>p.frame!),profile,board,`P${target+1}`);
      if(ticket!==version.current)return;
      if(result.status!=="ok"){const why=pointFailure(`P${target+1}`,result);setProblems(all=>({...all,[target]:why}));throw new Error(why);}
      setProblems(all=>{const next={...all};delete next[target];return next;});
      
      const ids=new Set(result.selected_capture_ids);
      const selected=candidates.filter(p=>ids.has(p.frame!.capture_id)).map(p=>({...p,url:URL.createObjectURL(p.file)}));
      photos[target]?.forEach(p=>p&&URL.revokeObjectURL(p.url));
      setPhotos(all=>points.map((_,i)=>i===target?selected:all[i]??[]));
      setRawFrames(all=>({...all,[target]:candidates.map(p=>p.frame!)}));
    }catch(e){if(ticket===version.current)setError(e instanceof Error?e.message:String(e));}
  }
  // Once every point is shot, check them together (the server judges the whole set) so a bad point
  // is found here, while only that point needs retaking, not after the plan is made.
  const photoKey = points.map((_,i)=>(photos[i]??[]).map(p=>p?.frame?.capture_id).join(",")).join("|");
  useEffect(() => {
    if (!complete || !captureCamera || verifying || verifyFailed || checkedKey === photoKey) return;
    const ticket = version.current, key = photoKey;
    setVerifying(true); setError(""); setSurfaceProblem("");
    checkMeasurement(session.scene_id, captureCamera, { session, testMarkerSideM, type: furnitureType, shape, vertices: points, points: points.map((_,i)=>({ id:`P${i+1}`, frames:rawFrames[i] ?? (photos[i]??[]).map(p=>p!.frame!) })) })
      .then(({ problems: found, checked }) => {
        if (ticket !== version.current) return;
        const retry = found.find(f => f.retryable || (f.point && !points.some((_, i) => f.point === `P${i+1}`)));
        if (retry) {
          setError(retry.message);
          setVerifyFailed(true);
          return;
        }
        setCheckedMeasurement(checked);
        const failed = found.filter(f => f.point);
        if (failed.length) {
          const bad = new Set(failed.map(f => Number(f.point!.slice(1)) - 1));
          bad.forEach(i => photos[i]?.forEach(p => p && URL.revokeObjectURL(p.url)));
          setPhotos(all => points.map((_,i) => bad.has(i) ? [] : all[i] ?? []));
          setRawFrames(all => {
            const remaining = { ...all };
            bad.forEach(i => delete remaining[i]);
            return remaining;
          });
          setProblems(all => ({ ...all, ...Object.fromEntries(failed.map(f => [Number(f.point!.slice(1)) - 1, f.message])) }));
          setError(`${[...bad].map(i=>`P${i+1}`).join(", ")} 지점만 다시 찍어 주세요. 나머지 지점 사진은 그대로 남아 있어요.`);
        } else if (found.length) setSurfaceProblem(found[0].message);
        setCheckedKey(key);
      })
      .catch(e => { if (ticket === version.current) { setError(e instanceof Error ? e.message : String(e)); setVerifyFailed(true); } })
      .finally(() => { if (ticket === version.current) setVerifying(false); });
  }, [complete, captureCamera, verifying, verifyFailed, checkedKey, photoKey]);
  function nextFurniture() {
    const captured: CapturedFurniture[] = [...items, { name: name.trim() || `가구 ${count + items.length + 1}`, type: furnitureType ?? "desk", capture: {session, testMarkerSideM, camera:captureCamera!, checkedMeasurement, shape, points: points.map((p,i)=>({id:`P${i+1}`,position:p,photos:photos[i].map(p=>p!.file),frames:rawFrames[i]??(photos[i].every(p=>p?.frame)?photos[i].map(p=>p!.frame!):undefined)}))} }];
    setItems(captured);
    setName(`가구 ${count + captured.length + 1}`);
    setFurnitureType(undefined);
    reset();
    setConfirmed(false);
    return captured;
  }

  function retakePoint(i: number) {
    version.current++;setCheckedMeasurement(undefined);setCheckedKey("");setVerifying(false);setVerifyFailed(false);photos[i]?.forEach(p=>p&&URL.revokeObjectURL(p.url));
    setRawFrames(all=>{const next={...all};delete next[i];return next;});setProblems(all=>{const next={...all};delete next[i];return next;});setSurfaceProblem("");
    setPhotos(all=>all.map((row,j)=>j===i?[]:row));setError("");
  }

  async function scanRoomWithVision(file: File) {
    if (visionBusy || checking) return;
    setVisionBusy(true);
    setVisionError("");
    try {
      const prepared = await prepare(file);
      const result = await estimateVisionFurniture(session.scene_id, prepared, session, {
        camera: captureCamera ?? savedCamera(),
        applyToScene: false,
        testMarkerSideM,
      });
      setLastVisionImage(prepared);
      setVisionItems(result.estimated_items);
      setPickedVision(null);
    } catch (e) {
      setVisionItems([]);
      setVisionError(e instanceof Error ? e.message : String(e));
    } finally {
      setVisionBusy(false);
    }
  }

  function startGuidedCaptureFromEstimate(item: VisionEstimatedItem, index: number) {
    const kind = fromSceneFurnitureType(item.occluder.type);
    const koreanLabel = furnitureTypes[kind] || "가구";
    reset();
    setName(`${koreanLabel} ${count + items.length + index + 1}`);
    setFurnitureType(kind);
    const hintShape = item.recommended_capture_shape;
    if (hintShape && hintShape in shapes) setShape(hintShape);
    setActiveGuidance("가구 네 모서리(P1~P4)에서 창문 마커를 촬영해 주세요.");
    setConfirmed(true);
  }

  async function applyVisionDraftToScene() {
    if (!lastVisionImage || visionBusy || checking) return;
    setVisionBusy(true);
    setVisionError("");
    try {
      const result = await estimateVisionFurniture(session.scene_id, lastVisionImage, session, {
        camera: captureCamera ?? savedCamera(),
        applyToScene: true,
        testMarkerSideM,
      });
      try { await endCaptureMarker(session); } catch { /* Best-effort session cleanup */ }
      await onVisionRegistered?.(result.estimated_items.length);
    } catch (e) {
      setVisionError(e instanceof Error ? e.message : String(e));
    } finally {
      setVisionBusy(false);
    }
  }

  return <div className="spatial-capture corner-capture">
    {!confirmed && <section className="vision-assist" aria-label="사진으로 가구 찾기">
      <div className="vision-assist-head">
        <h3>사진으로 가구 찾기</h3>
        <p className="muted small">휴대폰을 가로로 돌려 QR 마커와 가구가 같이 보이게 한 장 찍어 주세요.</p>
      </div>
      <button type="button" className="primary vision-shoot" aria-label={visionBusy ? undefined : "사진으로 촬영 시작"} disabled={visionBusy || checking} onClick={() => typeof navigator.mediaDevices?.getUserMedia === "function" ? setVisionCamera(true) : visionInput.current?.click()}>
        {visionBusy ? "사진 분석 중…" : "촬영 시작"}
      </button>
      <input
        ref={visionInput}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        disabled={visionBusy || checking}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void scanRoomWithVision(file);
        }}
      />
      {visionError && <p role="alert" className="error">{visionError}</p>}
      {visionItems.length > 0 && <div className="vision-results">
        <p className="muted small">가구 {visionItems.length}개를 찾았어요.</p>
        {space && space.width > 0 && space.depth > 0 && <VisionPreview space={space} items={visionItems} selected={pickedVision} onSelect={(i) => setPickedVision((cur) => cur === i ? null : i)} />}
        <ul className="vision-item-list">
          {visionItems.map((item, idx) => {
            const kind = fromSceneFurnitureType(item.occluder.type);
            const [w, d, h] = item.occluder.dimensions_m;
            return <li key={item.occluder.occluder_id} className="vision-item" data-selected={pickedVision === idx ? "" : undefined}>
              <button type="button" className="vision-item-top vision-item-pick" aria-pressed={pickedVision === idx} onClick={() => setPickedVision((cur) => cur === idx ? null : idx)}>
                <strong>{furnitureTypes[kind]} {idx + 1}</strong>
                <span className="muted small">신뢰도 {Math.round(item.confidence * 100)}%</span>
              </button>
              {item.estimation_quality === "low_needs_aruco" && <span className="vision-badge">정확도 낮음 · 마커 촬영 필요</span>}
              <dl className="vision-facts">
                <div><dt>크기</dt><dd>{w.toFixed(2)}×{d.toFixed(2)}×{h.toFixed(2)}m</dd></div>
                <div><dt>창문과의 거리</dt><dd>{item.calibrated_distance_m.toFixed(1)}m</dd></div>
              </dl>
              <button
                type="button"
                className="secondary"
                disabled={visionBusy || checking}
                onClick={() => startGuidedCaptureFromEstimate(item, idx)}
              >
                마커로 정밀 촬영
              </button>
            </li>;
          })}
        </ul>
        {onVisionRegistered && <button
          type="button"
          className="primary"
          disabled={visionBusy || checking}
          onClick={() => void applyVisionDraftToScene()}
        >
          {visionBusy ? "배치 중…" : "이대로 도면에 배치"}
        </button>}
      </div>}
    </section>}
    {visionCamera && <VisionCamera session={session} board={board} onClose={() => setVisionCamera(false)} onPhoto={(file) => { setVisionCamera(false); void scanRoomWithVision(file); }} />}
    {(() => {
      const plan = (
    <section className={`shape-plan${confirmed?" compact":""}`} aria-label="형태별 촬영 계획">
      <svg viewBox={confirmed&&points.length?tightBox(points):"0 0 100 100"} role="img" aria-label={`${shapes[shape].label} 촬영 순서`} onClick={e=>{if(shape!=='polygon'||confirmed)return; const r=e.currentTarget.getBoundingClientRect();setCustom(p=>[...p,{x:Math.max(8,Math.min(92,(e.clientX-r.left)/r.width*100)),y:Math.max(8,Math.min(92,(e.clientY-r.top)/r.height*100))}]);}}>
        {shape==='circle'?<circle cx="50" cy="50" r="35" className="shape-outline"/>:shape==='ellipse'?<ellipse cx="50" cy="50" rx="40" ry="25" className="shape-outline"/>:<polygon points={points.map(p=>`${p.x},${p.y}`).join(' ')} className="shape-outline"/>}
        {points.map((p,i)=>{
          // The label sits outside the outline: on the outward side of the corner (reversed at an inward corner).
          const n=points.length, prev=points[(i+n-1)%n], next=points[(i+1)%n];
          const unit=(x:number,y:number)=>{const l=Math.hypot(x,y)||1;return {x:x/l,y:y/l};};
          const a=unit(p.x-prev.x,p.y-prev.y), c=unit(p.x-next.x,p.y-next.y);
          const area=points.reduce((t,q,j)=>{const r=points[(j+1)%n];return t+q.x*r.y-r.x*q.y;},0);
          const turn=(p.x-prev.x)*(next.y-p.y)-(p.y-prev.y)*(next.x-p.x);
          const mx=points.reduce((t,q)=>t+q.x,0)/n, my=points.reduce((t,q)=>t+q.y,0)/n;
          const bis=n>=3&&area&&Math.hypot(a.x+c.x,a.y+c.y)>.2?unit((a.x+c.x)*(turn*area>0?1:-1),(a.y+c.y)*(turn*area>0?1:-1)):unit(p.x-mx,p.y-my);
          const anchor=bis.x>.85?'start':bis.x<-.85?'end':'middle', lx=Math.max(anchor==='middle'?9:2,Math.min(anchor==='middle'?91:98,p.x+bis.x*(anchor==='middle'?11:7))), ly=Math.max(9,Math.min(91,p.y+bis.y*(anchor==='middle'?11:0)))+3;
          return <g key={i}><circle cx={p.x} cy={p.y} r="3" className={confirmed&&!complete&&i===corner?'point-current':'point'}/><text x={lx} y={ly} textAnchor={anchor}>P{i+1}</text></g>;
        })}
      </svg>
      <div className="shape-plan-body">
      <p>{shape==='polygon'&&!confirmed?'꼭짓점을 순서대로 눌러 모양 구성':confirmed?`${shapes[shape].label} · ${points.length}개 지점`:`${points.length}개 지점 · 지점마다 촬영 한 번`}</p>
      {!confirmed ? <div className="actions">
        {shape==='polygon'&&<button type="button" className="secondary" disabled={!custom.length} onClick={()=>setCustom(p=>p.slice(0,-1))}>마지막 점 취소</button>}
        <button type="button" className="primary" aria-label="마커로 촬영 시작" disabled={shape==='polygon'&&!validPolygon(custom)} onClick={()=>{setConfirmed(true);setError('');}}>촬영 시작</button>
      </div>:<button type="button" className="text-button" disabled={checking} onClick={()=>{reset();setConfirmed(false);setActiveGuidance("");setFurnitureType(undefined);}}>형태 다시 선택</button>}
      </div>
    </section>
      );
      return confirmed ? plan : <div className="shape-card">
      <div className="shape-field">
      <div className="shape-field-head">
        <h3 className="shape-field-title">마커로 정밀 촬영</h3>
        <p>가구별 위치로 가서 찍어 주세요.</p>
      </div>
      <label className="field"><span>상판 형태</span>
        <select value={shape} disabled={confirmed||checking} onChange={e=>{reset();setShape(e.target.value as Shape);setCustom([]);}}>
          {Object.entries(shapes).map(([value,s])=><option key={value} value={value}>{s.label}</option>)}
        </select>
      </label>
    </div>
      {plan}
      </div>;
    })()}
    {confirmed && activeGuidance && <p role="status" className="capture-hint ok vision-guidance-banner">{activeGuidance}</p>}
    {confirmed && <>
    <ol className="corner-steps" aria-label="지점 촬영 상태">
      {points.map((_,i) => {
        const n=(photos[i]??[]).filter(p=>p&&!p.issue).length;
        const done=(photos[i]??[]).some(Boolean);
        const label=problems[i]?"다시 촬영":n>=photosPerPoint?"완료":"대기";
        return <li key={i} className={problems[i]?'retake':n>=photosPerPoint?'done':i===corner?'current':'waiting'} aria-current={i===corner?'step':undefined}>
          {done ? <button type="button" className="corner-retake" disabled={checking} aria-label={`P${i+1} 다시 촬영`} onClick={()=>retakePoint(i)}>
            <span className="corner-number">P{i+1}</span><small>{label}</small>
          </button> : <><span className="corner-number">P{i+1}</span><small>{label}</small></>}
        </li>;
      })}
    </ol>
    </>}
    <figure className="capture-viewfinder" style={{display: confirmed && !(complete && !verifying) ? undefined : "none"}}>
      <BurstCamera session={session} board={board} active={confirmed&&!complete} pointLabel={`P${corner+1}`} actionTarget={actionTarget}
        disabled={checking} getTicket={()=>version.current} phase={capturePhase} onPhaseChange={setCapturePhase} onReady={setCaptureCamera} onBatch={takeBatch}/>
      {(capturePhase !== "idle" || verifying || complete || !captureCamera) && <figcaption role="status">{capturePhase === "analyzing" || verifying ? "촬영 완료! 휴대폰을 내려도 돼요. 사진 분석 중…" : capturePhase === "capturing" ? "촬영 중… 휴대폰 위치를 유지해 주세요." : complete ? "모든 지점 촬영 완료" : "카메라 준비 · 마커가 보이도록 12장 촬영해 주세요"}</figcaption>}
    </figure>
    {confirmed && <>
    {Object.entries(problems).sort(([a],[b])=>Number(a)-Number(b)).map(([i,why])=><p key={i} className="point-problem">{why}</p>)}
    {surfaceProblem && <p role="alert" className="error">{surfaceProblem}</p>}
    {photos.some(row=>(row??[]).some(Boolean)) && !checking && <p role="status" className="muted small corner-hint">{complete ? "모든 지점 촬영 완료 · 지점을 누르면 다시 찍어요." : "완료한 지점을 누르면 다시 찍어요."}</p>}
    {error && !Object.values(problems).includes(error) && <p role="alert" className="error">{error}</p>}
    {complete && verifyFailed && <button type="button" className="secondary" onClick={()=>setVerifyFailed(false)}>다시 확인</button>}
    {/* The screen's one primary action; the app pins this bar above the bottom nav. */}
    <div className="capture-action" ref={setActionTarget}>
      {complete && <button type="button" className="primary" disabled={verifying||!checkedMeasurement||!!surfaceProblem||checkedKey!==photoKey} onClick={() => onFinish(nextFurniture())}>촬영 마치고 도면 만들기 ({items.length + 1}개)</button>}
    </div>
    {complete && <div className="actions">
      <button type="button" className="secondary" disabled={verifying||!checkedMeasurement||!!surfaceProblem||checkedKey!==photoKey} onClick={nextFurniture}>이 가구 저장 · 다음 가구 촬영</button>
    </div>}
    {!complete && items.length > 0 && <button type="button" className="secondary" onClick={() => onFinish(items)}>촬영 마치고 도면 만들기 ({items.length}개)</button>}
    </>}
  </div>;
}

// The small summary drawing is cropped to the shape itself, so it sits flush with the card padding.
function tightBox(points: { x: number; y: number }[]) {
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const size = Math.max(maxX - minX, maxY - minY) + 8;
  return `${(minX + maxX) / 2 - size / 2} ${(minY + maxY) / 2 - size / 2} ${size} ${size}`;
}
