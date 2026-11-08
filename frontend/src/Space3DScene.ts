import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { filmGrid, isWidthWall, resolveWindow } from "./model.ts";
import type { Face, Furniture, Region, Space, Wall } from "./model.ts";
import { sunPosition } from "./sun3d.ts";
import { createOffsetCache } from "./floorSun.ts";
import { shelfFront } from "./shelfFront.ts";

export type Level = "basic" | "sun" | "film";
export type Lit = { open: Record<string, number>; film: Record<string, number>; blocked: number; cells: number };
export type SceneApi = {
  setLevel(level: Level): void;
  setTime(dayOfYear: number, hour: number, server?: ServerMoment): Lit;
  setView(view: "3d" | "2d"): void;
  reset(): void;
  select(id: string | null): void;
  previewFurniture(furniture: Furniture | null): void;
  dispose(): void;
};
type Events = { onSelect(id: string | null): void; planBounds?: { left: number; right: number; top: number; bottom: number } };
// FR-01 sun direction (ENU) and FR-04 cell states (server order: row 0 top, column 0 left seen from inside).
export type ServerMoment = { sun?: [number, number, number] | null; cells?: Uint8Array | null; rows?: number; cols?: number };

const INK = "#1d1c22", ACTION = "#6660e5", SUN = "#ff7426";
// Cell state codes from space3dServer STATE_CODE: 1 opaque, 2 infeasible (darkest film is not enough). Unknown (3) cells are counted, not drawn.
// Same rule as the server (backend/app/ui_scene.py protected_face): furniture protects the side facing the window.
const WINDOW_FACE: Record<Wall, Face> = { south: "남쪽 전면", north: "북쪽 후면", east: "동쪽 측면", west: "서쪽 측면" };
const STATE_COLOR: Record<number, string> = { 1: "#4b45c6", 2: "#2a2833" };
const closes = (code: number) => code === 1 || code === 2;
const TYPE_COLOR: Record<string, string> = { bookcase: "#f2f0ea", desk: "#ececf0", table: "#f3f3f5", cabinet: "#e4e4e9", display: "#f3f3f5", other: "#ececec" };
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

export function createSpaceScene(host: HTMLElement, space: Space, events: Events, startView: "3d" | "2d" = "3d"): SceneApi {
  // T: wall thickness as drawn. Thin, like a plan line; the real wall depth is not measured.
  const W = space.width, D = space.depth, H = space.height, T = 0.04;
  // Plan coordinates: x east, y south from the north-west corner. Three.js: x east, y up, z south.
  const at = (x: number, y: number, h = 0) => new THREE.Vector3(x - W / 2, h, y - D / 2);
  const win = resolveWindow(space);
  const site = space.site ?? { latitude: 37.57, longitude: 126.98, timezone: "Asia/Seoul", northConfirmed: true };

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.toneMapping = THREE.NeutralToneMapping;
  host.appendChild(renderer.domElement);
  const labels = new CSS2DRenderer();
  labels.domElement.className = "s3-labels";
  host.appendChild(labels.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#ffffff");
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 400);
  // The editor's SVG gestures use the same metre coordinates as this orthographic view.
  const b = events.planBounds;
  const planCamera = b ? new THREE.OrthographicCamera(-W / 2 - b.left, W / 2 + b.right, D / 2 + b.top, -D / 2 - b.bottom, 0.1, 100) : null;
  if (planCamera) {
    planCamera.position.set(0, H + 20, 0);
    planCamera.up.set(0, 0, -1);
    planCamera.lookAt(0, 0, 0);
  }
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 5;
  controls.maxDistance = 140;
  controls.maxPolarAngle = THREE.MathUtils.degToRad(84);
  controls.screenSpacePanning = true;

  // Lights: a soft studio light for the plain view, a real sun with shadows for the sunlight steps.
  const hemi = new THREE.HemisphereLight("#ffffff", "#e7e6f0", 1.1);
  const key = new THREE.DirectionalLight("#ffffff", 1.3);
  key.position.set(4, 10, 7);
  const sun = new THREE.DirectionalLight("#ffb370", 0);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -9, right: 9, top: 9, bottom: -9, near: 1, far: 50 });
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.02;
  sun.shadow.radius = 2.5;
  scene.add(hemi, key, sun, sun.target);

  const box = (size: THREE.Vector3, center: THREE.Vector3, material: THREE.Material | THREE.Material[]) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(size.x, size.y, size.z), material);
    m.position.copy(center);
    m.castShadow = m.receiveShadow = true;
    return m;
  };
  const edges = (mesh: THREE.Mesh, material: THREE.LineBasicMaterial) => {
    const line = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 20), material);
    line.raycast = () => {};
    mesh.add(line);
    return line;
  };
  const occluders: THREE.Mesh[] = [];

  // Floor slab, 50 cm drawing grid, and an invisible ground that only catches the building's shadow.
  const floor = box(new THREE.Vector3(W + 2 * T, 0.1, D + 2 * T), new THREE.Vector3(0, -0.05, 0), new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 1 }));
  edges(floor, new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.35 }));
  scene.add(floor);
  const grid: number[] = [];
  for (let x = 0.5; x < W; x += 0.5) grid.push(...at(x, 0, 0.003).toArray(), ...at(x, D, 0.003).toArray());
  for (let y = 0.5; y < D; y += 0.5) grid.push(...at(0, y, 0.003).toArray(), ...at(W, y, 0.003).toArray());
  const gridGeo = new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(grid, 3));
  scene.add(new THREE.LineSegments(gridGeo, new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.06 })));
  const groundMat = new THREE.ShadowMaterial({ opacity: 0 });
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(90, 90), groundMat);
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.1;
  ground.receiveShadow = true;
  scene.add(ground);

  // Walls cut at the top like a section drawing. A wall between the camera and the room fades out
  // (but keeps casting shadows), so the room reads like a doll's house from any side.
  type WallPart = { normal: THREE.Vector3; center: THREE.Vector3; fill: THREE.MeshStandardMaterial[]; line: THREE.LineBasicMaterial; fade: number };
  const wallParts: WallPart[] = [];
  const walls: Wall[] = ["north", "south", "east", "west"];
  for (const wall of walls) {
    const wide = isWidthWall(wall), length = wide ? W : D;
    const fill = new THREE.MeshStandardMaterial({ color: "#f6f6f8", roughness: 0.95, transparent: true });
    const cap = new THREE.MeshStandardMaterial({ color: "#2a2833", roughness: 0.9, transparent: true });
    const line = new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.45 });
    const hole = win?.wall === wall ? win : null;
    const rects = hole
      ? [[-T, length + T, 0, hole.sill], [-T, length + T, hole.sill + hole.height, H], [-T, hole.offset, hole.sill, hole.sill + hole.height], [hole.offset + hole.width, length + T, hole.sill, hole.sill + hole.height]]
      : [[-T, length + T, 0, H]];
    for (const [u0, u1, v0, v1] of rects) {
      if (u1 - u0 < 1e-3 || v1 - v0 < 1e-3) continue;
      const u = (u0 + u1) / 2, v = (v0 + v1) / 2;
      const center = wide ? at(u, wall === "south" ? D + T / 2 : -T / 2, v) : at(wall === "east" ? W + T / 2 : -T / 2, u, v);
      const size = wide ? new THREE.Vector3(u1 - u0, v1 - v0, T) : new THREE.Vector3(T, v1 - v0, u1 - u0);
      const lid = v1 >= H - 1e-6 ? cap : fill;
      const mesh = box(size, center, [fill, fill, lid, fill, fill, fill]);
      edges(mesh, line);
      scene.add(mesh);
      occluders.push(mesh);
    }
    const normal = wide ? new THREE.Vector3(0, 0, wall === "south" ? 1 : -1) : new THREE.Vector3(wall === "east" ? 1 : -1, 0, 0);
    const center = wide ? at(W / 2, wall === "south" ? D : 0, H / 2) : at(wall === "east" ? W : 0, D / 2, H / 2);
    wallParts.push({ normal, center, fill: [fill, cap], line, fade: 1 });
  }
  // The ceiling is never drawn but still blocks the sun, so light enters only through the window.
  // It lives on layer 1, which only the sun's shadow camera and the light raycasts see.
  const ceiling = box(new THREE.Vector3(W + 2 * T, 0.1, D + 2 * T), new THREE.Vector3(0, H + 0.05, 0), new THREE.MeshBasicMaterial());
  ceiling.receiveShadow = false;
  ceiling.layers.set(1);
  sun.shadow.camera.layers.enable(1);
  scene.add(ceiling);
  occluders.push(ceiling);

  // Window: glass, accent frame, film-cell grid (step 3) and the cells themselves.
  const winFrame = { origin: new THREE.Vector3(), along: new THREE.Vector3(), inward: new THREE.Vector3(), outward: new THREE.Vector3() };
  const film = win ? filmGrid(win) : { cols: 0, rows: 0 };
  const cellCount = film.cols * film.rows;
  const cellMesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.6, transparent: true, opacity: 0.92 }), Math.max(1, cellCount));
  cellMesh.castShadow = true;
  cellMesh.count = cellCount;
  const cellTarget = new Float32Array(cellCount), cellNow = new Float32Array(cellCount), cellBlock = new Uint8Array(cellCount);
  const filmGridMat = new THREE.LineBasicMaterial({ color: ACTION, transparent: true, opacity: 0 });
  if (win) {
    const wide = isWidthWall(win.wall);
    // Offsets run from the west end (south/north walls) or the north end (east/west walls).
    const face = { south: [D, 1], north: [0, -1], east: [W, 1], west: [0, -1] }[win.wall];
    winFrame.origin = wide ? at(win.offset, face[0], win.sill) : at(face[0], win.offset, win.sill);
    winFrame.along = wide ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 0, 1);
    winFrame.outward = wide ? new THREE.Vector3(0, 0, face[1]) : new THREE.Vector3(face[1], 0, 0);
    winFrame.inward = winFrame.outward.clone().negate();
    const mid = winFrame.origin.clone().addScaledVector(winFrame.along, win.width / 2).addScaledVector(winFrame.outward, T / 2).setY(win.sill + win.height / 2);
    const glass = new THREE.Mesh(new THREE.PlaneGeometry(win.width, win.height), new THREE.MeshStandardMaterial({ color: "#e9e8fb", transparent: true, opacity: 0.16, depthWrite: false, side: THREE.DoubleSide }));
    glass.position.copy(mid);
    glass.lookAt(mid.clone().add(winFrame.outward));
    glass.raycast = () => {};
    scene.add(glass);
    const frameLine = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(...(wide ? [win.width, win.height, T + 0.004] : [T + 0.004, win.height, win.width]) as [number, number, number])), new THREE.LineBasicMaterial({ color: ACTION }));
    frameLine.position.copy(mid);
    scene.add(frameLine);
    const lines: number[] = [];
    const p = (u: number, v: number) => winFrame.origin.clone().addScaledVector(winFrame.along, u).addScaledVector(winFrame.inward, 0.012).setY(win.sill + v).toArray();
    for (let c = 1; c < film.cols; c++) lines.push(...p((c * win.width) / film.cols, 0), ...p((c * win.width) / film.cols, win.height));
    for (let r = 1; r < film.rows; r++) lines.push(...p(0, (r * win.height) / film.rows), ...p(win.width, (r * win.height) / film.rows));
    scene.add(new THREE.LineSegments(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(lines, 3)), filmGridMat));
    const color = new THREE.Color("#ffffff");
    for (let i = 0; i < cellCount; i++) cellMesh.setColorAt(i, color);
    scene.add(cellMesh);
  }
  const cellCenter = (i: number) => {
    const c = i % film.cols, r = Math.floor(i / film.cols);
    return winFrame.origin.clone().addScaledVector(winFrame.along, ((c + 0.5) * win!.width) / film.cols).addScaledVector(winFrame.inward, 0.02).setY(win!.sill + ((r + 0.5) * win!.height) / film.rows);
  };
  const cellMatrix = new THREE.Matrix4(), cellQuat = new THREE.Quaternion(), cellScale = new THREE.Vector3();
  const writeCell = (i: number) => {
    const s = cellNow[i];
    const cw = win!.width / film.cols, ch = win!.height / film.rows;
    const wide = isWidthWall(win!.wall);
    cellScale.set(wide ? cw * 0.94 * s : 0.01 * s, ch * 0.94 * s, wide ? 0.01 * s : cw * 0.94 * s);
    cellMesh.setMatrixAt(i, cellMatrix.compose(cellCenter(i), cellQuat, cellScale));
  };
  for (let i = 0; i < cellCount; i++) writeCell(i);

  // Furniture: one simple shape per type, white-grey with drawn edges.
  type Item = { f: Furniture; group: THREE.Group; mats: THREE.MeshStandardMaterial[]; line: THREE.LineBasicMaterial; parts: THREE.Mesh[]; selection: THREE.Group };
  const items = new Map<string, Item>();
  const random = (() => { let s = 7; return () => ((s = (s * 16807) % 2147483647) / 2147483647); })();
  for (const f of space.furniture) {
    const group = new THREE.Group();
    group.position.copy(at(f.x, f.y));
    const mat = new THREE.MeshStandardMaterial({ color: TYPE_COLOR[f.type ?? "other"], roughness: 0.85 });
    const line = new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.42 });
    const mats = [mat];
    const parts: THREE.Mesh[] = [];
    let target: THREE.Object3D = group;
    const part = (x0: number, x1: number, y0: number, y1: number, z0: number, z1: number, m: THREE.Material = mat, occludes = true) => {
      const mesh = box(new THREE.Vector3(x1 - x0, y1 - y0, z1 - z0), new THREE.Vector3((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), m);
      edges(mesh, line);
      mesh.userData.fid = f.id;
      target.add(mesh);
      parts.push(mesh);
      if (occludes) occluders.push(mesh);
      return mesh;
    };
    const { width: w, depth: d, height: h } = f, t = 0.025;
    if (f.type === "bookcase") {
      // Built with its open front toward +z, then turned so the front looks at the window (the
      // protected face); the footprint w x d stays as drawn in the plan. Books sit on each shelf.
      const front = shelfFront(win?.wall);
      const sideways = front.dx !== 0;
      const [lw, ld] = sideways ? [d, w] : [w, d];
      const turn = new THREE.Group();
      turn.position.set(w / 2, 0, d / 2);
      turn.rotation.y = front.yaw;
      const inner = new THREE.Group();
      inner.position.set(-lw / 2, 0, -ld / 2);
      turn.add(inner);
      group.add(turn);
      target = inner;
      part(0, lw, 0, h, 0, t);
      part(0, t, 0, h, 0, ld);
      part(lw - t, lw, 0, h, 0, ld);
      part(0, lw, h - t, h, 0, ld);
      part(0, lw, 0, 0.08, 0, ld);
      const shelves = Math.max(3, Math.round(h / 0.38));
      const gap = (h - 0.08 - t) / shelves;
      const books: [number, number, number, number, number][] = [];
      for (let s = 0; s < shelves; s++) {
        const base = 0.08 + s * gap;
        if (s > 0) part(t, lw - t, base - t / 2, base + t / 2, t, ld);
        for (let x = t + 0.01; x < lw - t - 0.08; ) {
          const bw = 0.025 + random() * 0.035, bh = gap * (0.55 + random() * 0.3);
          if (random() > 0.1) books.push([x, bw, base + t / 2, bh, 0.45 + random() * 0.25]);
          x += bw + 0.004;
        }
      }
      const tones = ["#e9e8ec", "#dcdbe1", "#efede6", "#d2d0d8", "#e4e2ef", "#c9c7d1"].map(c => new THREE.Color(c));
      const bookMesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ roughness: 0.9 }), books.length);
      books.forEach(([x, bw, y, bh, depthShare], i) => {
        const bd = (ld - t) * depthShare;
        bookMesh.setMatrixAt(i, new THREE.Matrix4().compose(new THREE.Vector3(x + bw / 2, y + bh / 2, ld - bd / 2 - 0.02), new THREE.Quaternion(), new THREE.Vector3(bw, bh, bd)));
        bookMesh.setColorAt(i, tones[Math.floor(random() * tones.length)]);
      });
      bookMesh.castShadow = bookMesh.receiveShadow = true;
      bookMesh.raycast = () => {};
      inner.add(bookMesh);
    } else if (f.type === "desk" || f.type === "table") {
      const leg = f.type === "table" ? 0.06 : 0.045, inset = 0.05;
      part(0, w, h - 0.035, h, 0, d);
      for (const [x, z] of [[inset, inset], [w - inset - leg, inset], [inset, d - inset - leg], [w - inset - leg, d - inset - leg]])
        part(x, x + leg, 0, h - 0.035, z, z + leg);
      if (f.type === "desk") part(inset, w - inset, h * 0.45, h - 0.035, inset, inset + 0.018);
    } else if (f.type === "cabinet") {
      part(0, w / 2 - 0.003, 0, h, 0, d);
      part(w / 2 + 0.003, w, 0, h, 0, d);
    } else if (f.type === "display") {
      const glass = new THREE.MeshStandardMaterial({ color: "#eeedfb", transparent: true, opacity: 0.22, roughness: 0.1, depthWrite: false });
      part(0, w, 0, h * 0.62, 0, d);
      part(0, w, h * 0.62, h, 0, d, glass, false).castShadow = false;
      part(w * 0.2, w * 0.45, h * 0.62, h * 0.62 + 0.04, d * 0.3, d * 0.7, new THREE.MeshStandardMaterial({ color: "#dcdbe1", roughness: 0.9 }));
      part(w * 0.55, w * 0.8, h * 0.62, h * 0.62 + 0.06, d * 0.25, d * 0.65, new THREE.MeshStandardMaterial({ color: "#e9e9e9", roughness: 0.9 }));
    } else part(0, w, 0, h, 0, d);
    // Selection is independent of protection grades: a tinted volume and a ground outline.
    // These decorations never participate in picking, shadowing or sunlight calculations.
    const selection = new THREE.Group();
    const tint = new THREE.Mesh(new THREE.BoxGeometry(w + 0.008, h + 0.008, d + 0.008),
      new THREE.MeshBasicMaterial({ color: ACTION, transparent: true, opacity: 0.14, depthWrite: false }));
    tint.position.set(w / 2, h / 2, d / 2);
    tint.raycast = () => {};
    selection.add(tint);
    const outline = new THREE.Shape();
    outline.moveTo(-0.065, 0.065);
    outline.lineTo(w + 0.065, 0.065);
    outline.lineTo(w + 0.065, -d - 0.065);
    outline.lineTo(-0.065, -d - 0.065);
    outline.closePath();
    const hole = new THREE.Path();
    hole.moveTo(-0.035, 0.035);
    hole.lineTo(-0.035, -d - 0.035);
    hole.lineTo(w + 0.035, -d - 0.035);
    hole.lineTo(w + 0.035, 0.035);
    hole.closePath();
    outline.holes.push(hole);
    const ring = new THREE.Mesh(new THREE.ShapeGeometry(outline), new THREE.MeshBasicMaterial({ color: ACTION, side: THREE.DoubleSide }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.015;
    ring.raycast = () => {};
    selection.add(ring);
    selection.visible = false;
    group.add(selection);
    scene.add(group);
    items.set(f.id, { f, group, mats, line, parts, selection });
  }

  // Protected regions are surfaces, as the server stores them. Only the grade chip is drawn (no
  // coloured face); the surface is kept for the light rays and the film preview.
  type RegionView = { r: Region; box: THREE.Box3; corner: THREE.Vector3; u: THREE.Vector3; v: THREE.Vector3; normal: THREE.Vector3; chip: HTMLDivElement };
  const regionViews: RegionView[] = [];
  for (const r of space.regions) {
    const { x, y, width: w, depth: d, bottom: lo, top: hi } = r;
    // corner, then the two edge vectors (u along the face, v up or southwards) and the outward normal.
    const [corner, u, v, normal] = {
      "남쪽 전면": [at(x, y + d, lo), new THREE.Vector3(w, 0, 0), new THREE.Vector3(0, hi - lo, 0), new THREE.Vector3(0, 0, 1)],
      "북쪽 후면": [at(x, y, lo), new THREE.Vector3(w, 0, 0), new THREE.Vector3(0, hi - lo, 0), new THREE.Vector3(0, 0, -1)],
      "동쪽 측면": [at(x + w, y, lo), new THREE.Vector3(0, 0, d), new THREE.Vector3(0, hi - lo, 0), new THREE.Vector3(1, 0, 0)],
      "서쪽 측면": [at(x, y, lo), new THREE.Vector3(0, 0, d), new THREE.Vector3(0, hi - lo, 0), new THREE.Vector3(-1, 0, 0)],
      "상단": [at(x, y, hi), new THREE.Vector3(w, 0, 0), new THREE.Vector3(0, 0, d), new THREE.Vector3(0, 1, 0)],
    }[r.furnitureId && r.face !== "상단" && win ? WINDOW_FACE[win.wall] : r.face];
    const chip = document.createElement("div");
    chip.className = `s3-chip g-${r.grade}`;
    chip.textContent = r.grade;
    const label = new CSS2DObject(chip);
    // Every grade chip sits over the middle of its furniture (or of a free area), whatever the face.
    const host = space.furniture.find(f => f.id === r.furnitureId);
    label.position.copy(host
      ? at(host.x + host.width / 2, host.y + host.depth / 2, host.height + 0.16)
      : at(r.x + r.width / 2, r.y + r.depth / 2, r.top + 0.16));
    scene.add(label);
    const box = new THREE.Box3().setFromPoints([corner, corner.clone().add(u).add(v)]).expandByVector(new THREE.Vector3(1, 1, 1).multiply(normal).multiplyScalar(0.01));
    regionViews.push({ r, box, corner, u, v, normal, chip });
  }
  // Furniture without a protection area is grade D (decided 2026-11-06), so it gets a D chip too.
  for (const f of space.furniture) {
    if (space.regions.some(r => r.furnitureId === f.id)) continue;
    const chip = document.createElement("div");
    chip.className = "s3-chip g-D";
    chip.textContent = "D";
    const label = new CSS2DObject(chip);
    label.position.copy(at(f.x + f.width / 2, f.y + f.depth / 2, f.height + 0.16));
    scene.add(label);
  }

  // The one north sign: painted on the ground outside the north wall, so it turns with the room.
  const northCanvas = document.createElement("canvas");
  northCanvas.width = northCanvas.height = 256;
  const nc = northCanvas.getContext("2d")!;
  nc.fillStyle = INK;
  nc.strokeStyle = INK;
  nc.lineWidth = 7;
  nc.lineCap = "round";
  nc.lineJoin = "round";
  nc.beginPath();
  nc.moveTo(128, 130);
  nc.lineTo(128, 30);
  nc.moveTo(104, 58);
  nc.lineTo(128, 30);
  nc.lineTo(152, 58);
  nc.stroke();
  nc.font = "500 64px 'Noto Sans KR', sans-serif";
  nc.textAlign = "center";
  nc.fillText("N", 128, 210);
  const northMark = new THREE.Mesh(new THREE.PlaneGeometry(0.48, 0.48), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(northCanvas), transparent: true, depthWrite: false }));
  northMark.rotation.x = -Math.PI / 2;
  northMark.position.copy(at(W / 2, -0.55, -0.09));
  northMark.visible = !planCamera;
  scene.add(northMark);

  // Sun: a disc with a soft glow, travelling along today's dashed sun path.
  const glowCanvas = document.createElement("canvas");
  glowCanvas.width = glowCanvas.height = 128;
  const g = glowCanvas.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,116,38,0.55)");
  grad.addColorStop(1, "rgba(255,116,38,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const sunGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(glowCanvas), transparent: true, opacity: 0, depthWrite: false }));
  sunGlow.scale.setScalar(2.6);
  const sunDisc = new THREE.Mesh(new THREE.SphereGeometry(0.26, 32, 16), new THREE.MeshBasicMaterial({ color: SUN, transparent: true, opacity: 0 }));
  sunDisc.add(sunGlow);
  scene.add(sunDisc);
  const pathMat = new THREE.LineDashedMaterial({ color: SUN, dashSize: 0.22, gapSize: 0.16, transparent: true, opacity: 0 });
  const sunPath = new THREE.Line(new THREE.BufferGeometry(), pathMat);
  scene.add(sunPath);
  const RADIUS = 6.5;
  const offsetFor = createOffsetCache();
  const sunDir = (day: number, hour: number) => {
    // Local clock time in the site's zone, with that date's offset (daylight saving included), looked up once per day.
    const utcOffset = offsetFor(site.timezone, new Date().getFullYear(), day);
    const { altitude, azimuth } = sunPosition(day, hour, site.latitude, site.longitude, utcOffset);
    const a = THREE.MathUtils.degToRad(altitude), z = THREE.MathUtils.degToRad(azimuth);
    return { dir: new THREE.Vector3(Math.cos(a) * Math.sin(z), Math.sin(a), -Math.cos(a) * Math.cos(z)), altitude, azimuth };
  };

  // Preview of the film cells for one sun direction, until the server plan arrives: a cell closes
  // when its ray into the room meets an A, B or C surface (grade caps need the server's irradiance).
  const cellsFor = (dir: THREE.Vector3, out: Uint8Array) => {
    out.fill(0);
    if (!win || dir.y <= 0 || dir.dot(winFrame.outward) <= 0.01) return out;
    const ray = new THREE.Ray(), into = dir.clone().negate();
    for (let i = 0; i < cellCount; i++) {
      ray.set(cellCenter(i), into);
      if (regionViews.some(v => v.r.grade !== "D" && ray.intersectsBox(v.box))) out[i] = 1;
    }
    return out;
  };
  // Share of each region's surface that the sun reaches (6 × 6 points), with or without closed cells.
  const raycaster = new THREE.Raycaster();
  raycaster.layers.enable(1);
  const winPlane = new THREE.Plane();
  const litFor = (dir: THREE.Vector3, cells: Uint8Array | null) => {
    const result: Record<string, number> = {};
    if (win) winPlane.setFromNormalAndCoplanarPoint(winFrame.outward, winFrame.origin);
    const q = new THREE.Vector3();
    for (const v of regionViews) {
      let sum = 0;
      const n = 36;
      if (dir.y > 0 && v.normal.dot(dir) > 0.02) for (let i = 0; i < 6; i++) for (let j = 0; j < 6; j++) {
        const p = v.corner.clone().addScaledVector(v.u, (i + 0.5) / 6).addScaledVector(v.v, (j + 0.5) / 6).addScaledVector(v.normal, 0.01);
        raycaster.set(p, dir);
        if (raycaster.intersectObjects(occluders, false).some(h => h.object.userData.fid !== v.r.furnitureId)) continue;
        if (cells && win && raycaster.ray.intersectPlane(winPlane, q)) {
          const c = Math.floor((q.clone().sub(winFrame.origin).dot(winFrame.along) / win.width) * film.cols);
          const r = Math.floor(((q.y - win.sill) / win.height) * film.rows);
          if (c >= 0 && r >= 0 && c < film.cols && r < film.rows && closes(cells[r * film.cols + c])) continue;
        }
        sum++;
      }
      result[v.r.id] = sum / n;
    }
    return result;
  };

  let level: Level = "basic";
  let litNow: Lit = { open: {}, film: {}, blocked: 0, cells: cellCount };
  let sunUp = true;
  const flip = win?.wall === "south" || win?.wall === "west";
  const setTime = (day: number, hour: number, server?: ServerMoment): Lit => {
    const e = server?.sun;
    const dir = e ? new THREE.Vector3(e[0], e[2], -e[1]).normalize() : sunDir(day, hour).dir;
    sun.position.copy(dir.clone().multiplyScalar(20));
    sunDisc.position.copy(dir.clone().multiplyScalar(RADIUS));
    sunDisc.visible = dir.y > -0.02;
    const pts: THREE.Vector3[] = [];
    for (let m = 4 * 60; m <= 20 * 60; m += 10) {
      const p = sunDir(day, m / 60).dir;
      if (p.y > -0.02) pts.push(p.multiplyScalar(RADIUS));
    }
    sunPath.geometry.setFromPoints(pts);
    sunPath.computeLineDistances();
    const fromServer = server?.cells && server.rows === film.rows && server.cols === film.cols ? server.cells : null;
    if (fromServer) {
      // Server row 0 is the top row; column 0 is the left one seen from inside (east end of a south wall).
      for (let i = 0; i < cellCount; i++) {
        const row = Math.floor(i / film.cols), col = i % film.cols;
        cellBlock[(film.rows - 1 - row) * film.cols + (flip ? film.cols - 1 - col : col)] = fromServer[i];
      }
    } else cellsFor(dir, cellBlock);
    const color = new THREE.Color();
    let blocked = 0;
    for (let i = 0; i < cellCount; i++) {
      if (closes(cellBlock[i])) cellMesh.setColorAt(i, color.set(STATE_COLOR[cellBlock[i]]));
      if (closes(cellBlock[i])) blocked++;
      cellTarget[i] = level === "film" && closes(cellBlock[i]) ? 1 : 0;
    }
    if (cellMesh.instanceColor) cellMesh.instanceColor.needsUpdate = true;
    sunUp = dir.y > 0;
    litNow = { open: litFor(dir, null), film: litFor(dir, cellBlock), blocked, cells: cellCount };
    return litNow;
  };

  // Hover (slight brighten) and click-to-select on furniture.
  let hovered: string | null = null, selected: string | null = null;
  const paint = () => {
    for (const [id, it] of items) {
      const on = id === selected;
      // The tint and ground ring read as depth cues only in 3D; seen from above they looked like a
      // wrong outline, so the plan views keep just the purple edges (or the editor's own SVG outline).
      it.selection.visible = on && view === "3d" && !planCamera;
      for (const m of it.mats) m.emissive.set(id === hovered || on ? "#ffffff" : "#000000").multiplyScalar(on ? 0.05 : 0.03);
      it.line.color.set(on ? ACTION : INK);
      it.line.opacity = on ? 1 : 0.42;
    }
  };
  const pointer = new THREE.Vector2();
  const pick = (e: PointerEvent) => {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const parts = [...items.values()].flatMap(it => it.parts);
    return (raycaster.intersectObjects(parts, false)[0]?.object.userData.fid as string | undefined) ?? null;
  };
  const onMove = (e: PointerEvent) => {
    if (e.buttons) return;
    const id = pick(e);
    if (id === hovered) return;
    hovered = id;
    // No label on hover: it covered the furniture. A click shows the size in the side panel.
    renderer.domElement.style.cursor = id ? "pointer" : "";
    paint();
  };
  let down: { x: number; y: number } | null = null;
  const onDown = (e: PointerEvent) => (down = { x: e.clientX, y: e.clientY });
  const onUp = (e: PointerEvent) => {
    if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return;
    const id = pick(e);
    select(id === selected ? null : id);
    events.onSelect(selected);
  };
  const onLeave = () => { hovered = null; paint(); };
  renderer.domElement.addEventListener("pointermove", onMove);
  renderer.domElement.addEventListener("pointerdown", onDown);
  renderer.domElement.addEventListener("pointerup", onUp);
  renderer.domElement.addEventListener("pointerleave", onLeave);
  const select = (id: string | null) => { selected = id; paint(); };

  // Camera moves are tweened in spherical coordinates so 2D ↔ 3D reads as one continuous motion.
  type Pose = { target: THREE.Vector3; radius: number; phi: number; theta: number; fov: number };
  const home = (): Pose => ({ target: at(W / 2, D / 2, 0.4), radius: Math.hypot(W, D) * 1.75 / Math.min(1, camera.aspect / 1.35), phi: 0.98, theta: 0.6, fov: 32 });
  // Sunlight steps look from the north-west, so the window, the sun and its path sit across the room.
  const sunView = (): Pose => ({ ...home(), radius: home().radius * 1.12, phi: 0.9, theta: -Math.PI / 2 - 0.72 });
  const top = (): Pose => {
    // Narrow lens for a near-flat plan; the room sits a little high to clear the time bar.
    const fov = 7, half = Math.max(D / 2 + 1.5, (W / 2 + 0.9) / camera.aspect);
    return { target: at(W / 2, D / 2 + 0.3, 0), radius: half / Math.tan(THREE.MathUtils.degToRad(fov / 2)), phi: 0.0001, theta: 0, fov };
  };
  let view = startView;
  const viewPose = () => (view === "2d" ? top() : level === "basic" ? home() : sunView());
  let tween: { from: Pose; to: Pose; start: number; ms: number } | null = null;
  const pose = (): Pose => {
    const s = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
    return { target: controls.target.clone(), radius: s.radius, phi: s.phi, theta: s.theta, fov: camera.fov };
  };
  const apply = (p: Pose) => {
    controls.target.copy(p.target);
    camera.position.copy(p.target).add(new THREE.Vector3().setFromSphericalCoords(p.radius, p.phi, p.theta));
    camera.fov = p.fov;
    camera.updateProjectionMatrix();
    camera.lookAt(p.target);
  };
  const go = (to: Pose) => {
    hovered = null;
    if (reduced) return apply(to);
    const from = pose();
    to.theta = from.theta + Math.atan2(Math.sin(to.theta - from.theta), Math.cos(to.theta - from.theta));
    tween = { from, to, start: performance.now(), ms: 1100 };
  };

  const resize = () => {
    const { clientWidth: w, clientHeight: h } = host;
    renderer.setSize(w, h);
    labels.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    if (planCamera && b && w && h) {
      // Fit like the SVG above it (preserveAspectRatio "meet"): keep the plan's scale, centre it, and
      // widen the unused axis, so furniture, outlines and protected-face lines land on the same spot.
      const viewW = W + b.left + b.right, viewH = D + b.top + b.bottom;
      const cx = (b.right - b.left) / 2, cz = (b.bottom - b.top) / 2;
      const scale = Math.max(viewW / w, viewH / h);
      planCamera.left = cx - (w * scale) / 2;
      planCamera.right = cx + (w * scale) / 2;
      planCamera.top = -cz + (h * scale) / 2;
      planCamera.bottom = -cz - (h * scale) / 2;
      planCamera.updateProjectionMatrix();
    }
  };
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();
  controls.enableRotate = view === "3d";
  apply(viewPose());
  
  // Smoothly follow level targets each frame (lights, sun visuals, cells, wall fades).
  const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
  let lastFrame = performance.now();
  const toCam = new THREE.Vector3();
  renderer.setAnimationLoop(() => {
    const now = performance.now(), dt = Math.min((now - lastFrame) / 1000, 0.05), k = reduced ? 1 : 1 - Math.exp(-dt * 7);
    lastFrame = now;
    const sunny = level !== "basic";
    hemi.intensity = lerp(hemi.intensity, sunny ? (sunUp ? 1.25 : 0.6) : 1.1, k);
    key.intensity = lerp(key.intensity, sunny ? 0 : 1.3, k);
    sun.intensity = lerp(sun.intensity, sunny && sunUp ? 5.2 : 0, k);
    scene.environmentIntensity = lerp(scene.environmentIntensity, sunny ? 0.6 : 0.65, k);
    (sunDisc.material as THREE.MeshBasicMaterial).opacity = lerp((sunDisc.material as THREE.MeshBasicMaterial).opacity, sunny ? 1 : 0, k);
    sunGlow.material.opacity = (sunDisc.material as THREE.MeshBasicMaterial).opacity;
    pathMat.opacity = lerp(pathMat.opacity, sunny ? 0.7 : 0, k);
    // ShadowMaterial draws shadows whatever the light's strength, so the ground fades with the step.
    groundMat.opacity = lerp(groundMat.opacity, sunny && sunUp ? 0.03 : 0, k);
    filmGridMat.opacity = lerp(filmGridMat.opacity, level === "film" ? 0.22 : 0, k);
    for (const v of regionViews) {
      const sunlit = level !== "basic" && ((level === "film" ? litNow.film : litNow.open)[v.r.id] ?? 0) > 0.01;
      v.chip.classList.toggle("lit", sunlit);
    }
    let moved = false;
    for (let i = 0; i < cellCount; i++) {
      if (cellNow[i] === cellTarget[i]) continue;
      const step = reduced ? 1 : dt * 5;
      cellNow[i] = cellTarget[i] > cellNow[i] ? Math.min(cellTarget[i], cellNow[i] + step) : Math.max(cellTarget[i], cellNow[i] - step);
      writeCell(i);
      moved = true;
    }
    if (moved) {
      cellMesh.instanceMatrix.needsUpdate = true;
      cellMesh.computeBoundingSphere();
    }
    if (tween) {
      const t = Math.min(1, (now - tween.start) / tween.ms), e = ease(t);
      const { from, to } = tween;
      apply({ target: from.target.clone().lerp(to.target, e), radius: lerp(from.radius, to.radius, e), phi: lerp(from.phi, to.phi, e), theta: lerp(from.theta, to.theta, e), fov: lerp(from.fov, to.fov, e) });
      if (t === 1) tween = null;
    } else controls.update();
    for (const w of wallParts) {
      // Seen from straight above, every wall stays solid so the plan reads like a drawing.
      const outside = view === "3d" && w.normal.dot(toCam.copy(camera.position).sub(w.center)) > 0;
      w.fade = lerp(w.fade, outside ? 0 : 1, k);
      for (const m of w.fill) {
        m.opacity = w.fade;
        m.depthWrite = w.fade > 0.98;
      }
      w.line.opacity = 0.16 + 0.3 * w.fade;
    }
    renderer.render(scene, planCamera ?? camera);
    labels.render(scene, planCamera ?? camera);
  });

  // Light rays read world matrices, which are otherwise only filled in by the first render.
  scene.updateMatrixWorld(true);
  return {
    setLevel(next) {
      const moves = view === "3d" && (next === "basic") !== (level === "basic");
      level = next;
      if (moves) go(viewPose());
      for (let i = 0; i < cellCount; i++) cellTarget[i] = level === "film" && closes(cellBlock[i]) ? 1 : 0;
    },
    setTime,
    setView(next) {
      view = next;
      paint();
      controls.enableRotate = next === "3d";
      go(viewPose());
    },
    reset() {
      go(viewPose());
    },
    select,
    previewFurniture(furniture) {
      for (const [id, item] of items) {
        const f = furniture?.id === id ? furniture : item.f;
        item.group.position.copy(at(f.x, f.y));
      }
    },
    dispose() {
      renderer.setAnimationLoop(null);
      observer.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      renderer.domElement.removeEventListener("pointerleave", onLeave);
      scene.traverse(o => {
        const m = o as THREE.Mesh;
        m.geometry?.dispose();
        for (const mat of ([] as THREE.Material[]).concat(m.material ?? [])) mat.dispose();
      });
      pmrem.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      labels.domElement.remove();
    },
  };
}
