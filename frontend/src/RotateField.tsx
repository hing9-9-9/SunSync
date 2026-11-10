import NumberField from "./NumberField";

const turn = (deg: number) => ((Math.round(deg * 10) / 10) % 360 + 360) % 360;

/**
 * Furniture rotation: one angle (degrees, counter-clockwise seen from above) that can be typed,
 * plus quarter-turn buttons. Any angle is valid; oblique furniture is saved and drawn as is.
 */
export default function RotateField({ value, onChange }: { value: number; onChange: (deg: number) => void }) {
  return (
    <div className="rotate-field">
      <NumberField label="왼쪽으로 돌린 각도 (°)" value={turn(value)} min={0} max={359.9} step={1} precision={1}
        onChange={(n) => { onChange(turn(Number.isFinite(n) ? n : 0)); }} />
      <div className="furniture-actions">
        <button type="button" className="secondary" onClick={() => onChange(turn(value - 90))}>오른쪽 90°</button>
        <button type="button" className="secondary" onClick={() => onChange(turn(value + 90))}>왼쪽 90°</button>
      </div>
    </div>
  );
}
