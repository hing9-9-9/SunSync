import { useEffect, useRef, useState } from "react";
import { acquireBurst } from "./burst";
import { detectFrame } from "./measureApi";
import { analyseImage } from "./captureCheck";
import { liveHint } from "./captureGuide";
import { pickBestFrame, VISION_BURST_COUNT } from "./visionShot";
import type { Hint } from "./captureGuide";
import type { Board, CaptureMarker } from "./measureApi";

/**
 * In-app camera for the one-photo furniture search. It takes a short burst, checks the window marker in
 * every frame and hands back the best one. It needs no lens setup: the marker anchors the estimate.
 */
export default function VisionCamera({ session, board, onPhoto, onClose }: {
  session: CaptureMarker;
  board: Board;
  onPhoto: (file: File) => void;
  onClose: () => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<"idle" | "capturing" | "choosing">("idle");
  const [hint, setHint] = useState<Hint | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const media = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
          audio: false,
        });
        if (!alive) { media.getTracks().forEach((t) => t.stop()); return; }
        stream.current = media;
        if (video.current) { video.current.srcObject = media; await video.current.play(); }
        setReady(true);
      } catch {
        if (alive) setMessage("카메라를 열지 못했어요. 카메라 권한을 확인해 주세요.");
      }
    })();
    return () => { alive = false; stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null; };
  }, []);

  async function snapshot(): Promise<File> {
    const v = video.current, track = stream.current?.getVideoTracks()[0];
    if (!v || v.readyState < 2 || !v.videoWidth || !v.videoHeight || !track || track.readyState !== "live")
      throw new Error("카메라 화면이 준비될 때까지 기다려 주세요.");
    const canvas = document.createElement("canvas");
    canvas.width = v.videoWidth; canvas.height = v.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("카메라 영상을 읽지 못했어요.");
    const captured = Date.now();
    ctx.drawImage(v, 0, 0);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("사진 생성 실패"))), "image/jpeg", 0.94));
    return new File([blob], `vision-${captured}.jpg`, { type: "image/jpeg", lastModified: captured });
  }

  // Live check while the camera is open: say what is wrong with the marker before the shutter is pressed.
  useEffect(() => {
    if (!ready || busy) return;
    let live = true;
    const preview = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function inspect() {
      try {
        const file = await snapshot();
        preview.signal.throwIfAborted();
        const [frame, metrics] = await Promise.all([
          detectFrame(file, board, session, new Date(file.lastModified).toISOString(), undefined, preview.signal),
          analyseImage(file),
        ]);
        if (live) setHint(liveHint(frame, metrics));
      } catch (e) {
        if (live) setHint({ tone: "warn", text: e instanceof Error ? e.message : String(e) });
      }
      if (live) timer = setTimeout(() => void inspect(), 1200);
    }
    timer = setTimeout(() => void inspect(), 500);
    return () => { live = false; clearTimeout(timer); preview.abort(); };
  }, [ready, busy, board, session]);

  async function shoot() {
    if (!ready || busy) return;
    setBusy(true); setMessage(""); setPhase("capturing");
    try {
      const files = await acquireBurst(snapshot, { count: VISION_BURST_COUNT, intervalMs: 150 });
      setPhase("choosing");
      const seen = await Promise.all(files.map(async (file) => {
        const [frame, metrics] = await Promise.all([
          detectFrame(file, board, session, new Date(file.lastModified).toISOString()).catch(() => null),
          analyseImage(file),
        ]);
        return { markers: frame?.detections.length ?? 0, ...metrics };
      }));
      const best = pickBestFrame(seen);
      if (best < 0) throw new Error("사진을 만들지 못했어요. 다시 촬영해 주세요.");
      onPhoto(files[best]);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
      setBusy(false); setPhase("idle");
    }
  }

  const phaseText = phase === "capturing" ? "촬영 중… 휴대폰 위치를 유지해 주세요." : phase === "choosing" ? "촬영 완료! 휴대폰을 내려도 돼요. 사진 분석 중…" : "";
  return <div className="camera-stage open vision-camera">
    <video ref={video} className="camera-video" autoPlay muted playsInline />
    <div className="camera-stage-top">
      <strong>사진으로 가구 찾기</strong>
      <button type="button" className="text-button" disabled={busy} onClick={onClose}>카메라 닫기</button>
    </div>
    <div className="camera-stage-bottom">
      {phaseText ? <p role="status">{phaseText}</p> : hint ? <p role="status" className={`capture-hint ${hint.tone}`}>{hint.text}</p> : <p role="status">창문 마커와 가구가 같이 보이게 맞춰 주세요.</p>}
      {message && <p role="alert">{message}</p>}
      <button type="button" className="primary" disabled={!ready || busy || hint?.tone === "stop"} onClick={() => void shoot()}>
        {busy ? "촬영 중…" : "촬영"}
      </button>
    </div>
  </div>;
}
