import test from "node:test";
import assert from "node:assert/strict";
import { BOARD, captureSelection, captureTargets, resumeCaptureMarker, cancelStoredCapture, boardAlignment, filmBoardAlignment, serverMessage, startCaptureMarker, checkCaptureMarker, endCaptureMarker, checkMeasurement, detectFrame, measureAndRegister, measurementProblem, measurementProblems, setupCamera } from "./measureApi.ts";
import { blankSpace } from "./model.ts";

const space = (wall: "south" | "north" | "east" | "west") => ({
  ...blankSpace(), width: 8, depth: 6, height: 3,
  windowDimensions: { width: 2, height: 1.5, wall, offset: 3, sill: 0.9, marker: { size: 0.3, x: 1, y: 0.75 } },
});
const apply = (T: number[], p: number[]) => [0, 1, 2].map((r) => T[r * 4] * p[0] + T[r * 4 + 1] * p[1] + T[r * 4 + 2] * p[2] + T[r * 4 + 3]);
const side = BOARD.marker_side_m;

test("the board sits on the window plane, centred on the stored marker centre, upright", () => {
  const expected = { south: [4, 0, 1.65], north: [4, 6, 1.65], east: [8, 2, 1.65], west: [0, 2, 1.65] };
  for (const wall of ["south", "north", "east", "west"] as const) {
    const pose = boardAlignment(space(wall))!;
    const T = pose.alignment.T_enu_board;
    const centre = apply(T, [side / 2, side / 2, 0]);
    centre.forEach((v, i) => assert.ok(Math.abs(v - expected[wall][i]) < 1e-9, `${wall} centre ${centre}`));
    // Board y runs down: one step along it lowers the point.
    assert.ok(apply(T, [0, 1, 0])[2] < apply(T, [0, 0, 0])[2]);
    assert.equal(pose.floor.board_origin_height_m, 1.65 + side / 2);
  }
});

test("server messages become next steps in Korean", () => {
  assert.match(serverMessage("Tilt the phone in both directions for automatic setup"), /기울여/);
  assert.match(serverMessage("something unexpected"), /다시 찍어/);
});

test("single film marker centre and axes round-trip through board alignment on all walls", () => {
  assert.deepEqual(BOARD.ids_by_row, [[0]]);
  assert.equal(BOARD.marker_side_m, .6);
  for (const wall of ["south", "north", "east", "west"] as const) {
    const expected = boardAlignment(space(wall))!;
    const b = expected.alignment.T_enu_board;
    const m = [...b];
    for (let r = 0; r < 3; r++) {
      m[r*4+1] = -b[r*4+1]; m[r*4+2] = -b[r*4+2];
      m[r*4+3] = b[r*4+3] + .3*b[r*4] + .3*b[r*4+1];
    }
    const marker = {code_id:0,dictionary:"DICT_4X4_50",side_length_m:.6,plane:"window",T_ENU_marker:m};
    filmBoardAlignment(marker).alignment.T_enu_board.forEach((v,i) => assert.ok(Math.abs(v-b[i])<1e-9));
    assert.throws(() => filmBoardAlignment({...marker,side_length_m:.24}));
    assert.throws(() => filmBoardAlignment({...marker,code_id:7}));
  }
});


test("random marker alignment uses the selected board, retaining strict ID matching", () => {
  const board = { ...BOARD, ids_by_row: [[37]] };
  const marker = {code_id:37,dictionary:"DICT_4X4_50",side_length_m:.6,plane:"window",T_ENU_marker:[-1,0,0,3,0,0,1,0,0,1,0,1,0,0,0,1]};
  assert.doesNotThrow(() => filmBoardAlignment(marker, board));
  assert.throws(() => filmBoardAlignment({...marker, code_id:0}, board));
});

test("capture start retries the persisted request ID and end releases it", async t => {
  const storage = new Map<string,string>();
  const original = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  Object.defineProperty(globalThis, "sessionStorage", {configurable:true,value:{
    getItem:(k:string)=>storage.get(k)??null, setItem:(k:string,v:string)=>storage.set(k,v), removeItem:(k:string)=>storage.delete(k),
  }});
  const ids: string[] = [];
  const fake = { session_id:"", scene_id:"scene", window_id:"win", expires_at:"2030-01-01", display_status:"virtual_applied" as const,
    board:{...BOARD,ids_by_row:[[37]]}, marker:{code_id:37,dictionary:"DICT_4X4_50",side_length_m:.6,plane:"window",T_ENU_marker:[]}, film_cells:[] };
  t.mock.method(globalThis,"fetch",async (url: string, init:RequestInit) => {
    if (url.endsWith("/end")) return Response.json({status:"ended"});
    const {request_id:id,...target} = JSON.parse(init.body as string);
    assert.deepEqual(target,{window_id:"win",module_row:1,module_col:2});
    ids.push(id);
    if (ids.length === 1) throw new Error("lost response");
    return Response.json({...fake,session_id:id});
  });
  try {
    const target = {window_id:"win",module_row:1,module_col:2};
    await assert.rejects(startCaptureMarker("scene",target));
    const session = await startCaptureMarker("scene",target);
    assert.equal(ids[0],ids[1]);
    assert.deepEqual(session.board.ids_by_row,[[37]]);
    await endCaptureMarker(session);
    await startCaptureMarker("scene",target);
    assert.notEqual(ids[1],ids[2]);
  } finally { if(original) Object.defineProperty(globalThis,"sessionStorage",original); else Reflect.deleteProperty(globalThis,"sessionStorage"); }
});

test("setup and final measurement use the server-selected ID; mixed session photos are rejected", async t => {
  const board = {...BOARD,ids_by_row:[[37]]};
  const marker = {code_id:37,dictionary:"DICT_4X4_50",side_length_m:.6,plane:"window",T_ENU_marker:[-1,0,0,3,0,0,1,0,0,1,0,1,0,0,0,1]};
  const session = {session_id:"session",scene_id:"scene",window_id:"win",expires_at:"2030-01-01",display_status:"virtual_applied" as const,board,marker,film_cells:[]};
  const camera = {width:800,height:600,fx:600,fy:600,cx:400,cy:300,pixel_space:"raw_opencv" as const,distortion:[0,0,0,0,0],source:"test",lens_id:"test"};
  const checkedText = '{"status":"model_ready_unreviewed","model":{"dimensions_m":[1.0,0.5,0.8]}}';
  const recorded: {url:string;body:Record<string,unknown>;raw?: string;method?:string}[]=[];
  const scene = {revision:1,room:{room_id:"room"},markers:[marker],registration_calibration:null};
  const room = {...space("south"),serverSceneId:"scene",site:{latitude:37,longitude:127,timezone:"Asia/Seoul",northConfirmed:true}};
  t.mock.method(globalThis,"fetch",async (url:string,init?:RequestInit)=>{
    const body = init?.body ? JSON.parse(init.body as string) : {};
    recorded.push({url,body,raw:init?.body as string | undefined,method:init?.method});
    if(url.includes("capture-marker")) return Response.json(session);
    if(url.endsWith("automatic-setup")) return Response.json({camera});
    if(url.endsWith("/v1/measurements")) return new Response(checkedText);
    if(url.endsWith("/v1/fr06")) return Response.json({calibration_id:"reference",markers:[marker],occluders:[]});
    if(url.includes("/api/ui/spaces/")) return Response.json(room);
    return Response.json(scene);
  });
  await setupCamera([],board);
  const frame = {marker_session_id:"session",capture_id:"c",captured_at:"2026-11-08T00:00:00Z",width:800,height:600,base64:"",detections:[{marker_id:37,corners_px:[[0,0],[1,0],[1,1],[0,1]] as [number,number][]}]};
  const item = {session,name:"desk",shape:"rectangle",vertices:[],points:[{id:"P1",frames:[frame]}]};
  const { checked, problems } = await checkMeasurement("scene", camera, item);
  assert.deepEqual(problems, []);
  assert.ok(checked);
  scene.revision = 7;
  await measureAndRegister(room,camera,{...item, checkedMeasurement: checked});
  assert.equal(recorded.filter(r=>r.url.endsWith("/v1/measurements")).length,1);
  assert.equal(recorded.find(r=>r.url.endsWith("/v1/fr06"))?.raw, `{"measurement":${checkedText}}`);
  assert.equal(recorded.find(r=>r.url.endsWith("/registrations"))?.body.expected_revision,8);
  await assert.rejects(measureAndRegister(room,camera,{...item, checkedMeasurement:{...checked,roomId:"other"}}),/촬영 기준/);
  assert.deepEqual(recorded.find(r=>r.url.endsWith("automatic-setup"))!.body.board,board);
  assert.deepEqual(recorded.find(r=>r.url.endsWith("/v1/measurements"))!.body.board,board);
  recorded.length=0;
  await assert.rejects(measureAndRegister(room,camera,{...item,points:[{id:"P1",frames:[{...frame,marker_session_id:"old"}]}]}),/섞였어요/);
  assert.equal(recorded.some(r=>r.url.endsWith("/v1/measurements")),false);

  // A cancelled preview session check must never proceed to image detection.
  const preview = new AbortController();
  const reason = new Error("Preview cancelled");
  t.mock.method(globalThis, "fetch", async (url: string, init?: RequestInit) => {
    assert.match(url, /capture-marker/);
    assert.equal(init?.signal, preview.signal);
    preview.abort(reason);
    throw reason;
  });
  await assert.rejects(
    detectFrame(new File([], "preview.jpg"), board, session, undefined, camera, preview.signal),
    error => error === reason,
  );
});

test("an incomplete measurement names each failed point and the reason instead of a generic error", () => {
  const bad = (status: string, points: Record<string, { status: string }>) => JSON.stringify({ status, points });
  const two = measurementProblems(bad("incomplete", { P1: { status: "ok" }, P2: { status: "insufficient_samples" }, P3: { status: "inconsistent_samples" } }));
  assert.deepEqual(two.map((p) => p.point), ["P2", "P3"]);
  assert.match(two[0].message, /^P2 · .*마커/);
  assert.match(two[1].message, /^P3 · .*어긋나요/);
  assert.equal(measurementProblems(bad("invalid_surface", { P1: { status: "ok" } }))[0].point, undefined);
  assert.match(measurementProblem(bad("incomplete", { P4: { status: "retake" } }))!, /^P4 · /);
  assert.deepEqual(measurementProblems('{"status":"model_ready_unreviewed","model":{"dimensions_m":[1,0.5,0.8]}}'), []);
  for (const response of [
    bad("incomplete", {}), '{"status":"incomplete"}',
    bad("incomplete", { P1: { status: "ok" } }), 'null', 'invalid JSON',
    '{"status":"model_ready_unreviewed","model":null}', '{"status":"model_ready_unreviewed","model":{}}', '{"status":"unknown"}',
  ]) {
    const [failure] = measurementProblems(response);
    assert.equal(failure?.retryable, true, response);
    assert.equal(failure.point, undefined);
  }
  assert.match(measurementProblem(bad("surface_measured", { P1: { status: "ok" } }))!, /높이/);
});

test("a 401 with a non-JSON body becomes the expired message and notifies", async (t) => {
  const { onUnauthorized } = await import("./auth.ts");
  let notified = 0;
  const off = onUnauthorized(() => notified++);
  t.mock.method(globalThis, "fetch", async () => new Response("<html>Unauthorized</html>", { status: 401 }));
  await assert.rejects(checkCaptureMarker({ scene_id: "scene-1", session_id: "s" } as never), (e: unknown) => (e as { status?: number }).status === 401 && (e as Error).message === "로그인이 만료됐어요. 다시 로그인해 주세요.");
  off();
  assert.equal(notified, 1);
});


test("capture selection lists modules, resumes without changing ID, and clears an expired session", async t => {
  const storage = new Map<string,string>();
  const original = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  Object.defineProperty(globalThis, "sessionStorage", {configurable:true,value:{
    getItem:(k:string)=>storage.get(k)??null, setItem:(k:string,v:string)=>storage.set(k,v), removeItem:(k:string)=>storage.delete(k),
  }});
  try {
    let expired = false;
    let creates = 0;
    const fake = {session_id:"test-session",scene_id:"scene"};
    t.mock.method(globalThis,"fetch",async (input: string, init?:RequestInit) => {
      const url = String(input);
      if (url.endsWith("/targets")) return Response.json({windows:[{window_id:"win",rows:2,cols:3}]});
      if (url.endsWith("/end")) return Response.json({status:"ended"});
      if (init?.method === "POST") { creates++; return Response.json(fake); }
      return expired ? Response.json({detail:"Expired"},{status:410}) : Response.json(fake);
    });
    assert.deepEqual(await captureTargets("scene"),[{window_id:"win",rows:2,cols:3}]);
    assert.equal(await resumeCaptureMarker("scene"),null);
    await startCaptureMarker("scene",{window_id:"win",module_row:1,module_col:2});
    assert.deepEqual(await resumeCaptureMarker("scene"),fake);
    assert.equal(creates,1);
    expired=true;
    assert.equal(await resumeCaptureMarker("scene"),null);
    assert.equal(storage.size,0);
    await startCaptureMarker("scene");
    await cancelStoredCapture("scene");
    assert.equal(storage.size,0);
  } finally {
    if(original) Object.defineProperty(globalThis,"sessionStorage",original);
    else Reflect.deleteProperty(globalThis,"sessionStorage");
  }
});


test("live video setup cannot overwrite the still-photo calibration",async t=>{
  const original=Object.getOwnPropertyDescriptor(globalThis,"localStorage");
  let writes=0;
  Object.defineProperty(globalThis,"localStorage",{configurable:true,value:{setItem:()=>writes++}});
  t.mock.method(globalThis,"fetch",async()=>Response.json({camera:{width:1920,height:1080}}));
  try{
    await setupCamera([],BOARD,false);
    assert.equal(writes,0);
    await setupCamera([],BOARD);
    assert.equal(writes,1);
  }finally{
    if(original)Object.defineProperty(globalThis,"localStorage",original);
    else Reflect.deleteProperty(globalThis,"localStorage");
  }
});


test("capture selection loads the window and module saved on another device", async t => {
  const selected = {window_id:"registered-window",module_row:1,module_col:2};
  t.mock.method(globalThis,"fetch",async (url:string) => {
    assert.equal(url,"/api/scenes/scene/capture-marker/targets");
    return Response.json({windows:[{window_id:"registered-window",rows:2,cols:3}],selected_target:selected});
  });
  const result = await captureSelection("scene");
  assert.deepEqual(result.selected_target,selected);
  assert.equal(result.windows[0].cols,3);
});
