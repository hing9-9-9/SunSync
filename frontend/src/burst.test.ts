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
