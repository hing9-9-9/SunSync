import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { readHandoff, userMessage } from "./spaceApi";
import type { HandoffSession } from "./spaceApi";
import type { Space } from "./model";
import { browserCaptureEnabled } from "./captureEnvironment";
export function DeviceHandoff({
  session,
  onComplete,
  design,
}: {
  session: HandoffSession;
  design?: "award";
  onComplete: (space: Space) => void;
}) {
  const [status, setStatus] = useState(session.status),
    [error, setError] = useState("");
  const [base] = useState(
    import.meta.env.VITE_PUBLIC_URL || location.origin,
  );
  const [qr, setQr] = useState("");
  let link = "",
    valid = false;
  try {
    const url = new URL(base);
    valid =
      url.protocol === "https:" &&
      !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    url.pathname = "/";
    url.search = new URLSearchParams({
      mode: "app",
      handoff: session.token,
      ...(design ? { example: design } : {}),
    }).toString();
    url.hash = "/capture";
    link = url.toString();
  } catch {
    /* Input is validated below. */
  }
  useEffect(() => {
    let cancelled = false;
    setQr("");
    if (valid)
      void QRCode.toDataURL(link, { width: 256, margin: 2 }).then((url) => {
        if (!cancelled) setQr(url);
      });
    return () => {
      cancelled = true;
    };
  }, [link, valid]);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const data = await readHandoff(session.token, controller.signal);
        if (stopped) return;
        setError("");
        setStatus(data.status);
        if (data.status === "completed" && data.space) {
          onComplete(data.space);
          return;
        }
      } catch (e) {
        if (stopped) return;
        setError(userMessage(e, "휴대폰 연결 상태를 확인하지 못했습니다. 잠시 후 다시 확인합니다."));
      }
      if (!stopped) timer = setTimeout(poll, 2500);
    }
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [session.token, onComplete]);
  return (
    <section className="device-handoff" aria-label="휴대폰 촬영 연결">
      <h3>
        {status === "completed"
          ? "측정 완료 · 도면 확인"
          : status === "connected"
            ? "측정 진행 중 · 결과 대기"
            : "측정 대기"}
      </h3>
      <p>같은 계정으로 로그인한 앱의 내 공간에서 ‘촬영 시작’ 선택</p>
      {browserCaptureEnabled && valid && <details><summary>테스트용 촬영 화면 열기</summary>
        {qr && <img src={qr} width="224" height="224" alt="공간 촬영 화면 바로가기 QR" />}
        <a className="secondary handoff-link" href={link} target="_blank" rel="noreferrer">촬영 화면 열기</a>
      </details>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
