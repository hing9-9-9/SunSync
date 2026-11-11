// What to tell the person while they shoot (live) and right after a point is shot (per point).
import { photoIssue, retakeMessages } from "./captureCheck.ts";

export type Hint = { text: string; tone: "ok" | "warn" | "stop" };
type Seen = { detections: { corners_px: [number, number][] }[]; width: number; height: number; pose_status?: string };

/** One line for the live preview. "stop" means no usable marker yet, so the shutter stays off. */
export function liveHint(frame: Seen, metrics?: { brightness: number; sharpness: number }): Hint {
  if (!frame.detections.length) return { tone: "stop", text: "마커가 인식되고 있지 않아요. 창문 필름의 마커 전체가 화면에 들어오게 해 주세요." };
  if (frame.detections.length > 1) return { tone: "stop", text: "마커가 여러 개 보여요. 이번 촬영의 마커만 보이게 해 주세요." };
  const xs = frame.detections[0].corners_px.map((c) => c[0]), ys = frame.detections[0].corners_px.map((c) => c[1]);
  const edge = 0.015 * Math.max(frame.width, frame.height);
  if (Math.min(...xs) < edge || Math.min(...ys) < edge || Math.max(...xs) > frame.width - edge || Math.max(...ys) > frame.height - edge)
    return { tone: "warn", text: "마커가 화면 가장자리에서 잘려요. 한 걸음 물러나 마커 네 모서리가 모두 보이게 해 주세요." };
  if (Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) < 0.12 * Math.max(frame.width, frame.height))
    return { tone: "warn", text: "마커가 너무 작게 보여요. 마커가 더 크게 보이도록 가까이 가 주세요." };
  const issue = metrics ? photoIssue({ ...metrics, marker: "visible" }) : null;
  if (issue) return { tone: "warn", text: retakeMessages[issue] };
  if (frame.pose_status && frame.pose_status !== "ok") return { tone: "warn", text: "마커 위치가 불안정해요. 폰을 고정하고 각도를 조금 바꿔 주세요." };
  return { tone: "ok", text: "마커가 잘 보여요. 촬영해 주세요." };
}

/** Why a point's burst was rejected, naming the point so only it is retaken. */
export function pointFailure(point: string, sel: { quality: { reason: string }; diagnostics?: { status: string }[] }): string {
  if (sel.quality.reason === "inconsistent_samples") return `${point} · 같은 지점 사진끼리 위치가 크게 어긋나요. 폰을 그 지점에 고정하고 다시 찍어 주세요.`;
  const d = sel.diagnostics ?? [];
  if (d.length && d.filter((x) => x.status === "pose_uncertainty_too_large").length * 2 >= d.length)
    return `${point} · 마커가 멀거나 비스듬해서 위치가 불안정해요. 마커가 크게, 정면에 가깝게 보이게 해서 다시 찍어 주세요.`;
  return `${point} · 마커가 또렷하게 잡힌 사진이 부족해요. 마커 전체가 보이게 해서 다시 찍어 주세요.`;
}
