import test from "node:test";
import assert from "node:assert/strict";
import { BOARD, boardAlignment, filmBoardAlignment, serverMessage, startCaptureMarker, endCaptureMarker, measureAndRegister, setupCamera } from "./measureApi.ts";
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
    const id = JSON.parse(init.body as string).request_id;
    ids.push(id);
    if (ids.length === 1) throw new Error("lost response");
    return Response.json({...fake,session_id:id});
  });
  try {
    await assert.rejects(startCaptureMarker("scene"));
    const session = await startCaptureMarker("scene");
    assert.equal(ids[0],ids[1]);
    assert.deepEqual(session.board.ids_by_row,[[37]]);
    await endCaptureMarker(session);
    await startCaptureMarker("scene");
    assert.notEqual(ids[1],ids[2]);
  } finally { if(original) Object.defineProperty(globalThis,"sessionStorage",original); else Reflect.deleteProperty(globalThis,"sessionStorage"); }
});

test("setup and final measurement use the server-selected ID; mixed session photos are rejected", async t => {
  const board = {...BOARD,ids_by_row:[[37]]};
  const marker = {code_id:37,dictionary:"DICT_4X4_50",side_length_m:.6,plane:"window",T_ENU_marker:[-1,0,0,3,0,0,1,0,0,1,0,1,0,0,0,1]};
  const session = {session_id:"session",scene_id:"scene",window_id:"win",expires_at:"2030-01-01",display_status:"virtual_applied" as const,board,marker,film_cells:[]};
  const camera = {width:800,height:600,fx:600,fy:600,cx:400,cy:300,pixel_space:"raw_opencv" as const,distortion:[0,0,0,0,0],source:"test",lens_id:"test"};
  const recorded: {url:string;body:Record<string,unknown>}[]=[];
  const scene = {revision:1,room:{room_id:"room"},markers:[marker],registration_calibration:null};
  const room = {...space("south"),serverSceneId:"scene",site:{latitude:37,longitude:127,timezone:"Asia/Seoul",northConfirmed:true}};
  t.mock.method(globalThis,"fetch",async (url:string,init?:RequestInit)=>{
    const body = init?.body ? JSON.parse(init.body as string) : {};
    recorded.push({url,body});
    if(url.includes("capture-marker")) return Response.json(session);
    if(url.endsWith("automatic-setup")) return Response.json({camera});
    if(url.endsWith("/v1/measurements")) return Response.json({test_measurement:true});
    if(url.endsWith("/v1/fr06")) return Response.json({calibration_id:"reference",markers:[marker],occluders:[]});
    if(url.includes("/api/ui/spaces/")) return Response.json(room);
    return Response.json(scene);
  });
  await setupCamera([],board);
  const frame = {marker_session_id:"session",capture_id:"c",captured_at:"2026-11-08T00:00:00Z",width:800,height:600,base64:"",detections:[{marker_id:37,corners_px:[[0,0],[1,0],[1,1],[0,1]] as [number,number][]}]};
  const item = {session,name:"desk",shape:"rectangle",vertices:[],points:[{id:"P1",frames:[frame]}]};
  await measureAndRegister(room,camera,item);
  assert.deepEqual(recorded.find(r=>r.url.endsWith("automatic-setup"))!.body.board,board);
  assert.deepEqual(recorded.find(r=>r.url.endsWith("/v1/measurements"))!.body.board,board);
  recorded.length=0;
  await assert.rejects(measureAndRegister(room,camera,{...item,points:[{id:"P1",frames:[{...frame,marker_session_id:"old"}]}]}),/섞였어요/);
  assert.equal(recorded.some(r=>r.url.endsWith("/v1/measurements")),false);
});
