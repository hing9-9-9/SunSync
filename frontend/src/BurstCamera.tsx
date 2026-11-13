import { createPortal } from "react-dom";
import { useEffect, useRef, useState } from "react";
import { acquireBurst, appendSetupView, captureGeometry, CaptureGeometryGuard } from "./burst";
import { detectFrame, setupCamera } from "./measureApi";
import { analyseImage } from "./captureCheck";
import { liveHint } from "./captureGuide";
import type { Hint } from "./captureGuide";
import type { Camera, CaptureMarker, Frame } from "./measureApi";

export type CapturePhase = "idle" | "capturing" | "analyzing";

/** Test-only video stream. Its calibration is deliberately separate from still-photo profiles. */
export default function BurstCamera({
  session,
  active,
  pointLabel,
  actionTarget,
  disabled,
  getTicket,
  phase,
  onPhaseChange,
  onReady,
  onBatch,
}: {
  session: CaptureMarker;
  active: boolean;
  pointLabel: string;
  actionTarget: HTMLElement | null;
  disabled: boolean;
  getTicket: () => number;
  phase: CapturePhase;
  onPhaseChange: (phase: CapturePhase) => void;
  onReady?: (camera: Camera | null) => void;
  onBatch: (files: File[], camera: Camera, ticket: number) => Promise<void>;
}) {
  const video = useRef<HTMLVideoElement>(null),
    stream = useRef<MediaStream | null>(null);
  const controller = useRef<AbortController | null>(null),
    generation = useRef(0);
  const [running, setRunning] = useState(false),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const [hint, setHint] = useState<Hint | null>(null);
  const [views, setViews] = useState<Frame[]>([]),
    [camera, setCamera] = useState<Camera | null>(null);
  const geometry = useRef(new CaptureGeometryGuard());
  function stop() {
    generation.current++;
    controller.current?.abort();
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    geometry.current.reset();
  }
  useEffect(() => () => stop(), []);
  function signature() {
    const track = stream.current?.getVideoTracks()[0];
    return captureGeometry(track?.getSettings() ?? {}, video.current?.videoWidth ?? 0, video.current?.videoHeight ?? 0);
  }
  async function open() {
    setBusy(true);
    setMessage("");
    const version = ++generation.current;
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      });
      if (version !== generation.current) {
        media.getTracks().forEach((t) => t.stop());
        return;
      }
      stream.current = media;
      geometry.current.reset();
      setViews([]);
      setCamera(null);
      onReady?.(null);
      setRunning(true);
      if (video.current) {
        video.current.srcObject = media;
        await video.current.play();
      }
      media.getVideoTracks()[0].addEventListener("ended", () => {
        if (stream.current !== media) return;
        stop();
        setCamera(null);
        onReady?.(null);
        setRunning(false);
        setMessage("카메라 연결이 종료됐어요. 다시 준비해 주세요.");
      });
    } catch (e) {
      setMessage(String(e));
      stop();
      setRunning(false);
    } finally {
      setBusy(false);
    }
  }
  function checkGeometry() {
    if (!geometry.current.accept(signature())) {
      generation.current++;
      controller.current?.abort();
      setCamera(null);
      onReady?.(null);
      setViews([]);
      const text = "카메라 크기나 렌즈 설정이 바뀌었어요. 현재 화면에서 카메라 준비 사진을 다시 촬영해 주세요.";
      setMessage(text);
      throw new Error(text);
    }
  }
  async function snapshot() {
    const v = video.current;
    const track = stream.current?.getVideoTracks()[0];
    if (!v || v.readyState < 2 || !v.videoWidth || !v.videoHeight || !track || track.readyState !== "live")
      throw new Error("카메라 화면이 준비될 때까지 기다려 주세요.");
    checkGeometry();
    const version = generation.current;
    const canvas = document.createElement("canvas");
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("카메라 영상을 읽지 못했어요.");
    const captured = Date.now();
    ctx.drawImage(v, 0, 0);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (b) => (b ? resolve(b) : reject(new Error("사진 생성 실패"))),
        "image/jpeg",
        0.94,
      ),
    );
    if (version !== generation.current) throw new Error("카메라 촬영이 취소됐어요.");
    checkGeometry();
    return new File([blob], `frame-${captured}.jpg`, {
      type: "image/jpeg",
      lastModified: captured,
    });
  }
  async function setup() {
    setBusy(true);
    setMessage("");
    const version = generation.current;
    let next: Frame[] | null = null;
    try {
      const file = await snapshot();
      const frame = await detectFrame(
        file,
        session.board,
        session,
        new Date(file.lastModified).toISOString(),
      );
      if (version !== generation.current) return;
      if (frame.detections.length !== 1)
        throw new Error("마커 전체가 보이도록 각도를 조절해 주세요.");
      next = appendSetupView(views, frame);
      if (next.length >= 12) {
        const profile = await setupCamera(next, session.board, false);
        if (version === generation.current) {
          setViews(next);
          setCamera(profile);
          onReady?.(profile);
          setMessage(`카메라 준비가 완료됐어요. 이제 ${pointLabel} 지점에서 촬영해 주세요.`);
        }
      } else {
        setViews(next);
      }
    } catch (e) {
      if (next && next.length >= 12 && version === generation.current) {
        setViews(next.slice(-11));
      }
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  // Live check while the camera is open (setup shots and point shots alike): say what is wrong with
  // the marker now, so the person fixes it before pressing the shutter.
  useEffect(() => {
    if (!active || !running || busy || disabled) { setHint(null); return; }
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    async function inspect() {
      try {
        const file = await snapshot();
        const [frame, metrics] = await Promise.all([
          detectFrame(file, session.board, session, new Date(file.lastModified).toISOString(), camera ?? undefined),
          analyseImage(file),
        ]);
        if (live) setHint(liveHint(frame, metrics));
      } catch (e) {
        if (live) setHint({ tone: "warn", text: e instanceof Error ? e.message : String(e) });
      }
      if (live) timer = setTimeout(() => void inspect(), 1200);
    }
    timer = setTimeout(() => void inspect(), 500);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [active, running, camera, busy, disabled, session]);
  async function shoot() {
    if (!active || !camera || busy || disabled) return;
    setBusy(true);
    onPhaseChange("capturing");
    setMessage("");
    const version = generation.current;
    const ticket = getTicket();
    controller.current = new AbortController();
    try {
      const files = await acquireBurst(snapshot, {
        signal: controller.current.signal,
      });
      if (version === generation.current) {
        onPhaseChange("analyzing");
        await onBatch(files, camera, ticket);
      }
    } catch (e) {
      if (version === generation.current)
        setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      onPhaseChange("idle");
    }
  }
  const action = <button type="button" className="primary" disabled={busy || disabled || (running && !!camera && hint?.tone === "stop")}
    onClick={() => void (!running ? open() : !camera ? setup() : shoot())}>
    {phase === "analyzing" ? "촬영 완료! 사진 분석 중…" : phase === "capturing" ? "촬영 중…" : busy ? "카메라 준비 중…" : !running ? "카메라 열기" : !camera ? `카메라 준비 사진 촬영 (${Math.min(views.length, 11)}/12)` : `${pointLabel} 촬영`}
  </button>;
  return (
    <section className="point-camera" style={{display:active?undefined:"none",width:"100%"}}>
      <video ref={video} autoPlay muted playsInline style={{width:"100%",maxHeight:320,display:running?"block":"none"}} />
      {!running && <p>카메라를 열어 촬영을 준비해 주세요.</p>}
      {running && !camera && <p>처음에는 마커 전체가 보이도록 위치와 기울기를 바꾸며 카메라를 준비해 주세요. ({Math.min(views.length, 11)}/12)</p>}
      {active && actionTarget && createPortal(action,actionTarget)}
      {running && <button type="button" className="text-button" disabled={busy||disabled} onClick={()=>{
        stop();setRunning(false);setCamera(null);onReady?.(null);setViews([]);
      }}>카메라 닫기</button>}
      {running && hint && <p role="status" className={`capture-hint ${hint.tone}`}>{hint.text}</p>}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
