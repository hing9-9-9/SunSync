import assert from "node:assert/strict";
import test from "node:test";
import { allowBrowserCapture } from "./capturePolicy.ts";

test("only the mobile screen captures: it is the app until a native one ships", () => {
  assert.equal(allowBrowserCapture(true), true);
  assert.equal(allowBrowserCapture(false), false);
});
