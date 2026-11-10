import { sunlightPolygon } from "./sunlight";
import { floorSunlight } from "./floorSun";

// The drawn light fades in and out around sunrise and sunset instead of switching at 06:00 / 18:00.
const duskFade = (hour: number) => Math.max(0, Math.min(1, hour - 5.5, 18.5 - hour));
import { useEffect, useId, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { moveFurnitureInside, clamp, isWidthWall, resolveWindow, uid } from "./model";
import type { Furniture, Space, Region } from "./model";
import type { SceneApi } from "./Space3DScene";
import "./Space3DView.css";

type Props = {
  space: Space;
  selectedFurniture: string | null;
  selectedRegion: string | null;
  onFurniture: (id: string) => void;
  onRegion: (id: string) => void;
  onRegions?: (regions: Region[]) => void;
  onFurnitureMove?: (furniture: Furniture) => void;
  tool?: "furniture" | "region";
  hour?: number;
  showDemoSun?: boolean;
  exposedRegionIds?: string[];
  modeled?: boolean;
};
type Gesture =
  | { kind: "draw" | "move" | "resize"; x: number; y: number; region: Region }
  | { kind: "furniture"; x: number; y: number; furniture: Furniture };

// Top-view symbols so furniture types can be told apart without the 3D view.
function FurnitureSymbol({ f, ox, oy }: { f: Furniture; ox: number; oy: number }) {
  const x = ox + f.x, y = oy + f.y;
  const line = { stroke: "var(--c-a6adba)", strokeWidth: "1pt", vectorEffect: "non-scaling-stroke" as const, pointerEvents: "none" as const };
  // A line along the long side at fraction t across the short side.
  const along = (t: number, inset: number) => f.width >= f.depth
    ? { x1: x + inset, x2: x + f.width - inset, y1: y + f.depth * t, y2: y + f.depth * t }
    : { y1: y + inset, y2: y + f.depth - inset, x1: x + f.width * t, x2: x + f.width * t };
  switch (f.type) {
    case "bookcase":
      return <>{[1 / 3, 2 / 3].map(t => <line key={t} {...line} {...along(t, 0.06)} />)}</>;
    case "desk":
      return <line {...line} {...along(0.85, 0.08)} />;
    case "cabinet":
      return <><line {...line} x1={x} y1={y} x2={x + f.width} y2={y + f.depth} /><line {...line} x1={x + f.width} y1={y} x2={x} y2={y + f.depth} /></>;
    case "display": {
      const inset = Math.min(0.08, f.width / 4, f.depth / 4);
      return <rect {...line} fill="none" x={x + inset} y={y + inset} width={f.width - inset * 2} height={f.depth - inset * 2} />;
    }
    default:
      return null;
  }
}
export default function Plan({
  space,
  selectedFurniture,
  selectedRegion,
  onFurniture,
  onRegion,
  onRegions,
  onFurnitureMove,
  tool = "furniture",
  hour = 14,
  showDemoSun = true,
  exposedRegionIds = [],
  modeled = false,
}: Props) {
  const svg = useRef<SVGSVGElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const live = useRef<Region | null>(null);
  const modelHost = useRef<HTMLDivElement>(null);
  const modelApi = useRef<SceneApi | null>(null);
  const [modelReady, setModelReady] = useState(false);
  const selectedModel = useRef(selectedFurniture);
  useEffect(() => { selectedModel.current = selectedFurniture; }, [selectedFurniture]);
  // A dragged piece is previewed locally and committed once, so a drag is one edit.
  const [moving, setMoving] = useState<Furniture | null>(null);
  // Margins only where something is drawn outside the room (the window and its label); elsewhere
  // the room's edges line up with the size and north labels above the plan.
  const wall = resolveWindow(space)?.wall;
  const edge = 0.05, labelRoom = 0.45;
  const left = wall === "west" ? labelRoom : edge, right = wall === "east" ? labelRoom : edge;
  const top = wall === "north" ? labelRoom : edge, bottom = wall === "south" ? labelRoom : edge;
  // Names and blocking-time edits do not change the model. Keep its canvas alive while typing.
  const modelSnapshot = JSON.stringify({
    id: "plan", name: "", building: "", floor: "", source: "manual", revision: 0,
    width: space.width, depth: space.depth, height: space.height,
    zone: space.zone, zones: [space.zone], windowDimensions: space.windowDimensions, site: space.site,
    furniture: space.furniture.filter(f => f.zone === space.zone).map(f => ({ ...f, name: "" })),
    regions: space.regions.filter(r => r.zone === space.zone).map(r => ({ ...r, name: "" })),
  });
  useEffect(() => {
    if (!modeled) return;
    let stopped = false;
    let scene: SceneApi | undefined;
    void import("./Space3DScene").then(({ createSpaceScene }) => {
      if (stopped || !modelHost.current) return;
      try {
        scene = createSpaceScene(modelHost.current, JSON.parse(modelSnapshot) as Space,
          { onSelect: () => {}, planBounds: { left, right, top, bottom } }, "2d");
        modelApi.current = scene;
        scene.select(selectedModel.current);
        setModelReady(true);
      } catch (error) {
        console.warn("2D model unavailable; keeping the interactive plan", error);
        setModelReady(false);
      }
    });
    return () => { stopped = true; scene?.dispose(); modelApi.current = null; };
  }, [modeled, modelSnapshot, left, right, top, bottom]);
  useEffect(() => { modelApi.current?.select(selectedFurniture); }, [selectedFurniture, space]);
  useEffect(() => { modelApi.current?.previewFurniture(moving); }, [moving]);
  // Keep plan labels readable at phone and desktop sizes, in SVG user units.
  const [textSize, setTextSize] = useState(0.25);
  useEffect(() => {
    const node = svg.current;
    if (!node) return;
    const measure = () => {
      const scale = node.getScreenCTM()?.a;
      if (scale && scale > 0) setTextSize(13 / scale);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    return () => observer.disconnect();
  }, [space.width, space.depth, wall]);
  function point(e: ReactPointerEvent<SVGSVGElement>) {
    const matrix = svg.current?.getScreenCTM();
    if (!matrix) return { x: 0, y: 0 };
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(
      matrix.inverse(),
    );
    return {
      x: clamp(p.x - left, 0, space.width),
      y: clamp(p.y - top, 0, space.depth),
    };
  }
  function start(e: ReactPointerEvent<SVGSVGElement>) {
    const t = e.target as Element;
    const p = point(e);
    // A furniture-linked protected face sits on top of its furniture, so it drags the host.
    const hostId = t.getAttribute("data-furniture") ??
      space.regions.find((r) => r.id === t.getAttribute("data-region"))?.furnitureId;
    const dragged = space.furniture.find((f) => f.id === hostId);
    if (onFurnitureMove && tool === "furniture" && dragged) {
      onFurniture(dragged.id);
      gesture.current = { kind: "furniture", ...p, furniture: { ...dragged } };
      svg.current?.setPointerCapture(e.pointerId);
      e.preventDefault();
      return;
    }
    if (!onRegions) return;
    const handle = t.getAttribute("data-handle");
    const id = t.getAttribute("data-region");
    const r = space.regions.find((v) => v.id === id);
    if (r && !r.furnitureId) {
      onRegion(r.id);
      gesture.current = {
        kind: handle ? "resize" : "move",
        ...p,
        region: { ...r },
      };
    } else if (tool === "region" && !t.getAttribute("data-furniture")) {
      const region: Region = {
        id: uid(),
        name: `보호 영역 ${space.regions.filter((r) => !r.furnitureId).length + 1}`,
        zone: space.zone,
        furnitureId: null,
        ...p,
        width: 0.01,
        depth: 0.01,
        bottom: 0,
        top: Math.min(0.75, space.height),
        face: "상단",
        grade: "A",
      };
      gesture.current = { kind: "draw", ...p, region };
      live.current = region;
      onRegions([...space.regions, region]);
      onRegion(region.id);
    }
    if (gesture.current) {
      svg.current?.setPointerCapture(e.pointerId);
      e.preventDefault();
    }
  }
  function move(e: ReactPointerEvent<SVGSVGElement>) {
    const g = gesture.current;
    if (!g) return;
    const p = point(e);
    if (g.kind === "furniture") {
      const f = g.furniture;
      setMoving(moveFurnitureInside({
        ...f,
        x: f.x + p.x - g.x,
        y: f.y + p.y - g.y,
      }, space));
      return;
    }
    if (!onRegions) return;
    const r = { ...g.region };
    if (g.kind === "draw") {
      r.x = Math.min(p.x, g.x);
      r.y = Math.min(p.y, g.y);
      r.width = Math.max(0.01, Math.abs(p.x - g.x));
      r.depth = Math.max(0.01, Math.abs(p.y - g.y));
    }
    if (g.kind === "move") {
      r.x = clamp(r.x + p.x - g.x, 0, space.width - r.width);
      r.y = clamp(r.y + p.y - g.y, 0, space.depth - r.depth);
    }
    if (g.kind === "resize") {
      r.width = clamp(p.x - r.x, 0.1, space.width - r.x);
      r.depth = clamp(p.y - r.y, 0.1, space.depth - r.y);
    }
    live.current = r;
    onRegions(
      space.regions.some((v) => v.id === r.id)
        ? space.regions.map((v) => (v.id === r.id ? r : v))
        : [...space.regions, r],
    );
  }
  function end() {
    if (moving) {
      onFurnitureMove?.(moving);
      setMoving(null);
    }
    if (
      gesture.current?.kind === "draw" &&
      live.current &&
      onRegions &&
      (live.current.width < 0.1 || live.current.depth < 0.1)
    )
      onRegions(space.regions.filter((r) => r.id !== live.current?.id));
    gesture.current = null;
    live.current = null;
  }
  const original = moving && space.furniture.find((f) => f.id === moving.id);
  // Real window light when the space has a window position and a site; else the old demo patch.
  // Drawn with the server prediction too: the patch shows where the light falls, the prediction which areas it reaches.
  const floorSun = floorSunlight(space, hour);
  const sunMask = `floor-sun-${useId().replace(/:/g, "")}`;
  const pts = (poly: { x: number; y: number }[]) => poly.map((p) => `${p.x + left},${p.y + top}`).join(" ");
  const shownFurniture = space.furniture.map((f) => (f.id === moving?.id ? moving : f));
  // Grade D adds no blocking, so the plan draws it as plain furniture (the inspector still shows D).
  const visibleRegions = space.regions.filter((r) => r.grade !== "D");
  const shownRegions = original
    ? visibleRegions.map((r) =>
        r.furnitureId === original.id
          ? { ...r, x: r.x + moving.x - original.x, y: r.y + moving.y - original.y }
          : r,
      )
    : visibleRegions;
  // Window drawn on its wall; offsets run from the plan's left (west) or top (north) end.
  const win = resolveWindow(space);
  const windowLine = win && (() => {
    const along = isWidthWall(win.wall) ? left : top;
    const a = along + win.offset, b = a + win.width, label = 0.28;
    if (win.wall === "south") return { line: { x1: a, x2: b, y1: top + space.depth, y2: top + space.depth }, label: { x: (a + b) / 2, y: top + space.depth + label } };
    if (win.wall === "north") return { line: { x1: a, x2: b, y1: top, y2: top }, label: { x: (a + b) / 2, y: top - label } };
    const x = win.wall === "east" ? left + space.width : left;
    return { line: { x1: x, x2: x, y1: a, y2: b }, label: { x: x + (win.wall === "east" ? label : -label), y: (a + b) / 2 } };
  })();
  return (
    <div className={`plan-wrap${modeled && modelReady ? " modeled-plan" : ""}`}>
      <div className="plan-caption">
        <span>
          {space.width} × {space.depth} m
        </span>
        <span>N ↑</span>
      </div>
      <div className="plan-surface">
      {modeled && <div ref={modelHost} className="plan-model-layer" aria-hidden="true" />}
      <svg
        ref={svg}
        className={`plan ${tool === "region" ? "drawing" : ""} ${onFurnitureMove && tool === "furniture" ? "dragging-furniture" : ""}`}
        viewBox={`0 0 ${left + space.width + right} ${top + space.depth + bottom}`}
        preserveAspectRatio="xMidYMin meet"
        role="group"
        aria-label="공간 평면도"
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
      >
        <rect
          x={left}
          y={top}
          width={space.width}
          height={space.depth}
          fill="var(--c-f7f8fa)"
          stroke="var(--c-aeb4c0)"
          strokeWidth="1pt"
          vectorEffect="non-scaling-stroke"
        />
        {(!modeled || !onFurnitureMove) && (floorSun ? <>
          {/* Window light on the floor for this hour; furniture shadows are cut out of it. */}
          <mask id={sunMask} maskUnits="userSpaceOnUse">
            <polygon points={pts(floorSun.patch)} fill="#fff" />
            {floorSun.shadows.map((sh, i) => <polygon key={i} points={pts(sh)} fill="#000" />)}
          </mask>
          <polygon points={pts(floorSun.patch)} fill="#ff7426" fillOpacity={0.22} mask={`url(#${sunMask})`} pointerEvents="none" />
        </> : showDemoSun && <polygon
          points={sunlightPolygon(space, Math.min(Math.max(hour, 6), 17.99)).map(p => `${p.x + left},${p.y + top}`).join(" ")}
          fill="var(--c-fff2d3)"
          opacity={duskFade(hour)}
          pointerEvents="none"
        />)}
        {shownFurniture
          .filter((f) => f.zone === space.zone)
          .map((f, i) => (
            <g
              key={f.id}
              transform={`rotate(${-(f.yaw ?? 0)} ${left+f.x+f.width/2} ${top+f.y+f.depth/2})`}
              role="button"
              tabIndex={0}
              aria-label={`${f.name} 선택`}
              aria-pressed={selectedFurniture === f.id}
              onClick={() => onFurniture(f.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onFurniture(f.id);
                }
              }}
            >
              <rect
                data-furniture={f.id}
                x={left + f.x}
                y={top + f.y}
                width={f.width}
                height={f.depth}
                fill={f.id === selectedFurniture ? "var(--c-d8dbe3)" : "var(--c-e4e8ef)"}
                stroke={f.id === selectedFurniture ? "var(--c-action)" : "var(--c-a6adba)"}
                strokeWidth="1pt"
                vectorEffect="non-scaling-stroke"
              />
              <FurnitureSymbol f={f} ox={left} oy={top} />
              <text
                x={left + f.x + f.width / 2}
                y={top + f.y + f.depth / 2}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={textSize}
                fill="var(--c-52596a)"
                pointerEvents="none"
              >
                {String(i + 1).padStart(2, "0")}
              </text>
            </g>
          ))}
        {shownRegions
          .filter((r) => r.zone === space.zone)
          .map((r) => (
            <g
              key={r.id}
              transform={(() => {const f=space.furniture.find(f=>f.id===r.furnitureId);return f ? `rotate(${-(f.yaw ?? 0)} ${left+f.x+f.width/2} ${top+f.y+f.depth/2})` : undefined;})()}
              role="button"
              tabIndex={0}
              aria-label={`${r.name} 선택`}
              aria-pressed={r.id === selectedRegion}
              onClick={() => onRegion(r.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onRegion(r.id);
                }
              }}
            >
              <rect
                data-region={r.id}
                data-linked={r.furnitureId ? "" : undefined}
                x={left + r.x}
                y={top + r.y}
                width={r.width}
                height={r.depth}
                fill={exposedRegionIds.includes(r.id) ? "var(--c-e8ad33)" : "var(--c-8b7ef4)"}
                // A region on furniture is shown by its protected face line, not by tinting the furniture.
                fillOpacity={r.furnitureId ? "0" : r.id === selectedRegion ? ".28" : ".18"}
                // A furniture region shares the furniture outline, so only free regions draw their own.
                stroke={r.furnitureId ? "none" : r.id === selectedRegion ? "var(--c-action)" : "var(--c-9c93e1)"}
                strokeWidth="1pt"
                vectorEffect="non-scaling-stroke"
              />
              {!r.furnitureId && r.width >= .7 && r.depth >= .3 && <text x={left + r.x + r.width / 2} y={top + r.y + r.depth / 2} textAnchor="middle" dominantBaseline="central" fontSize={Math.min(textSize, r.width / Math.max(r.name.length * 0.65, 1))} fill="var(--c-51488b)" pointerEvents="none">{r.name}</text>}
              {!r.furnitureId && r.id === selectedRegion && onRegions && (
                <rect
                  data-region={r.id}
                  data-handle="resize"
                  x={left + r.x + r.width - 0.13}
                  y={top + r.y + r.depth - 0.13}
                  width=".26"
                  height=".26"
                  rx=".04"
                  fill="transparent"
                  style={{ cursor: "nwse-resize" }}
                />
              )}
            </g>
          ))}
        {windowLine && (
          <>
            <line
              {...windowLine.line}
              stroke="var(--c-6861e8)"
              strokeWidth=".06"
            />
            <text
              {...windowLine.label}
              fontSize={textSize}
              textAnchor="middle"
              dominantBaseline="central"
              fill="var(--c-747986)"
            >
              창
            </text>
          </>
        )}
      </svg>
      </div>
      <div className="plan-legend">
        <span>
          <i />
          가구
        </span>
        <span>
          <i className="purple" />
          보호 영역
        </span>
        {(!modeled || !onFurnitureMove) && <span>
          <i className="yellow" />
          {showDemoSun ? "예상 햇빛(시뮬레이션)" : "예측 시각의 직사광선 노출"}
        </span>}
      </div>
    </div>
  );
}
