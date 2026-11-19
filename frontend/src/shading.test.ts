import test from "node:test";
import assert from "node:assert/strict";
import { filmStatesAt, gatePlan, validateShadingPlan } from "./shading.ts";
import type { ShadingPlan } from "./shading.ts";
import { predictShadingPlan } from "./spaceApi.ts";
const start = "2026-11-02T00:00:00+09:00";
const at = (h: number) => new Date(Date.parse(start) + h * 3600000).toISOString();
const setting = { enabled: true, start: "08:00", end: "18:00" };
function plan(): ShadingPlan {
  return { plan_version: "0.1.0", scene_id: "scene", scene_revision: 3,
    start_time: start, end_time: at(24), windows: [{window_id:"window",film_rows:2,film_cols:3,
      cells: ["opaque", "infeasible", "unknown"].map((state, col) => ({row:0,col,segments:[{
        start_time:at(10),end_time:at(11),state:state as "opaque" | "infeasible" | "unknown",
        reasons:[{region_id:"r",grade:"B"}],
      }]}))}] };
}
test("cell indices and failure states come from FR02 without marking unknown as opaque", () => {
  const cells = filmStatesAt(plan(), 10, setting);
  assert.equal(cells.get(0)?.state, "opaque");
  assert.equal(cells.get(1)?.state, "infeasible");
  assert.equal(cells.get(2)?.state, "unknown");
  assert.equal(cells.has(3), false);
});
test("segments include start, exclude end and leave gaps transparent", () => {
  assert.equal(filmStatesAt(plan(), 9.99, setting).size, 0);
  assert.equal(filmStatesAt(plan(), 10, setting).size, 3);
  assert.equal(filmStatesAt(plan(), 11, setting).size, 0);
  assert.throws(() => filmStatesAt(plan(), 24, setting), /범위/);
});
test("existing user on/off and chosen hours still gate the film preview", () => {
  assert.equal(filmStatesAt(plan(),10,{...setting,enabled:false}).size,0);
  assert.equal(filmStatesAt(plan(),10,{...setting,start:"11:00"}).size,0);
  assert.equal(filmStatesAt(plan(),10,{...setting,end:"10:00"}).size,0);
});
test("empty successful plan is transparent; missing/old/wrong plans are never substituted", () => {
  const empty = {...plan(), windows:[]};
  assert.equal(filmStatesAt(validateShadingPlan(empty,"scene",3,start),10,setting).size,0);
  assert.throws(() => validateShadingPlan(plan(),"scene",4,start));
  assert.throws(() => validateShadingPlan(plan(),"other",3,start));
  assert.throws(() => validateShadingPlan(plan(),"scene",3,at(1)));
  const invalid = plan(); invalid.windows[0].cells[0].col=3;
  assert.throws(() => validateShadingPlan(invalid,"scene",3,start));
});
test("request uses shading endpoint and same saved revision/date/resolution as FR01", async t => {
  t.mock.method(globalThis,"fetch",async (url: string, init: RequestInit) => {
    assert.equal(url,"/api/scenes/scene/shading-plans");
    assert.deepEqual(JSON.parse(init.body as string),{start_time:start,duration_minutes:1440,interval_seconds:1800,expected_scene_revision:3});
    return new Response(JSON.stringify(plan()),{status:200});
  });
  assert.equal((await predictShadingPlan("scene",3,start)).scene_revision,3);
});
test("failed FR02 request rejects without returning a fake transparent plan", async t => {
  t.mock.method(globalThis,"fetch",async () => new Response(JSON.stringify({detail:"revision conflict"}),{status:409}));
  await assert.rejects(predictShadingPlan("scene",3,start));
});

test("gatePlan keeps only the stretches inside the block time, cutting at its edges", () => {
  const wide = plan();
  wide.windows[0].cells[0].segments[0] = { start_time: at(9), end_time: at(13), state: "opaque", reasons: [{ region_id: "r", grade: "A" }] };
  const gated = gatePlan(wide, { enabled: true, start: "10:00", end: "12:30" });
  const segs = gated.windows[0].cells.find((c) => c.col === 0)!.segments;
  assert.equal(segs.length, 1);
  assert.equal(Date.parse(segs[0].start_time), Date.parse(at(10)));
  assert.equal(Date.parse(segs[0].end_time), Date.parse(at(12.5)));
  assert.equal(filmStatesAt(gated, 9, { enabled: true, start: "00:00", end: "23:59" }).has(0), false);
});
test("gatePlan removes everything while the block time is off", () => {
  assert.equal(gatePlan(plan(), { enabled: false, start: "10:00", end: "16:00" }).windows[0].cells.length, 0);
});
test("gatePlan follows a furniture's own time instead of the space's", () => {
  const own = (id: string) => (id === "r" ? { enabled: true, start: "10:00", end: "11:00" } : undefined);
  assert.equal(gatePlan(plan(), { enabled: false, start: "10:00", end: "16:00" }, own).windows[0].cells.length, 3);
});
