import { useState } from "react";

export default function NumberField({
  label,
  value,
  onChange,
  min = 0,
  max,
  step = 0.01,
  precision = 2,
  disabled = false,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  step?: number;
  precision?: number;
  disabled?: boolean;
}) {
  const [touched, setTouched] = useState(false);
  // No message under the box: a row of fields keeps its shape. An empty box is reported when the person moves on (formIssues);
  // a typed number outside the range only turns the box red.
  const issue = !disabled && touched && Number.isFinite(value) && (value < min || (max !== undefined && value > max));
  return (
    <label className="field number">
      <span>{label}</span>
      <input
        aria-invalid={issue}
        onBlur={() => setTouched(true)}
        type="number"
        inputMode="decimal"
        min={min}
        max={max}
        step={step}
        required
        disabled={disabled}
        value={Number.isFinite(value) ? Number(value.toFixed(precision)) : ""}
        onChange={(e) =>
          { setTouched(true); onChange(e.target.value === "" ? NaN : e.target.valueAsNumber); }
        }
      />
    </label>
  );
}
