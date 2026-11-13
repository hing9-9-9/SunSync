import BurstCamera from "./BurstCamera";
import type { CapturePhase } from "./BurstCamera";
import { useEffect, useRef, useState } from "react";
import { analyseImage, photoIssue } from "./captureCheck";
import { pointFailure } from "./captureGuide";
import type { PhotoIssue } from "./captureCheck";
import { shapes, validPolygon, nextCapturePoint, photosPerPoint } from "./capturePlan";
import type { Shape, CapturePoint } from "./capturePlan";
import type { CapturedFurniture } from "./model";
import { browserCaptureEnabled } from "./captureEnvironment";
import { checkMeasurement, selectFrames, detectFrame, captureSelection, resumeCaptureMarker, cancelStoredCapture, startCaptureMarker, endCaptureMarker } from "./measureApi";
import FilmModulePicker from "./FilmModulePicker";
import type { Camera, Frame, CaptureMarker, FilmTargetWindow } from "./measureApi";

type Photo = { file: File; url: string; issue: PhotoIssue | null; metrics: { brightness: number; sharpness: number }; frame?: Frame };

// MAR3.2: stand at each of the four corners of one piece of furniture and photograph the window marker.
export default function SpatialCapture(props: { sceneId?: string; count: number; onFinish: (items: CapturedFurniture[]) => void }) {
  if (!browserCaptureEnabled) return <section role="status">
    <h3>촬영은 SunSync 앱에서 진행해 주세요</h3>
    <p>앱에서 같은 공간을 선택해 촬영하면 측정 결과를 웹에서 확인할 수 있어요.</p>
  </section>;
  if (!props.sceneId) return <p role="alert">공간을 먼저 서버에 저장해 주세요.</p>;
  return <SessionSpatialCapture key={props.sceneId} {...props} />;
}

function SessionSpatialCapture(props: { sceneId?: string; count: number; onFinish: (items: CapturedFurniture[]) => void }) {
  const [session, setSession] = useState<CaptureMarker | null>(null);
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
        if (value && selected && (value.window_id !== selected.window_id ||
            value.film_module?.row !== selected.module_row || value.film_module?.col !== selected.module_col)) {
          throw new Error("선택한 필름과 진행 중인 촬영이 달라요. 이전 촬영을 끝낸 뒤 다시 시작해 주세요.");
        }
        setSession(value);setWindows(targets.windows);setSavedSelection(!!selected);
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
  if (busy) return <p role="status">촬영 마커를 준비하고 있어요…</p>;
  if (!session) {
    const win=windows.find(w=>w.window_id===windowId);
    return <section className="film-select">
      <h3>{savedSelection ? "선택한 필름으로 촬영" : "마커를 띄울 필름 선택"}</h3>
      <p className="muted small">안쪽에서 창문을 본 모습이에요. 고른 칸 가운데에 마커(60cm)가 떠요.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {!windows.length && <p className="muted small">촬영할 필름이 없어요. 창문 크기와 위치를 먼저 저장해 주세요.</p>}
      {!savedSelection && windows.length > 1 && <label className="field"><span>창문</span><select value={windowId} onChange={e=>{setWindowId(e.target.value);setRow(0);setCol(0);}}>
        {windows.map((w,i)=><option key={w.window_id} value={w.window_id}>창 {i+1}</option>)}
      </select></label>}
      {win && <>
        {!savedSelection && <FilmModulePicker rows={win.rows} cols={win.cols} topRemainder={win.top_remainder_m} rightRemainder={win.right_remainder_m} row={row} col={col} onChange={(r,c)=>{setRow(r);setCol(c);}} />}
        {savedSelection && <p>창문 입력 단계에서 선택한 필름 정보를 가져왔어요.</p>}
        <p className="film-select-choice" role="status">아래에서 {row+1}번째 · 왼쪽에서 {col+1}번째 필름</p>
      </>}
      <button type="button" className="primary" disabled={!win} onClick={()=>void begin()}>촬영 시작</button>
      <button type="button" className="text-button" onClick={()=>{if(!props.sceneId)return;setBusy(true);void cancelStoredCapture(props.sceneId).then(()=>setRetry(n=>n+1)).catch(e=>{setError(String(e));setBusy(false);});}}>이전 촬영 끝내기</button>
    </section>;
  }
  return <section>
    <p>선택한 필름: 아래에서 {(session.film_module?.row ?? 0)+1}번째, 왼쪽에서 {(session.film_module?.col ?? 0)+1}번째 · 마커 60cm</p>
    <details><summary>가상 필름 표시 확인 · 테스트용</summary>
      <svg viewBox={`0 0 ${session.film_cells[0]?.length ?? 10} ${session.film_cells.length || 10}`} width="200" height="200" role="img" aria-label="이번 촬영의 가상 필름 마커" shapeRendering="crispEdges">
        {session.film_cells.flatMap((row, y) => row.map((opacity, x) => <rect key={`${x}:${y}`} x={x} y={y} width="1" height="1" fill={opacity >= 0.5 ? "black" : "white"} />))}
      </svg>
      <p>가상 필름에 촬영용 마커를 표시했어요. 실제 필름 장치는 아직 연결되지 않았어요.</p>
    </details>
    {error && <p role="alert">{error}</p>}
    <TestSpatialCapture key={session.session_id} {...props} session={session} />
    <button type="button" onClick={() => void cancel()}>촬영 취소 · 마커 표시 종료</button>
  </section>;
}

function TestSpatialCapture({ count, onFinish, session }: { count: number; onFinish: (items: CapturedFurniture[]) => void; session: CaptureMarker }) {
  const [items, setItems] = useState<CapturedFurniture[]>([]);
  const [captureCamera,setCaptureCamera]=useState<Camera|null>(null);
  const [actionTarget,setActionTarget]=useState<HTMLDivElement|null>(null);
  const [rawFrames,setRawFrames]=useState<Record<number,Frame[]>>({});
  const [batchMessage,setBatchMessage]=useState("");
  const version=useRef(0);
  const [name, setName] = useState(`가구 ${count + 1}`);
  const [shape, setShape] = useState<Shape>('rectangle');
  const [custom, setCustom] = useState<CapturePoint[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const points = shape === 'polygon' ? custom : shapes[shape].points;
  const [photos, setPhotos] = useState<(Photo | null)[][]>([]);
  const [capturePhase, setCapturePhase] = useState<CapturePhase>("idle");
  const checking = capturePhase !== "idle";
  const [error, setError] = useState("");
  // Points that must be retaken, with the reason; only those points are cleared and shot again.
  const [problems, setProblems] = useState<Record<number,string>>({});
  const [verifying, setVerifying] = useState(false);
  const [checkedKey, setCheckedKey] = useState("");
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
  function reset() { version.current++;setVerifying(false);setVerifyFailed(false);setProblems({});setCheckedKey("");setSurfaceProblem("");setRawFrames({});setBatchMessage(""); photos.flat().forEach(p=>p&&URL.revokeObjectURL(p.url)); setPhotos([]); setError(''); }

  async function takeBatch(files:File[],profile:Camera,ticket:number) {
    if(pendingPoint===null||!confirmed||ticket!==version.current)return;
    const target=pendingPoint;
    if(JSON.stringify(profile)!==JSON.stringify(captureCamera)&&photos.some(row=>row.some(Boolean))) {
      reset();setCaptureCamera(profile);setError("카메라 설정이 바뀌어 이전 사진을 비웠어요. P1부터 다시 촬영해 주세요.");return;
    }
    setCaptureCamera(profile);setError("");setBatchMessage("");
    const candidates:Photo[]=[];
    try {
      for(const file of files.slice(0,20)) {
        if(ticket!==version.current)return;
        const metrics=await analyseImage(file);
        if(photoIssue({...metrics,marker:"visible"}))continue;
        const frame=await detectFrame(file,session.board,session,new Date(file.lastModified).toISOString());
        if(frame.width!==profile.width||frame.height!==profile.height)throw new Error("카메라 준비 때와 영상 크기가 달라요. 다시 준비해 주세요.");
        candidates.push({file,url:"",frame,metrics,issue:frame.detections.length===1?null:"missing"});
      }
      if(!candidates.length)throw new Error("사용할 수 있는 사진이 없어요. 밝기와 흔들림을 확인해 주세요.");
      const result=await selectFrames(candidates.map(p=>p.frame!),profile,session.board,`P${target+1}`);
      if(ticket!==version.current)return;
      if(result.status!=="ok"){const why=pointFailure(`P${target+1}`,result);setProblems(all=>({...all,[target]:why}));throw new Error(why);}
      setProblems(all=>{const next={...all};delete next[target];return next;});
      setBatchMessage(`P${target+1} 촬영을 완료했어요.`);
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
    checkMeasurement(session.scene_id, captureCamera, { session, shape, vertices: points, points: points.map((_,i)=>({ id:`P${i+1}`, frames:rawFrames[i] ?? (photos[i]??[]).map(p=>p!.frame!) })) })
      .then(found => {
        if (ticket !== version.current) return;
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
    const captured: CapturedFurniture[] = [...items, { name: name.trim() || `가구 ${count + items.length + 1}`, type: "other", capture: {session, camera:captureCamera!, shape, points: points.map((p,i)=>({id:`P${i+1}`,position:p,photos:photos[i].map(p=>p!.file),frames:rawFrames[i]??(photos[i].every(p=>p?.frame)?photos[i].map(p=>p!.frame!):undefined)}))} }];
    setItems(captured);
    setName(`가구 ${count + captured.length + 1}`);
    reset();
    setConfirmed(false);
    return captured;
  }

  return <div className="spatial-capture corner-capture">
    <div className="two-fields">
      <label className="field"><span>가구 이름</span><input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field"><span>상판 형태</span>
        <select value={shape} disabled={confirmed||checking} onChange={e=>{reset();setShape(e.target.value as Shape);setCustom([]);}}>
          {Object.entries(shapes).map(([value,s])=><option key={value} value={value}>{s.label}</option>)}
        </select>
      </label>
    </div>
    <section className="shape-plan" aria-label="형태별 촬영 계획">
      <svg viewBox="0 0 100 100" role="img" aria-label={`${shapes[shape].label} 촬영 순서`} onClick={e=>{if(shape!=='polygon'||confirmed)return; const r=e.currentTarget.getBoundingClientRect();setCustom(p=>[...p,{x:Math.max(8,Math.min(92,(e.clientX-r.left)/r.width*100)),y:Math.max(8,Math.min(92,(e.clientY-r.top)/r.height*100))}]);}}>
        {shape==='circle'?<circle cx="50" cy="50" r="35" className="shape-outline"/>:shape==='ellipse'?<ellipse cx="50" cy="50" rx="40" ry="25" className="shape-outline"/>:<polygon points={points.map(p=>`${p.x},${p.y}`).join(' ')} className="shape-outline"/>}
        {points.map((p,i)=><g key={i}><circle cx={p.x} cy={p.y} r="3" className={confirmed&&!complete&&i===corner?'point-current':'point'}/><text x={p.x} y={p.y-5} textAnchor="middle">P{i+1}</text></g>)}
      </svg>
      <p>{shape==='polygon'&&!confirmed?'꼭짓점을 순서대로 눌러 모양 구성':`${points.length}개 지점 · 지점마다 촬영 버튼 한 번`}</p>
      {!confirmed ? <div className="actions">
        {shape==='polygon'&&<button type="button" className="secondary" disabled={!custom.length} onClick={()=>setCustom(p=>p.slice(0,-1))}>마지막 점 취소</button>}
        <button type="button" className="primary" disabled={shape==='polygon'&&!validPolygon(custom)} onClick={()=>{setConfirmed(true);setError('');}}>형태 확정 · 촬영 시작</button>
      </div>:<button type="button" className="text-button" disabled={checking} onClick={()=>{reset();setConfirmed(false);}}>형태 다시 선택</button>}
    </section>
    <figure className="capture-viewfinder" style={{display: confirmed ? undefined : "none"}}>
      <BurstCamera session={session} active={confirmed&&!complete} pointLabel={`P${corner+1}`} actionTarget={actionTarget}
        disabled={checking} getTicket={()=>version.current} phase={capturePhase} onPhaseChange={setCapturePhase} onReady={setCaptureCamera} onBatch={takeBatch}/>
      <figcaption role="status">{capturePhase === "analyzing" || verifying ? "촬영 완료! 휴대폰을 내려도 돼요. 사진 분석 중…" : capturePhase === "capturing" ? "촬영 중… 휴대폰 위치를 유지해 주세요." : complete ? "모든 지점 촬영 완료" : !captureCamera ? "카메라 준비 · 마커가 보이도록 12장 촬영해 주세요" : `P${corner+1} · 촬영 버튼을 한 번 눌러 주세요`}</figcaption>
    </figure>
    {confirmed && <>
    {batchMessage&&<p role="status">{batchMessage}</p>}
    <ol className="corner-steps" aria-label="지점 촬영 상태">
      {points.map((_,i) => {
        const n=(photos[i]??[]).filter(p=>p&&!p.issue).length;
        return <li key={i} className={problems[i]?'retake':n>=photosPerPoint?'done':i===corner?'current':'waiting'} aria-current={i===corner?'step':undefined}>
          <span className="corner-number">P{i+1}</span><small>{problems[i]?"다시 촬영":n>=photosPerPoint?"완료":"대기"}</small>
        </li>;
      })}
    </ol>
    <p role="status" className="tracking-status">{capturePhase === "analyzing" ? "사진 분석 중…" : capturePhase === "capturing" ? "촬영 중…":verifying?'지점별 측정 확인 중…':complete?'모든 지점 촬영 완료':!captureCamera?'카메라 준비 사진 촬영 단계 (완료 후 P1부터 시작)':`P${corner+1} 위치에서 촬영`}</p>
    {Object.entries(problems).sort(([a],[b])=>Number(a)-Number(b)).map(([i,why])=><p key={i} className="point-problem">{why}</p>)}
    {surfaceProblem && <p role="alert" className="error">{surfaceProblem}</p>}
    {!complete && !checking && <p className="muted small">도면의 P{corner+1}에 렌즈를 맞추고 마커 전체가 보이도록 촬영 버튼을 한 번 눌러 주세요. 촬영이 끝날 때까지 위치를 유지해 주세요.</p>}
    <div className="actions">{points.map((_,i)=>(photos[i]??[]).some(Boolean)&&<button key={i} type="button" className="text-button" disabled={checking} onClick={()=>{
      version.current++;setVerifying(false);setVerifyFailed(false);photos[i]?.forEach(p=>p&&URL.revokeObjectURL(p.url));
      setRawFrames(all=>{const next={...all};delete next[i];return next;});setProblems(all=>{const next={...all};delete next[i];return next;});setSurfaceProblem("");
      setPhotos(all=>all.map((row,j)=>j===i?[]:row));setBatchMessage("");setError("");
    }}>P{i+1} 다시 촬영</button>)}</div>
    {error && !Object.values(problems).includes(error) && <p role="alert" className="error">{error}</p>}
    {complete && verifyFailed && <button type="button" className="secondary" onClick={()=>setVerifyFailed(false)}>다시 확인</button>}
    {/* The screen's one primary action; the app pins this bar above the bottom nav. */}
    <div className="capture-action" ref={setActionTarget}>
      {complete && <button type="button" className="primary" disabled={verifying||!!surfaceProblem||checkedKey!==photoKey} onClick={() => onFinish(nextFurniture())}>촬영 마치고 도면 만들기 ({items.length + 1}개)</button>}
    </div>
    {complete && <div className="actions">
      <button type="button" className="secondary" disabled={verifying||!!surfaceProblem||checkedKey!==photoKey} onClick={nextFurniture}>이 가구 저장 · 다음 가구 촬영</button>
    </div>}
    {!complete && items.length > 0 && <button type="button" className="secondary" onClick={() => onFinish(items)}>촬영 마치고 도면 만들기 ({items.length}개)</button>}
    </>}
    <p className="muted small">측정한 가구의 위치와 치수를 확인해 주세요.</p>
  </div>;
}
