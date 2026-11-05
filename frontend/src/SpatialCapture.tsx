import { useEffect, useRef, useState } from "react";
import { corners, measurePixels, photoIssue, retakeMessages } from "./captureCheck";
import type { PhotoIssue } from "./captureCheck";
import { furnitureTypes } from "./model";
import type { CapturedFurniture, FurnitureType } from "./model";
import { browserCaptureEnabled } from "./captureEnvironment";

type Photo = { url: string; issue: PhotoIssue | null; metrics: { brightness: number; sharpness: number } };

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
  const [items, setItems] = useState<CapturedFurniture[]>([]);
  const [name, setName] = useState(`가구 ${count + 1}`);
  const [type, setType] = useState<FurnitureType>("bookcase");
  const [photos, setPhotos] = useState<(Photo | null)[]>([null, null, null, null]);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const latest = useRef(photos);
  useEffect(() => { latest.current = photos; }, [photos]);
  useEffect(() => () => latest.current.forEach((p) => p && URL.revokeObjectURL(p.url)), []);
  const corner = photos.findIndex((p) => !p || p.issue);
  const current = corner === -1 ? null : photos[corner];
  const complete = corner === -1;

  async function take(file: File | undefined) {
    if (!file || corner === -1) return;
    if (!file.type.startsWith("image/")) { setError("사진 파일을 선택해 주세요."); return; }
    setError(""); setChecking(true);
    try {
      const metrics = await analyse(file);
      const url = URL.createObjectURL(file);
      // Marker recognition is the FR-03 module's job; it is assumed visible until that is connected.
      const photo = { url, metrics, issue: photoIssue({ ...metrics, marker: "visible" }) };
      const replaced = photos[corner];
      if (replaced) URL.revokeObjectURL(replaced.url);
      setPhotos((all) => all.map((p, i) => (i === corner ? photo : p)));
    } catch {
      setError("사진을 읽지 못했어요. 다시 찍어 주세요.");
    } finally {
      setChecking(false);
    }
  }
  function nextFurniture() {
    const captured = [...items, { name: name.trim() || `가구 ${count + items.length + 1}`, type }];
    setItems(captured);
    setName(`가구 ${count + captured.length + 1}`);
    photos.forEach((p) => p && URL.revokeObjectURL(p.url));
    setPhotos([null, null, null, null]);
    return captured;
  }

  return <div className="spatial-capture corner-capture">
    <p role="note">개발용 사진 테스트 · 실제 측정 결과로 저장되지 않습니다.</p>
    <div className="two-fields">
      <label className="field"><span>가구 이름</span><input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field"><span>가구 종류</span>
        <select value={type} onChange={(e) => setType(e.target.value as FurnitureType)}>
          {Object.entries(furnitureTypes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
    </div>
    {/* Viewfinder: the corner being shot now, or its photo when it needs a retake. */}
    <figure className={`capture-viewfinder ${current?.issue ? "retake" : ""}`}>
      <div className="viewfinder-frame" aria-hidden="true">
        <svg className="viewfinder-brackets" viewBox="0 0 100 100" preserveAspectRatio="none">
          <path d="M2 18V2h16M82 2h16v16M98 82v16H82M18 98H2V82" fill="none" stroke="currentColor" strokeWidth="3" vectorEffect="non-scaling-stroke" />
        </svg>
        {current?.url ? <img src={current.url} alt="" /> : complete && photos[3] ? <img src={photos[3].url} alt="" /> : (
          <svg viewBox="0 0 40 40" className="viewfinder-marker">
            <path d="M4 4h12v12H4zM24 4h12v12H24zM4 24h12v12H4z" fill="none" stroke="currentColor" strokeWidth="3" />
            <path d="M24 24h5v5h-5zM31 31h5v5h-5zM31 24h5v3M24 31h3v5" fill="currentColor" />
          </svg>
        )}
      </div>
      <figcaption>
        {complete
          ? "네 모서리 촬영 완료"
          : current?.issue
            ? `모서리 ${corner + 1} 다시 촬영`
            : `모서리 ${corner + 1} · 창문 QR 전체 촬영`}
      </figcaption>
    </figure>
    <ol className="corner-steps" aria-label="모서리 촬영 상태">
      {corners.map((label, i) => {
        const p = photos[i];
        const state = p ? (p.issue ? "retake" : "done") : i === corner ? "current" : "waiting";
        return <li key={label} className={state} aria-current={i === corner ? "step" : undefined}>
          {p ? <img src={p.url} alt="" /> : <span className="corner-number">{i + 1}</span>}
          <strong>{label}</strong>
          <small>{{ done: "촬영 완료", retake: "다시 촬영", current: "촬영할 차례", waiting: "대기" }[state]}</small>
        </li>;
      })}
    </ol>
    <p role="status" className="tracking-status">
      {checking ? "사진 확인 중…" : complete ? `${name || "가구"} · 네 모서리 촬영 완료` : `모서리 ${corner + 1} / 4 · ${corners[corner]}`}
    </p>
    {current?.issue
      ? <p className="error" role="alert">{retakeMessages[current.issue]}</p>
      : !complete && <p className="muted small">가구의 {corners[corner]} 모서리에 서서 창문 필름의 QR 마커 전체가 화면에 들어오게 찍어 주세요.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {/* The screen's one primary action; the app pins this bar above the bottom nav. */}
    <div className="capture-action">
      {!complete ? <label className={`file-button ${checking ? "disabled" : ""}`}>
        {checking ? "사진 확인 중…" : current?.issue ? `모서리 ${corner + 1} 다시 찍기` : `모서리 ${corner + 1} 촬영`}
        <input type="file" accept="image/*" capture="environment" disabled={checking} onChange={(e) => { void take(e.target.files?.[0]); e.target.value = ""; }} />
      </label> : <button type="button" className="primary" onClick={() => onFinish(nextFurniture())}>촬영 마치고 도면 만들기 ({items.length + 1}개)</button>}
    </div>
    {complete && <div className="actions">
      <button type="button" className="secondary" onClick={nextFurniture}>이 가구 저장 · 다음 가구 촬영</button>
    </div>}
    {!complete && items.length > 0 && <button type="button" className="secondary" onClick={() => onFinish(items)}>촬영 마치고 도면 만들기 ({items.length}개)</button>}
    <p className="muted small">사진의 흔들림과 밝기를 확인합니다. 가구 치수는 기본값으로 표시되므로 도면에서 실제 치수로 수정하세요.</p>
  </div>;
}
