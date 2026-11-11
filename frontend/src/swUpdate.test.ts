import assert from "node:assert/strict";
import test from "node:test";
import { CHECK_EVERY_MS, entryScript, pageIsOutdated, shouldCheckAgain, watchForNewVersion } from "./swUpdate.ts";

function container(controller: unknown) {
  const listeners: Array<() => void> = [];
  return {
    controller,
    addEventListener: (_type: "controllerchange", fn: () => void) => void listeners.push(fn),
    change: () => listeners.forEach((fn) => fn()),
  };
}

test("a worker that replaces the running one is announced once", () => {
  const sw = container({});
  let n = 0;
  watchForNewVersion(sw, () => n++);
  sw.change();
  sw.change();
  assert.equal(n, 1);
});

test("the first worker of a page that had none is not a new version", () => {
  const sw = container(null);
  let n = 0;
  watchForNewVersion(sw, () => n++);
  sw.change();
  assert.equal(n, 0);
});

test("a page that stays open checks again only after the interval", () => {
  assert.equal(shouldCheckAgain(1000, 1000 + CHECK_EVERY_MS - 1), false);
  assert.equal(shouldCheckAgain(1000, 1000 + CHECK_EVERY_MS), true);
});

const PAGE = (src: string) =>
  `<html><head><link rel="modulepreload" href="/assets/x.js"><script type="module" crossorigin src="${src}"></script></head></html>`;

test("the entry script is read from the index.html", () => {
  assert.equal(entryScript(PAGE("/assets/index-AAA.js")), "/assets/index-AAA.js");
  assert.equal(entryScript("<html></html>"), null);
});

test("a page is outdated only when the server starts another entry script", async () => {
  assert.equal(await pageIsOutdated("/assets/index-AAA.js", async () => PAGE("/assets/index-BBB.js")), true);
  assert.equal(await pageIsOutdated("/assets/index-AAA.js", async () => PAGE("/assets/index-AAA.js")), false);
});

test("nothing is announced when the check itself fails or the page has no script", async () => {
  const fails = async () => {
    throw new Error("offline");
  };
  assert.equal(await pageIsOutdated("/assets/index-AAA.js", fails), false);
  assert.equal(await pageIsOutdated(null, async () => PAGE("/assets/index-BBB.js")), false);
  assert.equal(await pageIsOutdated("/assets/index-AAA.js", async () => "<html></html>"), false);
});
