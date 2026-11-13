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

/** The room from the home page picture (8 × 6 m, bookcase, reading table, cabinet), given to an account that has no space yet. */
export function homeExampleSpace(): Space {
  const furniture: Furniture[] = [
    { id: uid(), name: "책장 01", type: "bookcase", zone: "구역 01", x: 5.7, y: 0.6, width: 2, depth: 0.6, height: 1.8 },
    { id: uid(), name: "열람 테이블 02", type: "table", zone: "구역 01", x: 3, y: 2.6, width: 1.6, depth: 0.8, height: 0.75 },
    { id: uid(), name: "수납장 03", type: "cabinet", zone: "구역 01", x: 0.6, y: 3.6, width: 0.9, depth: 0.5, height: 1 },
  ];
  const space: Space = {
    ...blankSpace(),
    building: "SunSync 도서관",
    floor: "2층",
    name: "예시 열람실",
    furniture,
    windowDimensions: { width: 3, height: 1.5, wall: "south", offset: 2.5, sill: 1 },
    site: { latitude: 37.57, longitude: 126.98, timezone: "Asia/Seoul", northConfirmed: true },
  };
  return { ...space, regions: [{ ...regionForFurniture(furniture[0]), grade: "A" as const }, ...furniture.slice(1).map((f) => regionForFurniture(f))] };
}
