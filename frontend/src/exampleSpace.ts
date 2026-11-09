import { blankSpace, sampleFurniture, regionForFurniture } from "./model.ts";
import type { SavedState } from "./model.ts";

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
