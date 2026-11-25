import { createPortal } from "react-dom";
import { useEffect, useRef, useState } from "react";
import { acquireBurst, appendSetupView, captureGeometry, CaptureGeometryGuard } from "./burst";
import { detectFrame, setupCamera } from "./measureApi";
import { analyseImage } from "./captureCheck";
import { liveHint } from "./captureGuide";
import type { Hint } from "./captureGuide";
import type { Board, Camera, CaptureMarker, Frame } from "./measureApi";
import type { CapturePoint, Shape } from "./capturePlan";

export type CapturePhase = "idle" | "capturing" | "analyzing";
export type PointPlan = { shape: Shape; points: CapturePoint[]; corner: number; done: boolean[] };

/** The furniture's top drawn small, window side up: finished points white, the point to shoot now purple, the rest dashed. */
function PointMap({ plan }: { plan: PointPlan }) {
  const { shape, points, corner, done } = plan;
  // Push the drawing down only as far as needed to keep the window bar clear of the topmost point (a triangle's P1 sits right at the top).
  const TOP = Math.max(2, 23 - Math.min(...points.map((q) => q.y)));
  const xs = points.map((q) => q.x), ys = points.map((q) => q.y + TOP);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const size = Math.max(Math.max(...xs) - Math.min(...xs) + 26, Math.max(...ys) + 12);
  const bar = size * 0.42;
  return <div className="vision-map" aria-hidden="true">
    <svg viewBox={`${cx - size / 2} 0 ${size} ${size}`}>
      <rect className="map-window" x={cx - bar / 2} y="0" width={bar} height={size * 0.045} />
      <text className="map-window-label" x={cx + bar / 2 + 3} y={size * 0.075}>창문</text>
      {shape === "circle" ? <circle className="map-outline" cx="50" cy={50 + TOP} r="34" />
        : shape === "ellipse" ? <ellipse className="map-outline" cx="50" cy={50 + TOP} rx="40" ry="25" />
        : <polygon className="map-outline" points={points.map((q) => `${q.x},${q.y + TOP}`).join(" ")} />}
      {points.map((q, i) => {
        const now = i === corner, finished = !!done[i];
        return <g key={i} className={now ? "map-now" : finished ? "map-done" : "map-todo"}>
          <circle cx={q.x} cy={q.y + TOP} r={now ? 9 : 7.5} />
          <text x={q.x} y={q.y + TOP + 3.6} textAnchor="middle">{i + 1}</text>
        </g>;
      })}
    </svg>
  </div>;
}

/** Test-only video stream. Its calibration is deliberately separate from still-photo profiles. */
export default function BurstCamera({
  session,
  board,
  active,
  pointLabel,
  plan,
  actionTarget,
  disabled,
  getTicket,
  phase,
  onPhaseChange,
  onReady,
  onBatch,
}: {
  session: CaptureMarker;
  board: Board;
  active: boolean;
  pointLabel: string;
  plan?: PointPlan;
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
        board,
        session,
        new Date(file.lastModified).toISOString(),
      );
      if (version !== generation.current) return;
      if (frame.detections.length !== 1)
        throw new Error("마커 전체가 보이도록 각도를 조절해 주세요.");
      next = appendSetupView(views, frame);
      if (next.length >= 12) {
        const profile = await setupCamera(next, board, false);
        if (version === generation.current) {
          setViews(next);
          setCamera(profile);
          onReady?.(profile);
          setMessage("");
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
    const preview = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function inspect() {
      try {
        const file = await snapshot();
        preview.signal.throwIfAborted();
        const [frame, metrics] = await Promise.all([
          detectFrame(file, board, session, new Date(file.lastModified).toISOString(), camera ?? undefined, preview.signal),
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
      preview.abort();
    };
  }, [active, running, camera, busy, disabled, session, board]);
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
  const stageOpen = running && active;
  const phaseText = phase === "capturing" ? "촬영 중… 휴대폰 위치를 유지해 주세요." : phase === "analyzing" ? "촬영 완료! 휴대폰을 내려도 돼요. 사진 분석 중…" : "";
  function closeCamera() {
    stop();setRunning(false);setCamera(null);onReady?.(null);setViews([]);
  }
  const shutterLabel = phase === "analyzing" ? "사진 분석 중" : phase === "capturing" ? "촬영 중" : busy ? "카메라 준비 중" : !running ? "카메라 열기" : !camera ? `카메라 준비 사진 촬영 ${Math.min(views.length, 11)}/12` : `${pointLabel} 촬영`;
  const noteText = phaseText || (hint ? (hint.tone === "ok" ? `QR 마커가 잘 보여요. 표시된 ${pointLabel}에서 촬영해 주세요.` : hint.text) : camera ? `표시된 ${pointLabel}에서 QR 마커가 보이게 맞춰 주세요.` : `마커 전체가 보이도록 위치와 기울기를 바꾸며 찍어 주세요. (${Math.min(views.length, 11)}/12)`);
  const noteTone = phaseText ? "ok" : hint?.tone ?? "ok";
  return (
    <section className="point-camera" style={{display:active?undefined:"none",width:"100%"}}>
      {/* While the camera is open the stage fills the phone screen; closed, it is only the idle video element. */}
      <div className={`camera-stage${stageOpen ? " open vision-camera" : ""}`}>
        <video ref={video} className="camera-video" autoPlay muted playsInline style={{width:"100%",maxHeight:320,display:running?"block":"none"}} />
        {stageOpen && <>
          <div className="vision-top">
            <strong className="vision-title">마커로 정밀 촬영</strong>
            <button type="button" className="secondary vision-close" disabled={busy || disabled} onClick={closeCamera}>닫기</button>
          </div>
          <div className="vision-row">
            {plan && camera && <PointMap plan={plan} />}
          </div>
          <div className="vision-low">
            {message && <p role="alert" className="vision-error">{message}</p>}
            <p role="status" className={`capture-hint vision-note ${noteTone}`}>{noteText}</p>
          </div>
          <div className="vision-bottom">
            <button type="button" className="vision-shutter" aria-label={shutterLabel} aria-busy={busy || phase !== "idle"}
              disabled={busy || disabled || (running && !!camera && hint?.tone === "stop")}
              onClick={() => void (!running ? open() : !camera ? setup() : shoot())}><i aria-hidden="true" /></button>
          </div>
        </>}
      </div>
      {active && actionTarget && !stageOpen && createPortal(action,actionTarget)}
      {!stageOpen && message && <p role="status">{message}</p>}
    </section>
  );
}
