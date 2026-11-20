import { photoIssue } from "./captureCheck.ts";

/** What is known about one frame of the burst: marker count from detection, sharpness and brightness from the pixels. */
export type ShotCandidate = { markers: number; sharpness: number; brightness: number };

/** How many frames the one-photo camera takes before picking the best one. */
export const VISION_BURST_COUNT = 5;

/**
 * Index of the frame to send for the one-photo furniture search, or -1 when there is none.
 * A frame with exactly one marker comes first (the server needs it), one with no photo issue before that,
 * then the sharpest; later frames win ties because the phone has settled by then.
 */
export function pickBestFrame(frames: ShotCandidate[]): number {
  let best = -1, bestKey: [number, number] = [-1, -1];
  frames.forEach((f, i) => {
    const tier = f.markers === 1 ? (photoIssue({ brightness: f.brightness, sharpness: f.sharpness, marker: "visible" }) ? 1 : 2) : 0;
    const key: [number, number] = [tier, f.sharpness];
    if (key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] >= bestKey[1])) { best = i; bestKey = key; }
  });
  return best;
}
