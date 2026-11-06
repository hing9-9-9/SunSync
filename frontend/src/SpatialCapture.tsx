import { useEffect, useRef, useState } from "react";
import { measurePixels, photoIssue, retakeMessages } from "./captureCheck";
import type { PhotoIssue } from "./captureCheck";
import { shapes, validPolygon, nextPhotoSlot, photosPerPoint } from "./capturePlan";
import type { Shape, CapturePoint } from "./capturePlan";
import type { CapturedFurniture } from "./model";
import { browserCaptureEnabled } from "./captureEnvironment";
import { BOARD, MeasureError, detectFrame, savedCamera, setupCamera } from "./measureApi";
import type { Camera, Frame } from "./measureApi";

type Photo = { file: File; url: string; issue: PhotoIssue | null; metrics: { brightness: number; sharpness: number }; frame?: Frame };

// Reads a downscaled copy so the check stays fast on phones; the photo never leaves the device.
async function analyse(file: File) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 320 / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(3, Math.round(bitmap.width * scale)), height = Math.max(3, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width; canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("사진을 확인할 수 없습니다.");
  context.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  return measurePixels(context.getImageData(0, 0, width, height).data, width, height);
}

// MAR3.2: stand at each of the four corners of one piece of furniture and photograph the window marker.
export default function SpatialCapture(props: { count: number; onFinish: (items: CapturedFurniture[]) => void }) {
  if (!browserCaptureEnabled) return <section role="status">
    <h3>촬영은 SunSync 앱에서 진행해 주세요</h3>
    <p>앱에서 같은 공간을 선택해 촬영하면 측정 결과를 웹에서 확인할 수 있어요.</p>
  </section>;
  return <TestSpatialCapture {...props} />;
}

function TestSpatialCapture({ count, onFinish }: { count: number; onFinish: (items: CapturedFurniture[]) => void }) {
  // WEB APP TEST ONLY: intrinsics come from the camera setup in 더보기 (see CameraSetup below).
  const [camera] = useState<Camera | null>(() => savedCamera());
  const [items, setItems] = useState<CapturedFurniture[]>([]);
  const [name, setName] = useState(`가구 ${count + 1}`);
  const [shape, setShape] = useState<Shape>('rectangle');
  const [custom, setCustom] = useState<CapturePoint[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const points = shape === 'polygon' ? custom : shapes[shape].points;
  const [photos, setPhotos] = useState<(Photo | null)[][]>([]);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const latest = useRef(photos);
  useEffect(() => { latest.current = photos; }, [photos]);
  useEffect(() => () => latest.current.flat().forEach(p => p && URL.revokeObjectURL(p.url)), []);
  const slot = nextPhotoSlot(points.map((_,i) => Array.from({length:photosPerPoint},(_,j)=>!!photos[i]?.[j] && !photos[i][j]!.issue)));
  const corner = slot?.point ?? points.length - 1;
  const current = slot ? photos[slot.point]?.[slot.photo] : photos.at(-1)?.at(-1);
  const complete = confirmed && !slot;
  function reset() { photos.flat().forEach(p=>p&&URL.revokeObjectURL(p.url)); setPhotos([]); setError(''); }

  async function take(file: File | undefined) {
    if (!file || !slot || !confirmed || checking) return;
    if (!file.type.startsWith("image/")) { setError("사진 파일을 선택해 주세요."); return; }
    setError(""); setChecking(true);
    try {
      const metrics = await analyse(file);
      // The measurement server finds the board; at least two markers give a camera pose.
      const frame = camera ? await detectFrame(file) : undefined;
      const found = frame?.detections.length ?? 4;
      const url = URL.createObjectURL(file);
      const photo = { file, url, metrics, frame, issue: photoIssue({ ...metrics, marker: found >= 2 ? "visible" : found ? "partial" : "missing" }) };
      const replaced = photos[slot.point]?.[slot.photo];
      if (replaced) URL.revokeObjectURL(replaced.url);
      setPhotos(all => points.map((_,i)=>Array.from({length:photosPerPoint},(_,j)=>i===slot.point&&j===slot.photo ? photo : all[i]?.[j] ?? null)));
    } catch (e) {
      setError(e instanceof MeasureError ? e.message : "사진을 읽지 못했어요. 다시 찍어 주세요.");
    } finally {
      setChecking(false);
    }
  }
  function nextFurniture() {
    const captured: CapturedFurniture[] = [...items, { name: name.trim() || `가구 ${count + items.length + 1}`, type: "other", capture: {shape, points: points.map((p,i)=>({id:`P${i+1}`,position:p,photos:photos[i].map(p=>p!.file),frames:photos[i].every(p=>p?.frame)?photos[i].map(p=>p!.frame!):undefined}))} }];
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
        <select value={shape} disabled={confirmed} onChange={e=>{reset();setShape(e.target.value as Shape);setCustom([]);}}>
          {Object.entries(shapes).map(([value,s])=><option key={value} value={value}>{s.label}</option>)}
        </select>
      </label>
    </div>
    <section className="shape-plan" aria-label="형태별 촬영 계획">
      <svg viewBox="0 0 100 100" role="img" aria-label={`${shapes[shape].label} 촬영 순서`} onClick={e=>{if(shape!=='polygon'||confirmed)return; const r=e.currentTarget.getBoundingClientRect();setCustom(p=>[...p,{x:Math.max(8,Math.min(92,(e.clientX-r.left)/r.width*100)),y:Math.max(8,Math.min(92,(e.clientY-r.top)/r.height*100))}]);}}>
        {shape==='circle'?<circle cx="50" cy="50" r="35" className="shape-outline"/>:shape==='ellipse'?<ellipse cx="50" cy="50" rx="40" ry="25" className="shape-outline"/>:<polygon points={points.map(p=>`${p.x},${p.y}`).join(' ')} className="shape-outline"/>}
        {points.map((p,i)=><g key={i}><circle cx={p.x} cy={p.y} r="3" className={confirmed&&!complete&&i===corner?'point-current':'point'}/><text x={p.x} y={p.y-5} textAnchor="middle">P{i+1}</text></g>)}
      </svg>
      <p>{shape==='polygon'&&!confirmed?'꼭짓점을 순서대로 눌러 모양 구성':`${points.length}개 지점 · 지점마다 사진 3장`}</p>
      {!confirmed ? <div className="actions">
        {shape==='polygon'&&<button type="button" className="secondary" disabled={!custom.length} onClick={()=>setCustom(p=>p.slice(0,-1))}>마지막 점 취소</button>}
        <button type="button" className="primary" disabled={shape==='polygon'&&!validPolygon(custom)} onClick={()=>{setConfirmed(true);setError('');}}>형태 확정 · 촬영 시작</button>
      </div>:<button type="button" className="text-button" onClick={()=>{reset();setConfirmed(false);}}>형태 다시 선택</button>}
    </section>
    {confirmed && <>
    {/* Viewfinder: the corner being shot now, or its photo when it needs a retake. */}
    <figure className={`capture-viewfinder ${current?.issue ? "retake" : ""}`}>
      <div className="viewfinder-frame" aria-hidden="true">
        <svg className="viewfinder-brackets" viewBox="0 0 100 100" preserveAspectRatio="none">
          <path d="M2 18V2h16M82 2h16v16M98 82v16H82M18 98H2V82" fill="none" stroke="currentColor" strokeWidth="3" vectorEffect="non-scaling-stroke" />
        </svg>
        {current?.url ? <img src={current.url} alt="" /> : complete && photos.at(-1)?.at(-1) ? <img src={photos.at(-1)!.at(-1)!.url} alt="" /> : (
          <svg viewBox="0 0 40 40" className="viewfinder-marker">
            <path d="M4 4h12v12H4zM24 4h12v12H24zM4 24h12v12H4z" fill="none" stroke="currentColor" strokeWidth="3" />
            <path d="M24 24h5v5h-5zM31 31h5v5h-5zM31 24h5v3M24 31h3v5" fill="currentColor" />
          </svg>
        )}
      </div>
      <figcaption>
        {complete
          ? "모든 지점 촬영 완료"
          : current?.issue
            ? `P${corner + 1} · 사진 ${(slot?.photo ?? 0)+1} 재촬영`
            : `P${corner + 1} · 사진 ${(slot?.photo ?? 0)+1} / 3`}
      </figcaption>
    </figure>
    <ol className="corner-steps" aria-label="지점 촬영 상태">
      {points.map((_,i) => {
        const n=(photos[i]??[]).filter(p=>p&&!p.issue).length;
        return <li key={i} className={n===3?'done':i===corner?'current':'waiting'} aria-current={i===corner?'step':undefined}>
          <span className="corner-number">P{i+1}</span><small>{n} / 3장</small>
        </li>;
      })}
    </ol>
    <p role="status" className="tracking-status">{checking?'사진 확인 중…':complete?'모든 지점 촬영 완료':`P${corner+1} 위치에서 사진 ${(slot?.photo??0)+1} / 3 촬영`}</p>
    {current?.issue ? <p className="error" role="alert">{retakeMessages[current.issue]}</p> : !complete && <p className="muted small">도면의 P{corner+1}에 렌즈를 맞추고 창문 마커 전체 촬영. 같은 위치에서 3장 촬영 후 다음 지점 이동.</p>}
    <div className="point-photos">{(photos[corner]??[]).map((p,j)=>p&&<div key={j}><img src={p.url} alt={`P${corner+1} 사진 ${j+1}`}/><button type="button" className="text-button" disabled={checking} onClick={()=>{URL.revokeObjectURL(p.url);setPhotos(all=>all.map((row,i)=>i===corner?row.map((p,k)=>k===j?null:p):row));}}>사진 {j+1} 재촬영</button></div>)}</div>
    {error && <p role="alert" className="error">{error}</p>}
    {/* The screen's one primary action; the app pins this bar above the bottom nav. */}
    <div className="capture-action">
      {!complete ? <label className={`file-button ${checking ? "disabled" : ""}`}>
        {checking ? "사진 확인 중…" : current?.issue ? `P${corner + 1} 사진 ${(slot?.photo??0)+1} 재촬영` : `P${corner + 1} 사진 ${(slot?.photo??0)+1} 촬영`}
        <input type="file" accept="image/*" capture="environment" disabled={checking} onChange={(e) => { void take(e.target.files?.[0]); e.target.value = ""; }} />
      </label> : <button type="button" className="primary" onClick={() => onFinish(nextFurniture())}>촬영 마치고 도면 만들기 ({items.length + 1}개)</button>}
    </div>
    {!complete && <label className="file-button secondary photo-library">사진 파일 선택<input type="file" accept="image/*" disabled={checking} onChange={e=>{void take(e.target.files?.[0]);e.target.value='';}} /></label>}
    {complete && <div className="actions">
      <button type="button" className="secondary" onClick={nextFurniture}>이 가구 저장 · 다음 가구 촬영</button>
    </div>}
    {!complete && items.length > 0 && <button type="button" className="secondary" onClick={() => onFinish(items)}>촬영 마치고 도면 만들기 ({items.length}개)</button>}
    </>}
    <p className="muted small">측정한 가구의 위치와 치수를 확인해 주세요.</p>
  </div>;
}

const SETUP_MIN = 12, SETUP_MAX = 30;

/**
 * WEB APP TEST ONLY — camera setup for phones testing through the browser.
 * A native app reads the lens intrinsics from the OS camera (mobile/ android·ios adapters) and
 * skips this, except on devices that report none. Browsers expose no intrinsics, so the installed
 * board photographed from many positions and angles lets the measurement server estimate them
 * (/v1/mobile/automatic-setup). Lives in 더보기 › 카메라 준비; the result is kept on this phone.
 * Remove this screen (and savedCamera use) once capture moves to the native app.
 */
export function CameraSetup({ onReady }: { onReady: (camera: Camera) => void }) {
  const [frames, setFrames] = useState<Frame[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function add(file: File | undefined) {
    if (!file || busy) return;
    setBusy(true); setMessage("");
    try {
      const frame = await detectFrame(file);
      if (frame.detections.length < 4) setMessage("보드의 마커 4개가 모두 보이게 다시 찍어 주세요.");
      else if (frames.length && (frame.width !== frames[0].width || frame.height !== frames[0].height)) setMessage("같은 카메라·같은 방향으로 찍어 주세요.");
      else setFrames((all) => [...all, frame].slice(0, SETUP_MAX));
    } catch (e) {
      setMessage(e instanceof MeasureError ? e.message : "사진을 읽지 못했어요. 다시 찍어 주세요.");
    } finally { setBusy(false); }
  }
  async function finish() {
    setBusy(true); setMessage("");
    try { onReady(await setupCamera(frames)); }
    catch (e) { setMessage(e instanceof MeasureError ? e.message : "카메라를 준비하지 못했어요."); }
    finally { setBusy(false); }
  }
  return <section className="spatial-capture camera-setup" aria-labelledby="camera-setup-title">
    <h3 id="camera-setup-title">카메라 준비 · 웹앱 테스트용</h3>
    <p className="muted small">창문의 마커 보드({BOARD.ids_by_row.flat().length}개 마커) 전체가 보이게, 휴대폰 위치와 기울기를 바꿔 가며 {SETUP_MIN}장 이상 찍어 주세요. 보드가 화면 가운데·가장자리에 골고루 오도록 찍으면 정확해져요.</p>
    <p role="status" className="tracking-status">{busy ? "확인 중…" : `${frames.length} / ${SETUP_MIN}장`}</p>
    {message && <p role="alert" className="error">{message}</p>}
    <div className="camera-setup-action">
      {frames.length < SETUP_MIN
        ? <label className={`file-button ${busy ? "disabled" : ""}`}>{busy ? "확인 중…" : `보드 사진 ${frames.length + 1} 촬영`}<input type="file" accept="image/*" capture="environment" disabled={busy} onChange={(e) => { void add(e.target.files?.[0]); e.target.value = ""; }} /></label>
        : <button type="button" className="primary" disabled={busy} onClick={() => void finish()}>{busy ? "준비 중…" : "카메라 준비 완료"}</button>}
    </div>
    {frames.length >= SETUP_MIN && frames.length < SETUP_MAX && <label className="file-button secondary">한 장 더 찍기<input type="file" accept="image/*" capture="environment" disabled={busy} onChange={(e) => { void add(e.target.files?.[0]); e.target.value = ""; }} /></label>}
  </section>;
}
