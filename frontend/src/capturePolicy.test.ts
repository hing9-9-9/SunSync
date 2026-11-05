import assert from "node:assert/strict";
import test from "node:test";
import { allowBrowserCapture } from "./capturePolicy.ts";

test("production cannot enable browser capture, even with the test flag", () => {
  assert.equal(allowBrowserCapture(false, "true"), false);
  assert.equal(allowBrowserCapture(false, undefined), false);
});
test("local capture tests require explicit opt-in", () => {
  assert.equal(allowBrowserCapture(true, undefined), false);
  assert.equal(allowBrowserCapture(true, "false"), false);
  assert.equal(allowBrowserCapture(true, "true"), true);
});
test("the mobile screen captures in every build: it is the app until a native one ships", () => {
  assert.equal(allowBrowserCapture(false, undefined, true), true);
  assert.equal(allowBrowserCapture(false, undefined, false), false);
});
