export type Grade = "A" | "B" | "C" | "D";
export type Face =
  "남쪽 전면" | "북쪽 후면" | "동쪽 측면" | "서쪽 측면" | "상단";
export type FurnitureType =
  | "bookcase"
  | "desk"
  | "table"
  | "cabinet"
  | "display"
  | "other";
export type Furniture = {
  id: string;
  name: string;
  type?: FurnitureType;
  zone: string;
  x: number;
  y: number;
  width: number;
  depth: number;
  height: number;
};
export type Region = {
  id: string;
  name: string;
  zone: string;
  furnitureId: string | null;
  x: number;
  y: number;
  width: number;
  depth: number;
  face: Face;
  bottom: number;
  top: number;
  grade: Grade;
};
export type ShadingTime = { enabled: boolean; start: string; end: string };
// The user chooses when light may be blocked; until they do, nothing is blocked.
export const defaultShadingTime: ShadingTime = { enabled: false, start: "10:00", end: "16:00" };
export function timeToMinute(value: string): number | null {
  const m = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(value);
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}
export function validateShadingTime(t: ShadingTime | undefined): string | null {
  if (!t || !t.enabled) return null;
  const start = timeToMinute(t.start), end = timeToMinute(t.end);
  if (start === null || end === null) return "차단 시작·종료 시간을 입력해 주세요.";
  if (start >= end) return "차단 종료 시간은 시작 시간보다 늦어야 합니다.";
  return null;
}
export type Wall = "south" | "north" | "east" | "west";
// Marker centre and size are relative to the window's lower-left corner, seen from inside.
export type WindowMarker = { size: number; x: number; y: number };
export type WindowSpec = {
  width: number;
  height: number;
  wall?: Wall;
  offset?: number;
  sill?: number;
  marker?: WindowMarker;
};
export type Space = {
  // The time range in which the user wants light blocked (saved with the space).
  shadingSchedule?: ShadingTime;
  id: string;
  building: string;
  floor: string;
  name: string;
  width: number;
  depth: number;
  height: number;
  zone: string;
  windowDimensions?: WindowSpec;
  zones: string[];
  furniture: Furniture[];
  regions: Region[];
  savedAt?: string;
  source: "sample" | "manual";
  revision: number;
  predictedRevision?: number;
  site?: {
    latitude: number;
    longitude: number;
    timezone: string;
    northConfirmed: boolean;
  };
  serverRevision?: number;
  serverSceneId?: string;
};
export type Account = { name: string; email: string };
export type SavedState = {
  version: 1;
  account: Account | null;
  spaces: Space[];
};
export const STORAGE_KEY = "sunsync-ui-v1";
export const grades: Record<Grade, string> = {
  A: "차단 100% · 암막 커튼을 친 정도",
  B: "차단 70% · 블라인드를 내린 정도",
  C: "차단 30% · 얇은 커튼을 친 정도",
  D: "차단 0% · 창문을 그대로 둔 정도",
};
export const furnitureTypes: Record<FurnitureType, string> = {
  bookcase: "책장",
  desk: "책상",
  table: "열람 테이블",
  cabinet: "수납장",
  display: "진열장",
  other: "기타",
};
export const walls: Record<Wall, string> = {
  south: "남쪽 벽",
  north: "북쪽 벽",
  east: "동쪽 벽",
  west: "서쪽 벽",
};
// Film cells are 10 cm squares (requirement document, 1 m window = 10 × 10 cells).
export const CELL_SIZE = 0.1;
// South and north walls run along the room's width (plan x); east and west along its depth (y).
export const isWidthWall = (wall: Wall) => wall === "south" || wall === "north";
export const wallLength = (s: Pick<Space, "width" | "depth">, wall: Wall) =>
  isWidthWall(wall) ? s.width : s.depth;
// Derived window margins are rounded to the millimetre so float noise never reaches the inputs.
export const margin = (m: number) => Math.round(m * 1000) / 1000;
// Fills the optional fields of spaces saved before window position existed.
export function resolveWindow(s: Space): Required<WindowSpec> | null {
  const w = s.windowDimensions;
  if (!w || !(w.width > 0) || !(w.height > 0)) return null;
  const wall = w.wall ?? "south";
  const size = Math.min(0.3, w.width, w.height);
  return {
    width: w.width,
    height: w.height,
    wall,
    offset: w.offset ?? Math.max(0, (wallLength(s, wall) - w.width) / 2),
    sill: w.sill ?? 0,
    marker: w.marker ?? { size, x: w.width / 2, y: w.height / 2 },
  };
}
export function filmGrid(w: Pick<WindowSpec, "width" | "height">) {
  return {
    cols: Math.max(1, Math.round(w.width / CELL_SIZE)),
    rows: Math.max(1, Math.round(w.height / CELL_SIZE)),
  };
}
export const faces: Face[] = [
  "남쪽 전면",
  "북쪽 후면",
  "동쪽 측면",
  "서쪽 측면",
  "상단",
];
export const uid = () => {
  // randomUUID is unavailable on a phone's local-network HTTP preview.
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) =>
    n.toString(16).padStart(2, "0"),
  ).join("");
};
export const clamp = (n: number, min: number, max: number) =>
  Math.max(min, Math.min(max, n));
export const blankSpace = (): Space => ({
  id: uid(),
  building: "",
  floor: "",
  name: "",
  width: 8,
  depth: 6,
  height: 3,
  zone: "구역 01",
  zones: ["구역 01"],
  furniture: [],
  regions: [],
  source: "manual",
  revision: 1,
});
export function sampleFurniture(zone = "구역 01"): Furniture[] {
  return [0, 1, 2].map((i) => ({
    id: uid(),
    name: `책장 ${String(i + 1).padStart(2, "0")}`,
    type: "bookcase" as const,
    zone,
    x: Number((0.45 + i * 2.45).toFixed(2)),
    y: 0.8,
    width: 2,
    depth: 0.6,
    height: 1.8,
  }));
}
// Example sizes per type until FR-03 returns measured corners; users correct them on the plan.
const typicalSize: Record<FurnitureType, [number, number, number]> = {
  bookcase: [2, 0.6, 1.8],
  desk: [1.2, 0.6, 0.75],
  table: [1.8, 0.9, 0.75],
  cabinet: [1, 0.5, 1.2],
  display: [1.2, 0.6, 1],
  other: [1, 0.6, 1],
};
// frames: photos prepared and checked by the measurement server (absent when measuring is skipped).
export type CapturedFurniture = { name: string; type: FurnitureType; capture?: {shape: string; points: {id: string; position: {x:number;y:number}; photos: File[]; frames?: import("./measureApi").Frame[]}[]} };
// Lays captured furniture out in rows inside the room, clamped to its size.
export function placeCapturedFurniture(
  space: Pick<Space, "width" | "depth" | "height" | "zone">,
  items: CapturedFurniture[],
  existing: Furniture[] = [],
): Furniture[] {
  const start = { x: 0.4, y: 0.8 };
  const below = existing.filter((f) => f.zone === space.zone);
  let x = start.x;
  let y = below.reduce((low, f) => Math.max(low, f.y + f.depth + 0.6), start.y);
  return items.map((item) => {
    const [w, d, h] = typicalSize[item.type];
    const width = Math.min(w, space.width);
    const depth = Math.min(d, space.depth);
    if (x > start.x && x + width > space.width) {
      x = start.x;
      y += 1.4;
    }
    const f: Furniture = {
      id: uid(),
      name: item.name,
      type: item.type,
      zone: space.zone,
      x: Number(clamp(x, 0, space.width - width).toFixed(2)),
      y: Number(clamp(y, 0, space.depth - depth).toFixed(2)),
      width,
      depth,
      height: Math.min(h, space.height),
    };
    x += width + 0.45;
    return f;
  });
}
// New furniture starts at grade D (no extra blocking): every piece shows a grade, raised to A–C when needed.
export function regionForFurniture(f: Furniture, grade: Region["grade"] = "D"): Region {
  return {
    id: uid(),
    name: `${f.name} 보호 영역`,
    zone: f.zone,
    furnitureId: f.id,
    x: f.x,
    y: f.y,
    width: f.width,
    depth: f.depth,
    face: "남쪽 전면",
    bottom: 0,
    top: f.height,
    grade,
  };
}
/** Gives each listed furniture without a region its default grade-D region. */
export function withDefaultRegions(s: Space, furniture: Furniture[]): Region[] {
  const hosted = new Set(s.regions.map((r) => r.furnitureId));
  return [...s.regions, ...furniture.filter((f) => !hosted.has(f.id)).map((f) => regionForFurniture(f))];
}
export function validateSpace(s: Space): string | null {
  if (
    s.site &&
    (!Number.isFinite(s.site.latitude) ||
      !Number.isFinite(s.site.longitude) ||
      s.site.latitude < -90 ||
      s.site.latitude > 90 ||
      s.site.longitude < -180 ||
      s.site.longitude > 180)
  )
    return "건물의 위도·경도를 확인해 주세요.";
  if (![s.building, s.floor, s.name, s.zone].every((v) => v.trim()))
    return "건물, 층, 공간과 구역 이름을 입력해 주세요.";
  if (
    !s.zones.includes(s.zone) ||
    new Set(s.zones).size !== s.zones.length ||
    s.zones.some((z) => !z.trim())
  )
    return "구역 이름은 중복 없이 입력해 주세요.";
  if (![s.width, s.depth, s.height].every((n) => Number.isFinite(n) && n > 0))
    return "공간 치수는 0보다 큰 숫자로 입력해 주세요.";
  const windowMessage = validateWindow(s);
  if (windowMessage) return windowMessage;
  const timeMessage = validateShadingTime(s.shadingSchedule);
  if (timeMessage) return timeMessage;
  for (const f of s.furniture) {
    if (!s.zones.includes(f.zone)) return "가구의 구역 연결을 확인해 주세요.";
    if (!f.name.trim()) return "가구 이름을 입력해 주세요.";
    if (f.type != null && !(f.type in furnitureTypes))
      return `${f.name}의 가구 종류를 선택해 주세요.`;
    if (![f.width, f.depth, f.height].every((n) => Number.isFinite(n) && n > 0))
      return `${f.name}의 가로·세로·높이를 확인해 주세요.`;
    if (
      ![f.x, f.y].every(Number.isFinite) ||
      f.x < 0 ||
      f.y < 0 ||
      f.x + f.width > s.width + 0.001 ||
      f.y + f.depth > s.depth + 0.001 ||
      f.height > s.height
    )
      return `${f.name}가 공간 경계를 벗어났습니다.`;
  }
  for (const r of s.regions) {
    if (!s.zones.includes(r.zone)) return "보호 영역의 구역 연결을 확인해 주세요.";
    if (!r.name.trim() || !faces.includes(r.face) || !(r.grade in grades))
      return "보호 영역 이름과 등급을 확인해 주세요.";
    const host = s.furniture.find((f) => f.id === r.furnitureId);
    if (r.furnitureId && (!host || host.zone !== r.zone))
      return "보호 영역의 가구 연결을 확인해 주세요.";
    if (
      ![r.x, r.y, r.width, r.depth, r.bottom, r.top].every(Number.isFinite) ||
      r.width <= 0 ||
      r.depth <= 0 ||
      r.x < 0 ||
      r.y < 0 ||
      r.x + r.width > s.width + 0.001 ||
      r.y + r.depth > s.depth + 0.001
    )
      return "보호 영역이 공간 경계를 벗어났습니다.";
    if (r.bottom < 0 || r.top <= r.bottom || r.top > (host?.height ?? s.height))
      return `${r.name}의 보호 높이를 확인해 주세요.`;
  }
  return null;
}
export function validateWindow(s: Space): string | null {
  const w = s.windowDimensions;
  if (!w) return null;
  if (![w.width, w.height].every((n) => Number.isFinite(n) && n > 0))
    return "창문 가로와 높이를 0보다 큰 숫자로 입력해 주세요.";
  if (w.wall != null && !(w.wall in walls))
    return "창문이 있는 벽을 선택해 주세요.";
  const r = resolveWindow(s);
  if (!r) return null;
  if (![r.offset, r.sill].every((n) => Number.isFinite(n) && n >= 0))
    return "창문 위치는 0 이상의 숫자로 입력해 주세요.";
  if (r.offset + r.width > wallLength(s, r.wall) + 0.001)
    return `창문이 ${walls[r.wall]} 길이를 벗어났습니다. 왼쪽·오른쪽 여백과 창문 가로를 확인해 주세요.`;
  if (r.sill + r.height > s.height + 0.001)
    return "창문이 천장 높이를 벗어났습니다. 위·아래 여백과 창문 높이를 확인해 주세요.";
  const m = r.marker;
  if (
    ![m.size, m.x, m.y].every(Number.isFinite) ||
    m.size <= 0 ||
    m.x - m.size / 2 < -0.001 ||
    m.y - m.size / 2 < -0.001 ||
    m.x + m.size / 2 > r.width + 0.001 ||
    m.y + m.size / 2 > r.height + 0.001
  )
    return "QR 마커가 창문 밖으로 나갔습니다. 마커 크기와 위치를 확인해 주세요.";
  return null;
}
export function updateFurniture(s: Space, updated: Furniture): Space {
  return {
    ...s,
    revision: s.revision + 1,
    furniture: s.furniture.map((f) => (f.id === updated.id ? updated : f)),
    regions: s.regions.map((r) =>
      r.furnitureId === updated.id
        ? {
            ...r,
            x: updated.x,
            y: updated.y,
            width: updated.width,
            depth: updated.depth,
            bottom: Math.min(r.bottom, Math.max(0, updated.height - 0.01)),
            top:
              r.face === "상단"
                ? updated.height
                : Math.min(r.top, updated.height),
          }
        : r,
    ),
  };
}
// Save stamps change on every save, the selected zone is only what is being looked at, and the
// server returns null where the UI leaves a field out.
const SAVE_STAMPS = new Set(["savedAt", "revision", "serverRevision", "serverSceneId", "predictedRevision", "zone"]);
function comparable(value: unknown, top = false): unknown {
  if (Array.isArray(value)) return value.map((v) => comparable(v));
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([k, v]) => v != null && !(top && SAVE_STAMPS.has(k)))
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => [k, comparable(v)]),
  );
}
export function hasUnsavedChanges(draft: Space | null, spaces: Space[]): boolean {
  if (!draft) return false;
  const saved = spaces.find((s) => s.id === draft.id);
  return !saved || JSON.stringify(comparable(draft, true)) !== JSON.stringify(comparable(saved, true));
}
export function upsertSpace(spaces: Space[], space: Space): Space[] {
  const next = { ...space, savedAt: new Date().toISOString() };
  return spaces.some((s) => s.id === next.id)
    ? spaces.map((s) => (s.id === next.id ? next : s))
    : [...spaces, next];
}
export function readState(storage: Pick<Storage, "getItem">): SavedState {
  try {
    const s = JSON.parse(storage.getItem(STORAGE_KEY) ?? "null");
    if (s?.version !== 1 || !Array.isArray(s.spaces))
      throw new Error("invalid");
    const spaces = s.spaces.filter((x: Space) => {
      try {
        return (
          typeof x?.id === "string" &&
          Array.isArray(x.furniture) &&
          Array.isArray(x.regions) &&
          Array.isArray(x.zones) &&
          // A window that breaks newer rules is fixed on save; it must not hide the whole space.
          validateSpace({ ...x, windowDimensions: undefined }) === null
        );
      } catch {
        return false;
      }
    });
    const account =
      s.account &&
      typeof s.account.email === "string" &&
      typeof s.account.name === "string"
        ? s.account
        : null;
    return { version: 1, account, spaces };
  } catch {
    return { version: 1, account: null, spaces: [] };
  }
}

export function removeZone(space: Space, zone: string): Space {
  if (space.zones.length <= 1 || !space.zones.includes(zone)) return space;
  const zones = space.zones.filter((name) => name !== zone);
  return {
    ...space,
    zones,
    zone: space.zone === zone ? zones[0] : space.zone,
    furniture: space.furniture.filter((item) => item.zone !== zone),
    regions: space.regions.filter((item) => item.zone !== zone),
    revision: space.revision + 1,
  };
}

/** Furniture lists read A → D (a piece takes its strictest grade; none counts as D), then by name. */
export function byGradeThenName<T extends { name: string; regions: Pick<Region, "grade">[] }>(rows: T[]): T[] {
  const rank = (row: T) => Math.min(...row.regions.map((r) => "ABCD".indexOf(r.grade)), 3);
  return [...rows].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, "ko", { numeric: true }));
}

/** Rows per grade as the furniture lists show them: each piece once at its strictest grade (none = D), plus freely drawn areas. */
export function gradeCounts(space: Pick<Space, "furniture" | "regions">): Record<Grade, number> {
  const counts: Record<Grade, number> = { A: 0, B: 0, C: 0, D: 0 };
  for (const f of space.furniture) {
    const own = space.regions.filter((r) => r.furnitureId === f.id);
    counts["ABCD"[Math.min(...own.map((r) => "ABCD".indexOf(r.grade)), 3)] as Grade]++;
  }
  for (const r of space.regions) if (!r.furnitureId) counts[r.grade]++;
  return counts;
}
