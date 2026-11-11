// Phone web capture → measurement server (measure/registration-core, reached as /measure) →
// FR-06 registration on the backend.
//
// WEB APP TEST ONLY parts are marked below: browsers expose no camera intrinsics, so phones testing
// through the web app run a one-time guided setup (더보기 › 카메라 준비, the installed board seen
// from many angles) and keep the result on the device. The native app (mobile/) reads intrinsics
// from the OS camera instead; drop savedCamera/setupCamera/prepare's fixed size when it ships.
import { resolveWindow } from "./model.ts";
import type { Space } from "./model.ts";
import { EXPIRED_MESSAGE, notifyUnauthorized } from "./auth.ts";

// 1m film, 10x10 cells: the central 6x6 cells form one 60cm ArUco marker.
// ID 0 remains a standalone test default; app capture uses the server session board.
export type Board = { board_id: string; dictionary: string; ids_by_row: number[][]; marker_side_m: number; gap_x_m: number; gap_y_m: number; border_bits: number };
export const BOARD: Board = {
  board_id: "window_board",
  dictionary: "DICT_4X4_50",
  ids_by_row: [[0]],
  marker_side_m: 0.6,
  gap_x_m: 0,
  gap_y_m: 0,
  border_bits: 1,
};
const BOARD_W = BOARD.marker_side_m;
const BOARD_H = BOARD.marker_side_m;

export type Camera = {
  width: number; height: number; fx: number; fy: number; cx: number; cy: number;
  pixel_space: "raw_opencv" | "rectified"; distortion: number[]; source: string; lens_id: string;
};
export type Detection = { marker_id: number; corners_px: [number, number][] };
export type Frame = { pose_status?: string; image_sha256?: string; marker_session_id?: string; capture_id: string; captured_at: string; width: number; height: number; base64: string; detections: Detection[] };
export type MeasuredPoint = { id: string; frames: Frame[] };

const LENS = "web-rear";
// Photos are scaled to one fixed size so every frame shares the intrinsics found by the setup.
const MAX_SIDE = 1920;
const CAMERA_KEY = "sunsync-web-camera-single-film-v2";

export class MeasureError extends Error {
  status?: number;
  constructor(message: string, status?: number) { super(message); this.status = status; }
}
// The measurement server may answer 401 with a non-JSON body (Caddy forward_auth): status only.
function unauthorized() {
  notifyUnauthorized();
  return new MeasureError(EXPIRED_MESSAGE, 401);
}

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
  if (response.status === 401) throw unauthorized();
  const text = await response.text();
  let data: { detail?: unknown } | null = null;
  try { data = JSON.parse(text); } catch { /* non-JSON error page */ }
  if (!response.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail ?? response.status);
    console.warn("[measure]", url, detail); // the person sees serverMessage; developers need the raw reason
    throw new MeasureError(serverMessage(detail), response.status);
  }
  return text;
}
async function get<T>(url: string): Promise<T> {
  const response = await fetch(url).catch(() => null);
  if (!response) throw new MeasureError("서버의 공간 정보를 읽지 못했어요.");
  if (response.status === 401) throw unauthorized();
  if (!response.ok) {
    const detail = JSON.stringify((await response.json().catch(() => null))?.detail ?? response.status);
    throw new MeasureError(serverMessage(detail), response.status);
  }
  return response.json() as Promise<T>;
}

// The server answers in English; the person sees what to do next.
export function serverMessage(detail: string): string {
  const rules: [RegExp, string][] = [
    [/선택한 필름이 변경됐어요/, "선택한 필름이 변경됐어요. 이전 촬영을 끝내고 촬영 화면을 다시 열어 주세요."],
    [/complete 1m film modules/i, "창의 필름 배치를 확인해 주세요. 1m 필름 여러 장과 10cm 셀을 기준으로 측정해요."],
    [/Film module outside/i, "선택한 필름이 창의 배치 범위를 벗어났어요."],
    [/Another capture session/i, "이 공간에서 다른 촬영이 진행 중이에요. 먼저 완료하거나 취소해 주세요."],
    [/Capture marker|Capture request/i, "촬영 마커가 바뀌었거나 시간이 만료됐어요. 촬영을 다시 시작해 주세요."],
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

/** The measurement server answers 200 even when the photos do not add up (the failure only surfaces
 *  later, from /v1/fr06, as an unreadable error). Name each point that failed and why, so only those
 *  points are retaken. An empty list means the measurement is usable. */
export type PointProblem = { point?: string; message: string };
export function measurementProblems(text: string): PointProblem[] {
  let m: { status?: string; points?: Record<string, { status?: string }> };
  try { m = JSON.parse(text); } catch { return []; }
  if (m.status !== "incomplete" && m.status !== "invalid_surface") return [];
  const found: PointProblem[] = Object.entries(m.points ?? {}).filter(([, p]) => p.status !== "ok").map(([point, p]) => ({
    point,
    message: p.status === "inconsistent_samples"
      ? `${point} · 같은 지점 사진끼리 위치가 크게 어긋나요(3cm 넘게). 폰을 그 지점에 고정하고 다시 찍어 주세요.`
      : `${point} · 마커가 또렷하게 잡힌 사진이 부족해요. 마커 전체가 보이게, 너무 멀거나 비스듬하지 않게 다시 찍어 주세요.`,
  }));
  if (!found.length && m.status === "invalid_surface") found.push({ message: "찍은 꼭짓점들이 고른 상판 모양으로 이어지지 않아요. 꼭짓점 순서와 위치를 확인하고 다시 찍어 주세요." });
  return found;
}
export const measurementProblem = (text: string): string | null => {
  const found = measurementProblems(text);
  return found.length ? found.map((p) => p.message).join(" ") : null;
};

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
export async function detectFrame(file: File, board: Board = BOARD, session?: CaptureMarker, capturedAt?: string, calibratedCamera?:Camera): Promise<Frame> {
  if (session) await checkCaptureMarker(session);
  const image = await prepare(file);
  if(calibratedCamera&&(image.width!==calibratedCamera.width||image.height!==calibratedCamera.height))throw new MeasureError("촬영 영상 크기가 카메라 설정과 달라요. 다시 준비해 주세요.");
  const result = await post<{ detections: Detection[]; pose?:{status:string} }>("/measure/v1/detect", {
    camera: calibratedCamera ?? detectionCamera(image.width, image.height), board, image_base64: image.base64,
  });
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  const image_sha256 = Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,"0")).join("");
  return { ...image, pose_status:calibratedCamera?result.pose?.status:undefined, image_sha256, marker_session_id: session?.session_id, capture_id: crypto.randomUUID(), captured_at: capturedAt ?? new Date(file.lastModified).toISOString(), detections: result.detections };
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
export async function setupCamera(frames: Frame[], board: Board = BOARD, persist = true): Promise<Camera> {
  const result = await post<{ camera: Camera }>("/measure/v1/mobile/automatic-setup", {
    board,
    views: frames.map((f) => ({ capture_id: f.capture_id, stream_id: LENS, lens_id: LENS, width: f.width, height: f.height, detections: f.detections })),
  });
  try { if(persist) localStorage.setItem(CAMERA_KEY, JSON.stringify(result.camera)); } catch { /* used for this session only */ }
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

export type FilmMarker = { code_id: number; dictionary: string; side_length_m: number; plane: string; T_ENU_marker: number[] };

/** FR-05 marker frame is centre/x-right/y-up; measurement board is top-left/x-right/y-down. */
export function filmBoardAlignment(marker: FilmMarker, board: Board = BOARD) {
  if (marker.dictionary !== board.dictionary || marker.code_id !== board.ids_by_row[0][0] ||
      Math.abs(marker.side_length_m - BOARD.marker_side_m) > 1e-6 || marker.T_ENU_marker.length !== 16) {
    throw new MeasureError("필름 마커 규격이 달라요. 이번 촬영에서 지정된 60cm 단일 마커를 표시하고 측정 기준을 다시 확인해 주세요.");
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

type MeasureItem = { session: CaptureMarker; shape: string; vertices: { x: number; y: number }[]; points: MeasuredPoint[] };

/** Run the measurement for one piece of furniture: raw server text plus what registration needs. */
async function measureRaw(sceneId: string, camera: Camera, item: MeasureItem) {
  if (item.session.scene_id !== sceneId) throw new MeasureError("촬영한 공간과 측정할 공간이 달라요.");
  const session = await checkCaptureMarker(item.session);
  const board = session.board;
  for (const point of item.points) for (const frame of point.frames) {
    if (frame.marker_session_id !== session.session_id || frame.detections.length !== 1 ||
        frame.detections[0].marker_id !== board.ids_by_row[0][0]) {
      throw new MeasureError("다른 촬영 세션 또는 마커의 사진이 섞였어요. 다시 촬영해 주세요.");
    }
  }
  const scene = await get<{ revision: number; room: { room_id: string }; registration_calibration: { calibration_id: string } | null } & Record<string, unknown>>(
    `/api/scenes/${encodeURIComponent(sceneId)}`,
  );
  const filmMarkers = ((scene.markers ?? []) as FilmMarker[]).filter(m => m.plane === "window");
  const filmMarker = filmMarkers.find(m => m.code_id === board.ids_by_row[0][0]);
  if (!filmMarker || JSON.stringify(filmMarker.T_ENU_marker) !== JSON.stringify(session.marker.T_ENU_marker)) {
    throw new MeasureError("촬영 중 마커 위치가 바뀌었어요. 다시 촬영해 주세요.");
  }
  const pose = filmBoardAlignment(filmMarker, board);
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
    board,
    ...pose,
    captures: item.points.flatMap((point) => point.frames.map((f) => ({
      capture_id: f.capture_id, point_id: point.id, captured_at: f.captured_at, image_sha256:f.image_sha256, camera, detections: f.detections,
    }))),
  }));
  return { session, scene, measurement };
}

/** Pre-check a finished set of points before leaving the capture screen: which points must be retaken. */
export async function checkMeasurement(sceneId: string, camera: Camera, item: MeasureItem): Promise<PointProblem[]> {
  return measurementProblems((await measureRaw(sceneId, camera, item)).measurement);
}

/** Measure one captured piece of furniture and register it in the space's scene. Returns the
 *  updated UI space (the backend adds the measured furniture to it). */
export async function measureAndRegister(space: Space, camera: Camera, item: MeasureItem & { name: string }): Promise<Space> {
  if (!space.serverSceneId) throw new MeasureError("공간을 먼저 서버에 저장해 주세요.");
  if (!space.site?.northConfirmed) throw new MeasureError("공간 정보에서 도면의 북쪽을 확인해 주세요.");
  const { session, scene, measurement } = await measureRaw(space.serverSceneId, camera, item);
  const problem = measurementProblem(measurement);
  if (problem) throw new MeasureError(problem);
  const fragment = await post<{ calibration_id: string; markers: unknown[]; occluders: { name: string }[] } & Record<string, unknown>>(
    "/measure/v1/fr06", `{"measurement":${measurement}}`,
  );
  fragment.occluders.forEach((o) => (o.name = item.name));
  const sceneUrl = `/api/scenes/${encodeURIComponent(space.serverSceneId)}`;
  const current = scene;
  if (current.registration_calibration?.calibration_id !== fragment.calibration_id) {
    if (current.registration_calibration) {
      throw new MeasureError("측정 기준이 바뀌었어요. 기존 공간을 보존하고 새 공간에서 측정해 주세요.");
    }
    // The board/ENU/floor reference is stored once per board placement, before its furniture.
    await put(sceneUrl, {
      ...current,
      revision: current.revision + 1,
      registration_calibration: { calibration_id: fragment.calibration_id, marker: fragment.markers[0] },
    });
    current.revision += 1;
  }
  await checkCaptureMarker(session);
  await post(`${sceneUrl}/registrations`, { expected_revision: current.revision, fragment });
  return get<Space>(`/api/ui/spaces/${encodeURIComponent(space.id)}`);
}

async function put(url: string, body: unknown) {
  const response = await fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!response) throw new MeasureError("서버에 연결하지 못했어요.");
  if (response.status === 401) throw unauthorized();
  if (!response.ok) {
    const detail = JSON.stringify((await response.json().catch(() => null))?.detail ?? response.status);
    console.warn("[measure]", url, detail);
    throw new MeasureError(serverMessage(detail), response.status);
  }
}


export type CaptureMarker = {
  session_id: string; scene_id: string; window_id: string; expires_at: string;
  display_status: "virtual_applied"; board: Board; marker: FilmMarker; film_cells: number[][];
  film_module?: { row: number; col: number; rows: number; cols: number; side_m: number; marker_center_uv_m: [number, number] };
};
export type CaptureTarget = { window_id?: string; module_row?: number; module_col?: number };
const markerKey = (sceneId: string) => `sunsync-capture-marker:${sceneId}`;
const markerUrl = (sceneId: string) => `/api/scenes/${encodeURIComponent(sceneId)}/capture-marker`;
export async function startCaptureMarker(sceneId: string, target: CaptureTarget = {}): Promise<CaptureMarker> {
  // Save before sending: retries, remounts and a lost response reuse the same ID.
  let id = sessionStorage.getItem(markerKey(sceneId));
  if (!id) { id = crypto.randomUUID(); sessionStorage.setItem(markerKey(sceneId), id); }
  try {
    return await post<CaptureMarker>(markerUrl(sceneId), { ...target, request_id: id });
  } catch (e) {
    if (e instanceof MeasureError && e.status === 410) {
      sessionStorage.removeItem(markerKey(sceneId));
      return startCaptureMarker(sceneId, target);
    }
    throw e;
  }
}
export async function checkCaptureMarker(session: CaptureMarker): Promise<CaptureMarker> {
  const current = await get<CaptureMarker>(`${markerUrl(session.scene_id)}/${session.session_id}`);
  if (current.session_id !== session.session_id || current.scene_id !== session.scene_id ||
      current.window_id !== session.window_id || JSON.stringify(current.film_module) !== JSON.stringify(session.film_module) || JSON.stringify(current.board) !== JSON.stringify(session.board) ||
      JSON.stringify(current.marker.T_ENU_marker) !== JSON.stringify(session.marker.T_ENU_marker)) {
    throw new MeasureError("촬영 마커 정보가 바뀌었어요. 다시 촬영해 주세요.");
  }
  return current;
}
export async function endCaptureMarker(session: CaptureMarker): Promise<void> {
  await post(`${markerUrl(session.scene_id)}/${session.session_id}/end`, {});
  if (sessionStorage.getItem(markerKey(session.scene_id)) === session.session_id) sessionStorage.removeItem(markerKey(session.scene_id));
}

export type FilmTargetWindow = {window_id:string; rows:number; cols:number};
export type CaptureTargets = { windows: FilmTargetWindow[]; selected_target?: CaptureTarget };
export async function captureSelection(sceneId:string):Promise<CaptureTargets> {
  return get<CaptureTargets>(`${markerUrl(sceneId)}/targets`);
}
export async function captureTargets(sceneId:string):Promise<FilmTargetWindow[]> {
  return (await captureSelection(sceneId)).windows;
}
export async function resumeCaptureMarker(sceneId:string):Promise<CaptureMarker|null> {
  const id=sessionStorage.getItem(markerKey(sceneId));
  if (!id) return null;
  try { return await get<CaptureMarker>(`${markerUrl(sceneId)}/${id}`); }
  catch(e) {
    if(e instanceof MeasureError && (e.status===404 || e.status===410)) {
      sessionStorage.removeItem(markerKey(sceneId)); return null;
    }
    throw e;
  }
}
export async function cancelStoredCapture(sceneId:string):Promise<void> {
  const id=sessionStorage.getItem(markerKey(sceneId));
  if(id) await post(`${markerUrl(sceneId)}/${id}/end`,{});
  sessionStorage.removeItem(markerKey(sceneId));
}


export type Selection = {status:"ok"|"retake";raw_samples:number;selected_samples:number;
  selected_capture_ids:string[];quality:{uncertainty_m?:number;reason:string};diagnostics?:{status:string}[]};
export async function selectFrames(frames:Frame[],camera:Camera,board:Board,pointId:string):Promise<Selection> {
  return post("/measure/v1/observations/select",{board,captures:frames.map(f=>({
    capture_id:f.capture_id,point_id:pointId,captured_at:f.captured_at,image_sha256:f.image_sha256,
    camera,detections:f.detections,
  }))});
}
