import { useId, useState } from "react";

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
  const descriptionId = useId();
  const [touched, setTouched] = useState(false);
  const issue = !disabled && touched ? (!Number.isFinite(value) ? "숫자를 입력해 주세요." : value < min ? `${min} 이상으로 입력해 주세요.` : max !== undefined && value > max ? `${max} 이하로 입력해 주세요.` : "") : "";
  return (
    <label className="field number">
      <span>{label}</span>
      <input
        aria-invalid={!!issue}
        aria-describedby={issue ? descriptionId : undefined}
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
      {issue && <small id={descriptionId} className="field-warning" role="alert">{issue}</small>}
    </label>
  );
}
