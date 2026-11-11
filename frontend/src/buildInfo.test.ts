import assert from "node:assert/strict";
import test from "node:test";
import { buildLabel } from "./buildInfo.ts";

test("the label shows the build id", () => assert.equal(buildLabel(" abc1234 "), "버전 abc1234"));
test("a build without an id says it is a development build", () => {
  assert.equal(buildLabel(undefined), "버전 개발용");
  assert.equal(buildLabel(""), "버전 개발용");
});
