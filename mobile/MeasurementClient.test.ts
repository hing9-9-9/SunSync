import assert from "node:assert/strict";
import test from "node:test";
import { MeasurementClient } from "./MeasurementClient.ts";
import type { NativePayload } from "./MeasurementClient.ts";

function frame(id = "one"): NativePayload {
  return { capture_id: id, point_id: "P1", captured_at: "2026-11-05T10:00:00Z", stream_id: "stream", lens_id: "back",
    width: 800, height: 600, pixel_rotation_degrees: 0, mirrored: false,
    image_base64: "native-jpeg", calibration: null, board: { board_id: "board" } };
}
const camera = { width: 800, height: 600, lens_id: "back" };
test("missing device metadata requests automatic setup, never accepts uncalibrated furniture", async () => {
  const client = new MeasurementClient("https://service", async () => Response.json({ camera: null, detections: [] }));
  const result = await client.capture(frame());
  assert.equal(result.status, "setup_required");
});
test("native calibrated capture preserves shutter-time point identity", async () => {
  const client = new MeasurementClient("https://service", async () => Response.json({ camera, detections: [], pose: { status: "ok" } }));
  const result = await client.capture(frame());
  assert.equal(result.status, "accepted");
  if (result.status === "accepted") {
    assert.equal(result.capture.point_id, "P1");
    assert.equal(result.capture.captured_at, frame().captured_at);
  }
  assert.equal((await client.capture(frame())).status, "retake");
});
test("reset invalidates an in-flight native response", async () => {
  let release!: (value: Response) => void;
  const pending = new Promise<Response>(resolve => { release = resolve; });
  const client = new MeasurementClient("https://service", async () => pending);
  const capture = client.capture(frame());
  client.reset();
  release(Response.json({ camera, detections: [], pose: { status: "ok" } }));
  assert.equal((await capture).status, "retake");
});
test("automatic setup completes separately from furniture capture and is invalidated on lens change", async () => {
  const paths: string[] = [];
  let n = 0;
  const client = new MeasurementClient("https://service", async (url) => {
    paths.push(String(url));
    if (String(url).endsWith("automatic-setup")) return Response.json({ camera, stream_id: "stream" });
    if (String(url).endsWith("/v1/detect")) return Response.json({ detections: [], pose: { status: "ok" } });
    n++;
    return Response.json({ camera: null, detections: [0].map(marker_id => ({ marker_id,
      corners_px: [[n * 10, 1], [n * 10 + 5, 1], [n * 10 + 5, 6], [n * 10, 6]] })) });
  });
  for (let i = 0; i < 11; i++) assert.equal((await client.capture(frame(String(i)))).status, "setup_required");
  assert.equal((await client.capture(frame("11"))).status, "setup_complete");
  assert.equal((await client.capture(frame("12"))).status, "accepted");
  assert.ok(paths.at(-1)?.endsWith("/v1/detect"));
  assert.equal((await client.capture({ ...frame("13"), lens_id: "other" })).status, "setup_required");
  assert.ok(paths.at(-1)?.endsWith("/v1/mobile/captures"));
});
