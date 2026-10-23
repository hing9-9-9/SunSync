import test from "node:test";
import assert from "node:assert/strict";
import {
  shouldConfirmNavigation,
  locationKey,
  locationUrl,
  readLocation,
  resolveLocation,
} from "./navigation.ts";
import { blankSpace, sampleFurniture } from "./model.ts";

test("guide steps and individual spaces have distinct browser history locations", () => {
  const base = {
    page: "guide" as const,
    guideIndex: 0,
    activeId: null,
    draft: null,
  };
  assert.equal(locationUrl(base), "#/guide/1");
  assert.notEqual(locationKey(base), locationKey({ ...base, guideIndex: 1 }));
  assert.notEqual(
    locationKey({ ...base, page: "detail", activeId: "a" }),
    locationKey({ ...base, page: "detail", activeId: "b" }),
  );
});
test("typing in a form does not create a new browser history entry", () => {
  const draft = blankSpace();
  const route = {
    page: "register" as const,
    guideIndex: null,
    activeId: null,
    draft,
  };
  assert.equal(
    locationKey(route),
    locationKey({ ...route, draft: { ...draft, name: "열람실" } }),
  );
});
test("Back restores the screen while keeping the latest edits to the same draft", () => {
  const draft = { ...blankSpace(), furniture: sampleFurniture() };
  const latest = {
    ...draft,
    name: "수정한 공간",
    furniture: draft.furniture.map((f) => ({ ...f, width: 1.5 })),
  };
  const target = {
    page: "review" as const,
    guideIndex: null,
    activeId: null,
    draft,
  };
  const resolved = resolveLocation(target, true, latest, []);
  assert.equal(resolved.page, "review");
  assert.equal(resolved.draft?.name, "수정한 공간");
  assert.equal(resolved.draft?.furniture[0].width, 1.5);
});
test("logout and unavailable drafts never restore a blank or protected screen", () => {
  const route = {
    page: "protect" as const,
    guideIndex: null,
    activeId: null,
    draft: null,
  };
  assert.equal(resolveLocation(route, true, null, []).page, "home");
  assert.equal(
    resolveLocation({ ...route, draft: blankSpace() }, false, null, []).page,
    "home",
  );
  assert.equal(
    resolveLocation(
      { ...route, page: "detail", activeId: "missing" },
      true,
      null,
      [],
    ).page,
    "spaces",
  );
});
test("URL reading tolerates invalid routes, malformed identifiers, and invalid guide steps", () => {
  assert.equal(readLocation(null, "#/unknown").page, "home");
  assert.equal(readLocation(null, "#/detail/%zz").activeId, null);
  assert.equal(readLocation(null, "#/guide/99").guideIndex, null);
  assert.equal(readLocation(null, "#/guide/3").guideIndex, 2);
});

test("guide navigation asks before leaving an unsaved editor", () => {
  assert.equal(shouldConfirmNavigation(true, "protect", "guide"), true);
  assert.equal(shouldConfirmNavigation(true, "register", "guide"), true);
  assert.equal(shouldConfirmNavigation(false, "detail", "guide"), false);
  assert.equal(shouldConfirmNavigation(true, "protect", "protect"), false);
});
