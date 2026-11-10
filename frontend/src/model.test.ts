import test from "node:test";
import assert from "node:assert/strict";
import {
  blankSpace,
  validateShadingTime,
  filmGrid,
  hasUnsavedChanges,
  placeCapturedFurniture,
  readState,
  resolveWindow,
  regionForFurniture,
  removeZone,
  sampleFurniture,
  STORAGE_KEY,
  updateFurniture,
  upsertSpace,
  validateSpace,
  uid,
} from "./model.ts";
import type { Space } from "./model.ts";

test("space creation also works on HTTP phone previews without randomUUID", () => {
  const original = crypto.randomUUID;
  Object.defineProperty(crypto, "randomUUID", {
    value: undefined,
    configurable: true,
  });
  try {
    const first = uid();
    assert.equal(first.length, 32);
    assert.notEqual(first, uid());
  } finally {
    Object.defineProperty(crypto, "randomUUID", {
      value: original,
      configurable: true,
    });
  }
});

function fixture() {
  const space = {
    ...blankSpace(),
    building: "도서관",
    floor: "1층",
    name: "열람실",
    furniture: sampleFurniture(),
  };
  space.regions = [regionForFurniture(space.furniture[0])];
  return space;
}
test("sample measurements can be saved; user dimension corrections follow the attached protection face", () => {
  const space = fixture();
  assert.equal(validateSpace(space), null);
  const next = updateFurniture(space, {
    ...space.furniture[0],
    x: 1,
    width: 1.5,
    height: 1.2,
  });
  assert.equal(next.regions[0].width, 1.5);
  assert.equal(next.regions[0].x, 1);
  assert.equal(next.regions[0].top, 1.2);
  assert.equal(next.revision, space.revision + 1);
  assert.equal(validateSpace(next), null);
});
test("reject invalid furniture dimensions and out-of-room placement", () => {
  const space = fixture();
  for (const width of [0, -1, NaN, Infinity, 99]) {
    const next = updateFurniture(space, { ...space.furniture[0], width });
    assert.notEqual(validateSpace(next), null);
  }
});
test("reject inverted or excessive protection heights and missing hosts", () => {
  const space = fixture();
  for (const patch of [
    { bottom: 2, top: 1 },
    { top: 5 },
    { bottom: -1 },
    { furnitureId: "missing" },
  ]) {
    assert.notEqual(
      validateSpace({ ...space, regions: [{ ...space.regions[0], ...patch }] }),
      null,
    );
  }
});
test("editing a saved space updates it without duplicating other spaces", () => {
  const space = fixture();
  const other = { ...fixture(), id: "other" };
  const saved = upsertSpace([space, other], { ...space, name: "수정 열람실" });
  assert.equal(saved.length, 2);
  assert.equal(saved[0].name, "수정 열람실");
  assert.equal(saved[1], other);
});
test("reload restores account and spaces; logout preserves saved spaces", () => {
  const space = fixture();
  const storage = {
    getItem: (key: string) =>
      key === STORAGE_KEY
        ? JSON.stringify({
            version: 1,
            account: { name: "사용자", email: "sun@example.com" },
            spaces: [space],
          })
        : null,
  };
  assert.equal(readState(storage).account?.email, "sun@example.com");
  assert.equal(readState(storage).spaces[0].name, "열람실");
  assert.equal(
    readState({
      getItem: () =>
        JSON.stringify({ version: 1, account: null, spaces: [space] }),
    }).spaces.length,
    1,
  );
});
test("corrupt or incompatible local data falls back to a safe initial state", () => {
  for (const raw of ["broken", "null", '{"version":2,"spaces":[]}'])
    assert.deepEqual(readState({ getItem: () => raw }), {
      version: 1,
      account: null,
      spaces: [],
    });
});

test("zones keep furniture and protection references in the same zone", () => {
  const space = fixture();
  const extra = sampleFurniture("구역 02");
  const next = {
    ...space,
    zones: [...space.zones, "구역 02"],
    furniture: [...space.furniture, ...extra],
    regions: [...space.regions, regionForFurniture(extra[0])],
  };
  assert.equal(validateSpace(next), null);
  assert.notEqual(validateSpace({ ...next, zones: ["구역 01"] }), null);
  assert.notEqual(
    validateSpace({
      ...next,
      regions: [{ ...next.regions[0], zone: "구역 02" }],
    }),
    null,
  );
});

test("one corrupt stored space does not erase other valid spaces", () => {
  const space = fixture();
  const state = readState({
    getItem: () =>
      JSON.stringify({
        version: 1,
        account: null,
        spaces: [{ ...space, regions: [null] }, space],
      }),
  });
  assert.equal(state.spaces.length, 1);
});

test("deleting a zone removes its contents, preserves other zones, and keeps at least one", () => {
  const space = blankSpace();
  const first = space.zone;
  space.zones.push("구역 02");
  space.furniture = [...sampleFurniture(first), ...sampleFurniture("구역 02")];
  space.regions = space.furniture.map((f) => regionForFurniture(f));
  const otherFurniture = space.furniture.filter((f) => f.zone === "구역 02");
  const next = removeZone(space, first);
  assert.deepEqual(next.zones, ["구역 02"]);
  assert.equal(next.zone, "구역 02");
  assert.deepEqual(next.furniture, otherFurniture);
  assert.ok(next.regions.every((r) => r.zone === "구역 02"));
  assert.equal(next.revision, space.revision + 1);
  assert.equal(removeZone(next, "구역 02"), next);
  assert.equal(space.zones.length, 2);
});

test("window position defaults keep older spaces valid and centre the window", () => {
  const space = fixture();
  space.windowDimensions = { width: 4, height: 2 };
  assert.equal(validateSpace(space), null);
  const win = resolveWindow(space)!;
  assert.equal(win.wall, "south");
  assert.equal(win.offset, (space.width - 4) / 2);
  assert.deepEqual(win.marker, { size: 0.6, x: 2, y: 1 });
  assert.deepEqual(filmGrid(win), { cols: 40, rows: 20 });
});

test("window and marker must stay on the wall and inside the window", () => {
  const space = fixture();
  space.windowDimensions = { width: 4, height: 2, wall: "east", offset: 3 };
  assert.match(validateSpace(space)!, /동쪽 벽/);
  space.windowDimensions = { width: 4, height: 2, sill: 1.5 };
  assert.match(validateSpace(space)!, /천장/);
  space.windowDimensions = { width: 4, height: 2, marker: { size: 0.4, x: 0.1, y: 1 } };
  assert.match(validateSpace(space)!, /QR 마커/);
});

test("unknown furniture types are rejected but a missing type is allowed", () => {
  const space = fixture();
  delete space.furniture[0].type;
  assert.equal(validateSpace(space), null);
  space.furniture[0].type = "sofa" as never;
  assert.match(validateSpace(space)!, /가구 종류/);
});

test("captured furniture is placed inside the room with its type", () => {
  const space = fixture();
  const items = placeCapturedFurniture(space, [
    { name: "책장 A", type: "bookcase" },
    { name: "책상 A", type: "desk" },
    { name: "책장 B", type: "bookcase" },
    { name: "진열장", type: "display" },
  ]);
  assert.deepEqual(items.map((f) => f.type), ["bookcase", "desk", "bookcase", "display"]);
  space.furniture = items;
  space.regions = [];
  assert.equal(validateSpace(space), null);
  assert.ok(items[3].y > items[0].y, "wraps to the next row when the wall is full");
});

test("a stored window that breaks newer rules keeps the space and its data", () => {
  const space = fixture();
  space.windowDimensions = { width: 4, height: 0 };
  const storage = { getItem: () => JSON.stringify({ version: 1, account: null, spaces: [space] }) };
  const [restored] = readState(storage).spaces;
  assert.equal(restored.id, space.id);
  assert.deepEqual(restored.windowDimensions, { width: 4, height: 0 });
  assert.match(validateSpace(restored)!, /창문/);
});

test("a saved space reopened without edits has no unsaved changes", () => {
  const draft = { ...blankSpace(), name: "열람실", windowDimensions: { width: 2, height: 1.5 } };
  const saved = { ...structuredClone(draft), revision: draft.revision + 1, savedAt: "2026-10-22T10:00:00Z", serverRevision: 3, windowDimensions: { width: 2, height: 1.5, marker: null } } as unknown as Space;
  assert.equal(hasUnsavedChanges(null, [saved]), false);
  assert.equal(hasUnsavedChanges(draft, []), true);
  assert.equal(hasUnsavedChanges(draft, [saved]), false);
  assert.equal(hasUnsavedChanges({ ...draft, name: "자료실" }, [saved]), true);
});

test("blocking time is the user's choice and must run forwards", () => {
  assert.equal(validateShadingTime(undefined), null);
  assert.equal(validateShadingTime({ enabled: false, start: "18:00", end: "09:00" }), null);
  assert.equal(validateShadingTime({ enabled: true, start: "09:00", end: "18:00" }), null);
  assert.match(validateShadingTime({ enabled: true, start: "18:00", end: "09:00" }) ?? "", /늦어야/);
  assert.match(validateShadingTime({ enabled: true, start: "12:00", end: "12:00" }) ?? "", /늦어야/);
  assert.match(validateShadingTime({ enabled: true, start: "", end: "12:00" }) ?? "", /입력/);
  const s = blankSpace();
  s.building = "도서관"; s.floor = "1층"; s.name = "열람실";
  s.shadingSchedule = { enabled: true, start: "15:00", end: "10:00" };
  assert.match(validateSpace(s) ?? "", /늦어야/);
});

test("new furniture starts with a grade-D region, existing regions stay", async () => {
  const { withDefaultRegions, blankSpace } = await import("./model.ts");
  const space = { ...blankSpace(), regions: [] };
  const f = { id: "f1", name: "책상", zone: space.zone, type: "desk" as const, x: 0, y: 0, width: 1, depth: 0.6, height: 0.75 };
  const regions = withDefaultRegions(space, [f]);
  assert.equal(regions.length, 1);
  assert.equal(regions[0].grade, "D");
  assert.equal(regions[0].furnitureId, "f1");
  assert.equal(withDefaultRegions({ ...space, regions }, [f]).length, 1);
});

test("furniture lists go A to D, then by name, with no region counting as D", async () => {
  const { byGradeThenName } = await import("./model.ts");
  const rows = [
    { name: "책장 10", regions: [] },
    { name: "책장 2", regions: [{ grade: "B" as const }] },
    { name: "가구", regions: [{ grade: "D" as const }, { grade: "A" as const }] },
    { name: "책장 1", regions: [{ grade: "B" as const }] },
    { name: "다", regions: [] },
  ];
  assert.deepEqual(byGradeThenName(rows).map((r) => r.name), ["가구", "책장 1", "책장 2", "다", "책장 10"]);
});

test("grade counts match the furniture list: no region counts as D, a piece counts once", async () => {
  const { gradeCounts } = await import("./model.ts");
  const r = (furnitureId: string | null, grade: "A" | "B" | "C" | "D") => ({ furnitureId, grade });
  const counts = gradeCounts({
    furniture: [{ id: "a" }, { id: "b" }, { id: "c" }] as never,
    regions: [r("a", "C"), r("a", "A"), r(null, "B")] as never,
  });
  assert.deepEqual(counts, { A: 1, B: 1, C: 0, D: 2 });
});

test("overlapping 3D furniture in the same zone is rejected with both names, touching edges allowed", async () => {
  const {
    validateFurnitureOverlap,
    findOpenFurnitureSpot,
  } = await import("./model.ts");
  const space = {
    ...fixture(),
    width: 8,
    depth: 6,
    height: 3,
    regions: [],
    furniture: [
      {
        id: "f-table",
        name: "테이블",
        zone: "구역 01",
        type: "desk" as const,
        x: 5.8,
        y: 2.5,
        width: 1.6,
        depth: 0.8,
        height: 0.75,
      },
      {
        id: "f-case",
        name: "진열장",
        zone: "구역 01",
        type: "display" as const,
        x: 6.8,
        y: 3.0,
        width: 1.0,
        depth: 0.5,
        height: 1.5,
      },
    ],
  };
  const msg = validateSpace(space);
  assert.ok(msg);
  assert.match(msg, /테이블/);
  assert.match(msg, /진열장/);
  assert.match(msg, /겹칩니다/);

  //Already-saved space with overlapping furniture is preserved by readState and surfaces overlap warning
  const storage = { getItem: () => JSON.stringify({ version: 1, account: null, spaces: [space] }) };
  const [restored] = readState(storage).spaces;
  assert.equal(restored.id, space.id);
  assert.match(validateFurnitureOverlap(restored) ?? "", /테이블.*진열장/);

  // Touching boundary (x = 5.8 + 1.6 = 7.4 -> wait, inside 8m room, x = 4.8 + 1.0 = 5.8) is allowed
  space.furniture[1].x = 4.8;
  assert.equal(validateSpace(space), null);

  // findOpenFurnitureSpot picks a non-overlapping position when (0, 0) is occupied
  space.furniture[0].x = 0;
  space.furniture[0].y = 0;
  const spot = findOpenFurnitureSpot(space, 1.0, 0.8, 1.2);
  space.furniture.push({
    id: "f-new",
    name: "새 가구",
    zone: "구역 01",
    type: "cabinet",
    x: spot.x,
    y: spot.y,
    width: 1.0,
    depth: 0.8,
    height: 1.2,
  });
  assert.equal(validateSpace(space), null);
});

test("editor example space passes validateSpace without overlap", async () => {
  const { exampleState } = await import("./exampleSpace.ts");
  const { spaces } = exampleState();
  assert.equal(spaces.length, 1);
  const sample = spaces[0];
  assert.equal(validateSpace(sample), null);
  const { validateFurnitureOverlap } = await import("./model.ts");
  assert.equal(validateFurnitureOverlap(sample), null);
});


test("rotated footprints preserve dimensions, clamp corners and reject real overlaps", async () => {
  const {furnitureCorners,moveFurnitureInside,furnitureOverlaps3D,localFrontFace}=await import("./model.ts");
  const f={id:"a",name:"desk",zone:"z",x:1,y:1,width:2,depth:1,height:1,yaw:90};
  const corners=furnitureCorners(f);
  assert.ok(Math.abs(Math.min(...corners.map(p=>p.x))-1.5)<1e-9);
  assert.ok(Math.abs(Math.max(...corners.map(p=>p.y))-2.5)<1e-9);
  const moved=moveFurnitureInside({...f,x:-1,y:-1},{width:5,depth:5});
  assert.ok(furnitureCorners(moved).every(p=>p.x>=-1e-9 && p.y>=-1e-9));
  assert.equal(moved.width,2);
  assert.equal(furnitureOverlaps3D(f,{...f,id:"b",x:1.5,yaw:30}),true);
  assert.equal(furnitureOverlaps3D(f,{...f,id:"b",baseHeight:1}),false);
  assert.equal(localFrontFace({...f,yaw:30},"south"),"남쪽 전면");
});


test("hosted protection near a wall uses the rotated furniture bounds", () => {
  const space = fixture();
  space.furniture = [{...space.furniture[0],x:-0.7,y:2,width:2,depth:.2,yaw:90}];
  space.regions = [regionForFurniture(space.furniture[0])];
  assert.equal(validateSpace(space),null);
});
