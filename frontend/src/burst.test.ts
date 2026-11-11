import test from "node:test";
import assert from "node:assert/strict";
import { acquireBurst } from "./burst.ts";

test("one action captures ordered independent frames and awaits each shutter", async () => {
  let active = false,
    n = 0;
  const frames = await acquireBurst(
    async () => {
      assert.equal(active, false);
      active = true;
      await Promise.resolve();
      active = false;
      return ++n;
    },
    { count: 5, intervalMs: 0 },
  );
  assert.deepEqual(frames, [1, 2, 3, 4, 5]);
});
test("cancellation discards in-flight burst rather than attaching it to a new point", async () => {
  const controller = new AbortController();
  let n = 0;
  await assert.rejects(
    acquireBurst(
      async () => {
        n++;
        controller.abort();
        return n;
      },
      { signal: controller.signal, intervalMs: 0 },
    ),
  );
  assert.equal(n, 1);
});
test("burst size is bounded and camera failure cannot produce a completed burst", async () => {
  await assert.rejects(acquireBurst(async () => 1, { count: 100 }));
  await assert.rejects(
    acquireBurst(
      async () => {
        throw new Error("camera changed");
      },
      { intervalMs: 0 },
    ),
    /camera changed/,
  );
});


test("capture geometry ignores frame-rate, exposure, and continuous autofocus fluctuations", async () => {
  const { captureGeometry, CaptureGeometryGuard } = await import("./burst.ts");
  const guard = new CaptureGeometryGuard();
  const initial = {deviceId:"rear",facingMode:"environment",frameRate:30,exposureTime:10,focusDistance:0.8,zoom:1.0};
  assert.equal(guard.accept(captureGeometry(initial,1920,1080)),true);
  assert.equal(guard.accept(captureGeometry({...initial,frameRate:29.97,exposureTime:20,focusDistance:1.45,zoom:1.002},1920,1080)),true);
});

test("actual geometry changes invalidate once and allow setup again", async () => {
  const { captureGeometry, CaptureGeometryGuard } = await import("./burst.ts");
  for (const changed of [
    captureGeometry({deviceId:"front"},1920,1080),
    captureGeometry({deviceId:"rear"},1080,1920),
    captureGeometry({deviceId:"rear",zoom:2},1920,1080),
  ]) {
    const guard = new CaptureGeometryGuard();
    assert.equal(guard.accept(captureGeometry({deviceId:"rear"},1920,1080)),true);
    assert.equal(guard.accept(changed),false);
    assert.equal(guard.accept(changed),true);
    guard.reset();
    assert.equal(guard.accept(captureGeometry({deviceId:"rear"},1920,1080)),true);
  }
});
