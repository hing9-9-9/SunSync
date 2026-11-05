/** Framework-independent native-app orchestration. Never load from the production web UI. */
export type NativePayload = {
  capture_id: string; point_id: string; captured_at: string; stream_id: string;
  lens_id: string; width: number; height: number; image_base64: string;
  board: Record<string, unknown>; calibration: unknown | null;
  pixel_rotation_degrees: 0; mirrored: false;
};
type Camera = { width: number; height: number; lens_id: string; [key: string]: unknown };
type Detection = { marker_id: number; corners_px: number[][] };
type Observation = {
  capture_id: string; stream_id: string; lens_id: string; width: number; height: number;
  detections: Detection[];
};
type DetectionResult = { camera?: Camera; detections: Detection[]; pose?: { status: string } };
export type CaptureOutcome =
  | { status: "setup_required"; message: string; acceptedViews: number }
  | { status: "setup_complete"; message: string }
  | { status: "retake"; message: string }
  | { status: "accepted"; capture: Record<string, unknown> };

export class MeasurementClient {
  private profile: Camera | null = null;
  private identity = "";
  private generation = 0;
  private views: Observation[] = [];
  private seen = new Set<string>();
  private busy = false;
  private baseURL: string;
  private fetcher: typeof fetch;
  constructor(baseURL: string, fetcher: typeof fetch = fetch) {
    this.baseURL = baseURL; this.fetcher = fetcher;
  }

  reset() {
    this.generation++;
    this.identity = ""; this.profile = null; this.views = []; this.seen.clear();
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const response = await this.fetcher(this.baseURL.replace(/\/$/, "") + path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`Measurement request failed: ${response.status}`);
    return response.json() as Promise<T>;
  }

  /** A serialized native shutter pipeline prevents late frames attaching to the next point.
   * The host still validates the returned capture_id/point_id against its active step ticket.
   */
  async capture(frame: NativePayload): Promise<CaptureOutcome> {
    if (this.busy) return { status: "retake", message: "현재 사진 처리가 끝난 뒤 다시 촬영해 주세요." };
    const identity = JSON.stringify([frame.stream_id, frame.lens_id, frame.width, frame.height, frame.board]);
    if (identity !== this.identity) { this.reset(); this.identity = identity; }
    if (this.seen.has(frame.capture_id)) return { status: "retake", message: "새 사진을 촬영해 주세요." };
    this.busy = true;
    this.seen.add(frame.capture_id);
    const generation = this.generation;
    try {
      let result: DetectionResult;
      if (frame.calibration === null && this.profile) {
        result = await this.post<DetectionResult>("/v1/detect", {
          camera: this.profile, board: frame.board, image_base64: frame.image_base64,
        });
        result.camera = this.profile;
      } else {
        result = await this.post<DetectionResult>("/v1/mobile/captures", frame);
      }
      if (generation !== this.generation) return { status: "retake", message: "카메라가 변경됐어요. 다시 촬영해 주세요." };
      if (!result.camera) {
        const flattened = (detections: Detection[]) => [...detections].sort((a, b) => a.marker_id - b.marker_id).flatMap(d => d.corners_px.flat());
        const incoming = flattened(result.detections);
        const repeated = this.views.some(view => {
          const previous = flattened(view.detections);
          return previous.length === incoming.length && Math.sqrt(incoming.reduce((sum, n, i) => sum + (n - previous[i]) ** 2, 0) / incoming.length) < 5;
        });
        if (result.detections.length === 4 && !repeated) {
          this.views.push({ capture_id: frame.capture_id, stream_id: frame.stream_id, lens_id: frame.lens_id,
            width: frame.width, height: frame.height, detections: result.detections });
          this.views = this.views.slice(-30);
        }
        if (this.views.length >= 12) {
          try {
            const setup = await this.post<{ camera: Camera; stream_id: string }>("/v1/mobile/automatic-setup", {
              board: frame.board, views: this.views,
            });
            if (generation !== this.generation || setup.stream_id !== frame.stream_id) throw new Error("Stale camera setup");
            this.profile = setup.camera;
            this.views = [];
            return { status: "setup_complete", message: "카메라 준비가 끝났어요. 첫 모서리부터 촬영해 주세요." };
          } catch {
            // Keep sampling; a failed setup never becomes a zero-distortion camera.
          }
        }
        return { status: "setup_required", acceptedViews: this.views.length,
          message: "마커 네 개가 모두 보이도록 화면의 가운데와 가장자리, 좌우·위아래 기울인 각도로 천천히 비춰 주세요." };
      }
      if (result.pose?.status !== "ok") return { status: "retake", message: "마커가 선명하게 보이도록 잠시 멈춰 다시 촬영해 주세요." };
      return { status: "accepted", capture: {
        capture_id: frame.capture_id, point_id: frame.point_id, captured_at: frame.captured_at,
        camera: result.camera, detections: result.detections, camera_to_point_m: [0, 0, 0],
      } };
    } catch {
      return { status: "retake", message: "사진을 처리하지 못했어요. 연결 상태를 확인하고 다시 촬영해 주세요." };
    } finally { this.busy = false; }
  }
}
