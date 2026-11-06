// Phone web capture → measurement server (measure/registration-core, reached as /measure) →
// FR-06 registration on the backend.
//
// WEB APP TEST ONLY parts are marked below: browsers expose no camera intrinsics, so phones testing
// through the web app run a one-time guided setup (더보기 › 카메라 준비, the installed board seen
// from many angles) and keep the result on the device. The native app (mobile/) reads intrinsics
// from the OS camera instead; drop savedCamera/setupCamera/prepare's fixed size when it ships.
import { resolveWindow } from "./model.ts";
import type { Space } from "./model.ts";

// 1m film, 10x10 cells: the central 6x6 cells form one 60cm ArUco marker.
// ID 0 is shared with the default FR-05 display; scenes remain separately identified.
export const BOARD = {
  board_id: "window_board",
  dictionary: "DICT_4X4_50",
  ids_by_row: [[0]],
  marker_side_m: 0.6,
  gap_x_m: 0,
  gap_y_m: 0,
  border_bits: 1,
} as const;
const BOARD_W = BOARD.marker_side_m;
const BOARD_H = BOARD.marker_side_m;

export type Camera = {
  width: number; height: number; fx: number; fy: number; cx: number; cy: number;
  pixel_space: "raw_opencv" | "rectified"; distortion: number[]; source: string; lens_id: string;
};
export type Detection = { marker_id: number; corners_px: [number, number][] };
export type Frame = { capture_id: string; captured_at: string; width: number; height: number; base64: string; detections: Detection[] };
export type MeasuredPoint = { id: string; frames: Frame[] };

const LENS = "web-rear";
// Photos are scaled to one fixed size so every frame shares the intrinsics found by the setup.
const MAX_SIDE = 1920;
const CAMERA_KEY = "sunsync-web-camera-single-film-v2";

export class MeasureError extends Error {}

async function post<T>(url: string, body: unknown): Promise<T> {
  return JSON.parse(await postRaw(url, typeof body === "string" ? body : JSON.stringify(body))) as T;
}
// Returns the body text untouched: the measurement's reference_id is a digest of its JSON, and a
// parse/stringify round trip in JS (1.0 → 1) would break it before /v1/fr06 checks it.
async function postRaw(url: string, body: string): Promise<string> {
  let response: Response;
  try {
    response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body });
  } catch {
    throw new MeasureError("측정 서버에 연결하지 못했어요. 네트워크를 확인해 주세요.");
  }
  const text = await response.text();
  let data: { detail?: unknown } | null = null;
  try { data = JSON.parse(text); } catch { /* non-JSON error page */ }
  if (!response.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail ?? response.status);
    console.warn("[measure]", url, detail); // the person sees serverMessage; developers need the raw reason
    throw new MeasureError(serverMessage(detail));
  }
  return text;
}
async function get<T>(url: string): Promise<T> {
  const response = await fetch(url).catch(() => null);
  if (!response?.ok) throw new MeasureError("서버의 공간 정보를 읽지 못했어요.");
  return response.json() as Promise<T>;
}

// The server answers in English; the person sees what to do next.
export function serverMessage(detail: string): string {
  const rules: [RegExp, string][] = [
    [/Move the phone|repeated viewpoints/i, "같은 자리에서 찍은 사진이 있어요. 휴대폰 위치를 바꿔 가며 찍어 주세요."],
    [/across more of the screen/i, "보드가 화면 가장자리에도 오도록 위치를 바꿔 가며 찍어 주세요."],
    [/Tilt the phone/i, "휴대폰을 위아래·좌우로 기울여 가며 더 찍어 주세요."],
    [/unstable|independent-view/i, "카메라 준비가 불안정해요. 흔들리지 않게 몇 장 더 찍어 주세요."],
    [/All configured board markers/i, "마커의 검정 테두리와 네 모서리가 모두 보이게 찍어 주세요."],
    [/optical range/i, "이 카메라는 자동 준비를 지원하지 않아요. 다른 카메라(기본 1배)로 찍어 주세요."],
    [/Duplicate target marker/i, "같은 마커가 두 번 보여요. 다른 보드나 화면을 가려 주세요."],
    [/calibration|Register and confirm/i, "측정 기준을 저장하지 못했어요. 창문 위치와 북쪽 확인을 다시 저장해 주세요."],
    [/Revision conflict|409/i, "다른 곳에서 공간이 바뀌었어요. 목록을 새로고침한 뒤 다시 시도해 주세요."],
    [/repeat|spread|residual|reprojection|insufficient|not enough/i, "같은 지점 사진끼리 위치가 많이 달라요. 지점에 렌즈를 정확히 맞추고 다시 찍어 주세요."],
  ];
  return rules.find(([pattern]) => pattern.test(detail))?.[1] ?? "측정하지 못했어요. 사진을 다시 찍어 주세요.";
}

/** Raw pixels (EXIF rotation ignored, as the server does), scaled to MAX_SIDE, as JPEG base64. */
export async function prepare(file: File): Promise<Pick<Frame, "width" | "height" | "base64">> {
  const bitmap = await createImageBitmap(file, { imageOrientation: "none" });
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale), height = Math.round(bitmap.height * scale);
  const canvas = document.createElement("canvas");
  canvas.width = width; canvas.height = height;
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();
  const blob = await new Promise<Blob>((done, fail) => canvas.toBlob((b) => (b ? done(b) : fail(new Error("encode"))), "image/jpeg", 0.92));
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return { width, height, base64: btoa(binary) };
}

// Detection only needs the pixel size; intrinsics come from the setup.
const detectionCamera = (width: number, height: number): Camera => ({
  width, height, fx: width, fy: width, cx: width / 2, cy: height / 2,
  pixel_space: "raw_opencv", distortion: [0, 0, 0, 0, 0], source: "detection_only", lens_id: LENS,
});

/** Prepare a photo and find the board markers in it. */
export async function detectFrame(file: File): Promise<Frame> {
  const image = await prepare(file);
  const result = await post<{ detections: Detection[] }>("/measure/v1/detect", {
    camera: detectionCamera(image.width, image.height), board: BOARD, image_base64: image.base64,
  });
  return { ...image, capture_id: crypto.randomUUID(), captured_at: new Date().toISOString(), detections: result.detections };
}

// WEB APP TEST ONLY: intrinsics from the guided setup, per phone browser.
export function savedCamera(): Camera | null {
  try {
    const camera = JSON.parse(localStorage.getItem(CAMERA_KEY) ?? "null");
    return camera && typeof camera.fx === "number" ? camera : null;
  } catch {
    return null;
  }
}

/** WEB APP TEST ONLY: 12–30 views of the whole board → intrinsics for this phone camera, kept on this device. */
export async function setupCamera(frames: Frame[]): Promise<Camera> {
  const result = await post<{ camera: Camera }>("/measure/v1/mobile/automatic-setup", {
    board: BOARD,
    views: frames.map((f) => ({ capture_id: f.capture_id, stream_id: LENS, lens_id: LENS, width: f.width, height: f.height, detections: f.detections })),
  });
  try { localStorage.setItem(CAMERA_KEY, JSON.stringify(result.camera)); } catch { /* used for this session only */ }
  return result.camera;
}

type Vec = [number, number, number];
const add = (a: Vec, b: Vec, k = 1): Vec => [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k];
const cross = (a: Vec, b: Vec): Vec => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

/** Board pose in the room's ENU frame, from the window and the marker centre the space stores.
 *  Window frames match backend/app/ui_scene.py; the board is centred on the marker, read from inside. */
export function boardAlignment(space: Space) {
  const w = resolveWindow(space);
  if (!w) return null;
  const o = w.offset, z = w.sill, W = w.width;
  const frames: Record<string, [Vec, Vec]> = {
    south: [[o + W, 0, z], [-1, 0, 0]],
    north: [[o, space.depth, z], [1, 0, 0]],
    east: [[space.width, space.depth - o, z], [0, -1, 0]],
    west: [[0, space.depth - o - W, z], [0, 1, 0]],
  };
  const [corner, u] = frames[w.wall];
  const up: Vec = [0, 0, 1], down: Vec = [0, 0, -1];
  const centre = add(add(corner, u, w.marker.x), up, w.marker.y);
  // Board frame: x right and y down as seen from inside, z into the board (out of the room).
  const origin = add(add(centre, u, -BOARD_W / 2), up, BOARD_H / 2);
  const zAxis = cross(u, down);
  return {
    alignment: {
      T_enu_board: [u[0], down[0], zAxis[0], origin[0], u[1], down[1], zAxis[1], origin[1], u[2], down[2], zAxis[2], origin[2], 0, 0, 0, 1],
      north_confirmed: true,
      floor_confirmed: true,
    },
    floor: { board_origin_height_m: origin[2], up_board: [0, -1, 0] },
  };
}

type FilmMarker = { code_id: number; dictionary: string; side_length_m: number; plane: string; T_ENU_marker: number[] };

/** FR-05 marker frame is centre/x-right/y-up; measurement board is top-left/x-right/y-down. */
export function filmBoardAlignment(marker: FilmMarker) {
  if (marker.dictionary !== BOARD.dictionary || marker.code_id !== BOARD.ids_by_row[0][0] ||
      Math.abs(marker.side_length_m - BOARD.marker_side_m) > 1e-6 || marker.T_ENU_marker.length !== 16) {
    throw new MeasureError("필름 마커 규격이 달라요. ID 0의 60cm 단일 마커를 표시하고 측정 기준을 다시 확인해 주세요.");
  }
  const t = marker.T_ENU_marker;
  const T = [...t];
  for (let r = 0; r < 3; r++) {
    T[r * 4 + 1] = -t[r * 4 + 1];
    T[r * 4 + 2] = -t[r * 4 + 2];
    T[r * 4 + 3] = t[r * 4 + 3] - t[r * 4] * BOARD_W / 2 + t[r * 4 + 1] * BOARD_H / 2;
  }
  return { alignment: { T_enu_board: T, north_confirmed: true, floor_confirmed: true },
    floor: { board_origin_height_m: T[11], up_board: [T[8], T[9], T[10]] } };
}

const SERVER_SHAPES: Record<string, string> = { rectangle: "rectangle", circle: "circle", ellipse: "ellipse" };

/** Measure one captured piece of furniture and register it in the space's scene. Returns the
 *  updated UI space (the backend adds the measured furniture to it). */
export async function measureAndRegister(space: Space, camera: Camera, item: {
  name: string; shape: string; vertices: { x: number; y: number }[]; points: MeasuredPoint[];
}): Promise<Space> {
  if (!space.serverSceneId) throw new MeasureError("공간을 먼저 서버에 저장해 주세요.");
  if (!space.site?.northConfirmed) throw new MeasureError("공간 정보에서 도면의 북쪽을 확인해 주세요.");
  let pose = boardAlignment(space);
  if (!pose) throw new MeasureError("공간 정보에서 창문 크기와 위치를 입력해 주세요.");
  const scene = await get<{ revision: number; room: { room_id: string }; registration_calibration: { calibration_id: string } | null } & Record<string, unknown>>(
    `/api/scenes/${encodeURIComponent(space.serverSceneId)}`,
  );
  const filmMarkers = ((scene.markers ?? []) as FilmMarker[]).filter(m => m.plane === "window");
  if (filmMarkers.length > 1) throw new MeasureError("측정에 사용할 창문 마커를 하나만 지정해 주세요.");
  if (filmMarkers.length === 1) pose = filmBoardAlignment(filmMarkers[0]);
  else if (Math.abs(resolveWindow(space)!.marker.size - BOARD.marker_side_m) > 1e-6) {
    throw new MeasureError("창문 정보의 마커 크기를 실제 검정 외곽 한 변 60cm로 확인해 주세요.");
  }
  const kind = SERVER_SHAPES[item.shape] ?? "polygon";
  const plan = {
    space_id: scene.room.room_id,
    furniture_id: `measured_${crypto.randomUUID().slice(0, 8)}`,
    furniture_kind: "desk",
    model_id: "standard_desk",
    shape: kind === "polygon" ? { kind, vertices: item.vertices.map((p) => [p.x / 100, p.y / 100]) } : { kind },
    samples_per_point: 3,
  };
  const measurement = await postRaw("/measure/v1/measurements", JSON.stringify({
    plan,
    board: BOARD,
    ...pose,
    captures: item.points.flatMap((point) => point.frames.map((f) => ({
      capture_id: f.capture_id, point_id: point.id, captured_at: f.captured_at, camera, detections: f.detections,
    }))),
  }));
  const fragment = await post<{ calibration_id: string; markers: unknown[]; occluders: { name: string }[] } & Record<string, unknown>>(
    "/measure/v1/fr06", `{"measurement":${measurement}}`,
  );
  fragment.occluders.forEach((o) => (o.name = item.name));
  const sceneUrl = `/api/scenes/${encodeURIComponent(space.serverSceneId)}`;
  let current = scene;
  if (current.registration_calibration?.calibration_id !== fragment.calibration_id) {
    if (current.registration_calibration) {
      // The board moved (window or marker changed): records measured against the old placement
      // no longer match. A plain web save turns them into ordinary furniture first.
      const ui = await get<Space>(`/api/ui/spaces/${encodeURIComponent(space.id)}`);
      await put(`/api/ui/spaces/${encodeURIComponent(space.id)}`, ui);
      current = await get<typeof scene>(sceneUrl);
    }
    // The board/ENU/floor reference is stored once per board placement, before its furniture.
    await put(sceneUrl, {
      ...current,
      revision: current.revision + 1,
      registration_calibration: { calibration_id: fragment.calibration_id, marker: fragment.markers[0] },
    });
    current = { ...current, revision: current.revision + 1 };
  }
  await post(`${sceneUrl}/registrations`, { expected_revision: current.revision, fragment });
  return get<Space>(`/api/ui/spaces/${encodeURIComponent(space.id)}`);
}

async function put(url: string, body: unknown) {
  const response = await fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!response) throw new MeasureError("서버에 연결하지 못했어요.");
  if (!response.ok) {
    const detail = JSON.stringify((await response.json().catch(() => null))?.detail ?? response.status);
    console.warn("[measure]", url, detail);
    throw new MeasureError(serverMessage(detail));
  }
}
