import { blankSpace, sampleFurniture, regionForFurniture, uid } from "./model.ts";
import type { Furniture, SavedState, Space } from "./model.ts";

export const editorExample =
  typeof location !== "undefined" &&
  new URLSearchParams(location.search).get("example") === "editor";
export function exampleState(): SavedState {
  const furniture = sampleFurniture();
  const space = {
    ...blankSpace(),
    id: "editor-example",
    building: "SunSync 도서관",
    name: "2층 열람실",
    floor: "2층",
    furniture,
    regions: [{ ...regionForFurniture(furniture[0]), grade: "B" as const }],
    site: { latitude: 37.6, longitude: 127, timezone: "Asia/Seoul", northConfirmed: true },
  };
  return { version: 1, account: { name: "SunSync 사용자", email: "preview@example.com" }, spaces: [space] };
}

export const exampleBuilding = "SunSync 도서관", exampleRoomName = "예시 열람실";
/** The given example room (until the person renames it): the 3D scene adds a standing person to it so it reads as a real room. */
export const isExampleRoom = (s: Pick<Space, "building" | "name">) => s.building === exampleBuilding && s.name === exampleRoomName;
// A full-wall glass window (통창): the whole south wall, floor to just under the ceiling.
export const exampleWindow = { width: 8, height: 2.8, wall: "south" as const, offset: 0, sill: 0 };
const furnish = (name: string, type: Furniture["type"], x: number, y: number, width: number, depth: number, height: number): Furniture =>
  ({ id: uid(), name, type, zone: "구역 01", x, y, width, depth, height });
const chair = (name: string, x: number, y: number, yaw: number): Furniture => ({ ...furnish(name, "chair", x, y, 0.45, 0.45, 0.85), yaw });
/** The first three pieces: a bookcase and a cabinet on the walls and a table in the middle (only the bookcase protected). */
const firstPieces = (): Furniture[] => [
  furnish("책장 01", "bookcase", 5.7, 0, 2, 0.6, 1.8), // both bookcases touch the north wall (y = 0)
  furnish("열람 테이블 02", "table", 3, 2.6, 1.6, 0.8, 0.75),
  furnish("수납장 03", "cabinet", 0.6, 3.6, 0.9, 0.5, 1),
];
/** What was added to make the room feel lived in: a second bookcase, a display case and two chairs facing each other across the table. */
const extraPieces = (): Furniture[] => [
  furnish("책장 04", "bookcase", 1.4, 0, 1.6, 0.6, 1.8),
  furnish("진열장 05", "display", 0.6, 1.9, 1.2, 0.5, 1),
  chair("의자 06", 3.2, 2.12, 0), // north of the table, facing south
  chair("의자 07", 3.2, 3.45, 180), // south of the table, facing north
];
/** Where the standing person is drawn (plan metres): in front of the first bookcase, looking at the window. */
export const examplePerson = { x: 6.7, y: 1.8 };
/** An example room from an earlier version (no second bookcase) becomes the current room: window, furniture and regions are replaced. */
export function refreshExample(s: Space): Space | null {
  if (!isExampleRoom(s) || s.furniture.some((f) => f.name === "책장 04")) return null;
  const fresh = homeExampleSpace();
  return { ...s, windowDimensions: exampleWindow, furniture: fresh.furniture, regions: fresh.regions };
}
export function homeExampleSpace(): Space {
  const furniture = [...firstPieces(), ...extraPieces()];
  const space: Space = {
    ...blankSpace(),
    building: exampleBuilding,
    floor: "2층",
    name: exampleRoomName,
    furniture,
    windowDimensions: exampleWindow,
    site: { latitude: 37.57, longitude: 126.98, timezone: "Asia/Seoul", northConfirmed: true },
  };
  return { ...space, regions: [{ ...regionForFurniture(furniture[0]), grade: "A" as const }, ...furniture.slice(1).map((f) => regionForFurniture(f))] };
}
