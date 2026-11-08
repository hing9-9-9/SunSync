import test from "node:test";
import assert from "node:assert/strict";
import { blankSpace } from "./model.ts";
import { mergeStash, saveStash, splitForStash, stashKey, takeStash } from "./userStash.ts";
import type { TempDraft } from "./navigation.ts";

const memory = () => {
  const m = new Map<string, string>();
  return { m, getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) };
};
const saved = (name: string) => ({ ...blankSpace(), name, serverRevision: 2 });
const local = (name: string) => ({ ...blankSpace(), name });
const temp = (name: string): TempDraft => ({ space: local(name), page: "protect", savedAt: "2026-01-01T00:00:00Z" });

test("server copies are dropped; local spaces, temp drafts and the open draft are kept", () => {
  const draft = local("draft");
  const stash = splitForStash([saved("s"), local("l")], draft, [temp("t")]);
  assert.deepEqual(stash.spaces.map((s) => s.name), ["l"]);
  assert.deepEqual(stash.tempDrafts.map((t) => t.space.name), ["t", "draft"]);
  assert.equal(splitForStash([], draft, [{ space: draft, page: "review", savedAt: "x" }]).tempDrafts.length, 1);
});

test("stash is keyed by lowercased email, restored once, then removed", () => {
  const st = memory();
  saveStash(st, "A@Example.com", splitForStash([local("l")], null, [temp("t")]));
  assert.ok(st.m.has(stashKey("a@example.com")));
  assert.equal(takeStash(st, "b@example.com"), null);
  const back = takeStash(st, "a@EXAMPLE.com")!;
  assert.equal(back.spaces[0].name, "l");
  assert.equal(back.tempDrafts[0].space.name, "t");
  assert.equal(st.m.size, 0);
  assert.equal(takeStash(st, "a@example.com"), null);
});

test("empty emails and empty stashes store nothing; storage errors are swallowed", () => {
  const st = memory();
  saveStash(st, "", splitForStash([local("l")], null, []));
  saveStash(st, "a@x.com", { spaces: [], tempDrafts: [] });
  assert.equal(st.m.size, 0);
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => { throw new Error("blocked"); } };
  saveStash(broken, "a@x.com", splitForStash([local("l")], null, []));
  assert.equal(takeStash(broken, "a@x.com"), null);
});

test("merging keeps current items and adds parked ones without duplicates", () => {
  const a = local("a");
  const merged = mergeStash({ spaces: [a], tempDrafts: [] }, { spaces: [a, local("b")], tempDrafts: [temp("t")] });
  assert.deepEqual(merged.spaces.map((s) => s.name), ["a", "b"]);
  assert.equal(merged.tempDrafts.length, 1);
});
