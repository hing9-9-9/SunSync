import test from "node:test";
import assert from "node:assert/strict";
import { BOARD, boardAlignment, filmBoardAlignment, serverMessage } from "./measureApi.ts";
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
