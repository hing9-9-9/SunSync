import { fromSceneFurnitureType, resolveWindow } from "./model.ts";
import type { Space, Wall } from "./model.ts";
import type { VisionEstimatedItem } from "./measureApi.ts";
import { faceSegment, previewBox } from "./visionGeometry.ts";
import "./VisionPreview.css";

type Props = {
  space: Space;
  items: VisionEstimatedItem[];
  selected: number | null;
  onSelect: (index: number) => void;
};

/** Small plan of the room with the furniture the photo found, before anything is saved. */
export default function VisionPreview({ space, items, selected, onSelect }: Props) {
  const win = resolveWindow(space);
  const wall: Wall = win?.wall ?? "south";
  const pad = 0.4;
  const { width: W, depth: D } = space;
  const windowLine = win && (() => {
    const a = win.offset, b = win.offset + win.width;
    if (wall === "south") return { x1: a, x2: b, y1: D, y2: D };
    if (wall === "north") return { x1: a, x2: b, y1: 0, y2: 0 };
    const x = wall === "east" ? W : 0;
    return { x1: x, x2: x, y1: a, y2: b };
  })();
  const boxes = items.map((item) => previewBox(item, D, wall));
  const text = Math.max(0.22, Math.max(W, D) / 28);
  return <figure className="vision-preview">
    <svg viewBox={`${-pad} ${-pad} ${W + pad * 2} ${D + pad * 2}`} role="img" aria-label={`찾은 가구 ${items.length}개의 위치 미리보기`}>
      <rect className="vp-room" x={0} y={0} width={W} height={D} />
      {boxes.map((b, i) => {
        const cx = b.x + b.width / 2, cy = b.y + b.depth / 2;
        const on = selected === i;
        const seg = faceSegment(b);
        const turn = ((((-b.yaw + 180) % 360) + 360) % 360) - 180; // the box's rotation on screen; labels never read upside down
        const kind = fromSceneFurnitureType(items[i].occluder.type);
        return <g
          key={items[i].occluder.occluder_id}
          transform={`rotate(${-b.yaw} ${cx} ${cy})`}
          className={`vp-item${on ? " is-selected" : ""}`}
          role="button"
          tabIndex={0}
          aria-label={`${i + 1}번 가구 ${on ? "선택됨" : "선택"}`}
          aria-pressed={on}
          data-kind={kind}
          onClick={() => onSelect(i)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(i); } }}
        >
          <rect className="vp-box" x={b.x} y={b.y} width={b.width} height={b.depth} vectorEffect="non-scaling-stroke" />
          {b.protectedTop && <rect className="vp-top" x={b.x} y={b.y} width={b.width} height={b.depth} />}
          {b.protectedFace && <line className="vp-face" {...seg} vectorEffect="non-scaling-stroke" />}
          <text className="vp-no" x={cx} y={cy} fontSize={text} textAnchor="middle" dominantBaseline="central"
            transform={Math.abs(turn) > 90 ? `rotate(180 ${cx} ${cy})` : undefined}>
            {String(i + 1).padStart(2, "0")}
          </text>
        </g>;
      })}
      {windowLine && <line className="vp-window" {...windowLine} />}
    </svg>
  </figure>;
}
