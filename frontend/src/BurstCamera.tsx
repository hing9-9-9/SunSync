import { useEffect, useRef, useState } from "react";
import { acquireBurst } from "./burst";
import { detectFrame, setupCamera } from "./measureApi";
import type { Camera, CaptureMarker, Frame } from "./measureApi";

/** Test-only video stream. Its calibration is deliberately separate from still-photo profiles. */
export default function BurstCamera({
  session,
  disabled,
  getTicket,
  onBusy,
  onBatch,
}: {
  session: CaptureMarker;
  disabled: boolean;
  getTicket: () => number;
  onBusy: (busy: boolean) => void;
  onBatch: (files: File[], camera: Camera, ticket: number) => Promise<void>;
}) {
  const video = useRef<HTMLVideoElement>(null),
    stream = useRef<MediaStream | null>(null);
  const controller = useRef<AbortController | null>(null),
    generation = useRef(0);
  const [running, setRunning] = useState(false),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const [views, setViews] = useState<Frame[]>([]),
    [camera, setCamera] = useState<Camera | null>(null);
  const identity = useRef("");
  function stop() {
    generation.current++;
    controller.current?.abort();
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  }
  useEffect(() => () => stop(), []);
  function signature() {
    const track = stream.current?.getVideoTracks()[0];
    return JSON.stringify(track?.getSettings());
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
      identity.current = signature();
      setViews([]);
      setCamera(null);
      setRunning(true);
      if (video.current) {
        video.current.srcObject = media;
        await video.current.play();
      }
      media.getVideoTracks()[0].addEventListener("ended", () => {
        setCamera(null);
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
  async function snapshot() {
    if (signature() !== identity.current) {
      setCamera(null);
      setViews([]);
      throw new Error("촬영 설정이 바뀌었어요. 카메라를 다시 준비해 주세요.");
    }
    const v = video.current;
    if (!v || v.readyState < 2 || !v.videoWidth)
      throw new Error("카메라 화면이 준비될 때까지 기다려 주세요.");
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
    return new File([blob], `frame-${captured}.jpg`, {
      type: "image/jpeg",
      lastModified: captured,
    });
  }
  async function setup() {
    setBusy(true);
    setMessage("");
    const version = generation.current;
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
      const next = [...views, frame].slice(-30);
      setViews(next);
      if (next.length >= 12) {
        const profile = await setupCamera(next, session.board, false);
        if (version === generation.current) setCamera(profile);
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (!running || !camera || busy || disabled) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    async function inspect() {
      try {
        const file = await snapshot();
        const frame = await detectFrame(
          file,
          session.board,
          session,
          new Date(file.lastModified).toISOString(),
          camera!,
        );
        if (live)
          setMessage(
            frame.pose_status === "ok"
              ? "마커가 보여요. 현재 지점에서 연사해 주세요."
              : "마커 관측이 불안정해요. 지점 위치를 유지하고 마커 전체가 크게 보이도록 각도를 조절해 주세요.",
          );
      } catch (e) {
        if (live) setMessage(e instanceof Error ? e.message : String(e));
      }
      if (live) timer = setTimeout(() => void inspect(), 1500);
    }
    timer = setTimeout(() => void inspect(), 500);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [running, camera, busy, disabled, session]);
  async function shoot() {
    if (!camera || busy || disabled) return;
    setBusy(true);
    onBusy(true);
    setMessage("");
    const version = generation.current;
    const ticket = getTicket();
    controller.current = new AbortController();
    try {
      const files = await acquireBurst(snapshot, {
        signal: controller.current.signal,
      });
      if (version === generation.current) await onBatch(files, camera, ticket);
    } catch (e) {
      if (version === generation.current)
        setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <section className="burst-camera">
      <video
        ref={video}
        autoPlay
        muted
        playsInline
        style={{
          width: "100%",
          maxHeight: 320,
          display: running ? "block" : "none",
        }}
      />
      {!running ? (
        <button
          type="button"
          disabled={busy || disabled}
          onClick={() => void open()}
        >
          연사 카메라 열기
        </button>
      ) : (
        <>
          {!camera ? (
            <>
              <p>
                연사 영상은 사진 모드와 카메라 설정이 달라 별도로 준비해요.
                위치·기울기를 바꾸며 12~30개 시점을 촬영해 주세요. (
                {views.length}/12)
              </p>
              <button
                type="button"
                disabled={busy || disabled}
                onClick={() => void setup()}
              >
                {busy ? "확인 중…" : "카메라 준비 사진 촬영"}
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={busy || disabled}
              onClick={() => void shoot()}
            >
              {busy ? "연사·관측 확인 중…" : "현재 지점 10장 연사"}
            </button>
          )}
          <button
            type="button"
            disabled={busy || disabled}
            onClick={() => {
              stop();
              setRunning(false);
              setCamera(null);
              setViews([]);
            }}
          >
            카메라 닫기
          </button>
        </>
      )}
      {message && <p role="alert">{message}</p>}
    </section>
  );
}
