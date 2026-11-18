import { useId, useRef, useState } from "react";
import type { CaptureMarker } from "./measureApi";
import "./TestMarkerPreview.css";

export default function TestMarkerPreview({ session }: { session: CaptureMarker | null }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  if (!session) return null;

  const { code_id: id, start_row: top, start_col: left, marker_rows: rows, marker_cols: cols } = session.marker;
  const cells = top != null && left != null && rows != null && cols != null && rows > 0 && cols > 0
    ? session.film_cells.slice(top, top + rows).map(row => row.slice(left, left + cols))
    : [];
  const width = cells[0]?.length ?? 0;
  const hasPattern = cells.length === rows && width === cols && cells.every(row => row.length === width);

  return <>
    <button type="button" className="secondary test-marker-button" aria-haspopup="dialog"
      onClick={() => dialog.current?.showModal()}>
      이번 촬영 마커 보기
    </button>
    <dialog ref={dialog} className="test-marker-dialog" aria-labelledby={titleId}>
      <h2 id={titleId}>이번 촬영 마커</h2>
      <p>마커 ID {id}</p>
      {hasPattern ? (
        <svg viewBox={`-1 -1 ${width + 2} ${cells.length + 2}`} role="img"
          aria-label={`이번 촬영에 배정된 마커 ID ${id}`} shapeRendering="crispEdges">
          <rect x="-1" y="-1" width={width + 2} height={cells.length + 2} fill="white" />
          {cells.flatMap((row, y) => row.map((opacity, x) => opacity >= 0.5
            ? <rect key={`${x}:${y}`} x={x} y={y} width="1" height="1" fill="black" />
            : null))}
        </svg>
      ) : <p>마커 이미지를 표시하지 못했어요.</p>}
      <p>노트북에서 사용할 때는 이 이미지를 캡처해 옮겨 주세요.</p>
      <button type="button" className="primary" autoFocus onClick={() => dialog.current?.close()}>닫기</button>
    </dialog>
  </>;
}

export function TestMarkerSize({ sideM, onApply }: {
  sideM?: number;
  onApply: (sideM?: number) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [value, setValue] = useState("");
  const centimeters = Number(value);
  const valid = Number.isFinite(centimeters) && centimeters > 0 && centimeters <= 60;
  return <>
    <button type="button" className="secondary test-marker-button" aria-haspopup="dialog" onClick={() => {
      setValue(sideM === undefined ? "" : String(sideM * 100));
      dialog.current?.showModal();
    }}>테스트 마커 크기{sideM === undefined ? "" : ` · ${+(sideM * 100).toFixed(2)}cm`}</button>
    <dialog ref={dialog} className="test-marker-dialog" aria-labelledby={titleId}>
      <h2 id={titleId}>테스트 마커 크기</h2>
      <p>노트북에 띄운 마커의 흰 여백을 제외한 검정 외곽 한 변을 재 주세요.</p>
      <p>테스트용 공간에서 사용해 주세요. 크기를 바꾸면 이번 세션의 촬영이 초기화돼요.</p>
      <form onSubmit={event => {
        event.preventDefault();
        if (!valid) return;
        onApply(centimeters === 60 ? undefined : centimeters / 100);
        dialog.current?.close();
      }}>
        <label className="field"><span>검정 외곽 한 변 (cm)</span>
          <input type="number" inputMode="decimal" max={60} step="any" required
            value={value} onChange={event => setValue(event.target.value)} />
        </label>
        <div className="actions">
          <button type="submit" className="primary" disabled={!valid}>이번 촬영에 적용</button>
          {sideM !== undefined && <button type="button" className="secondary" onClick={() => {
            onApply(undefined);
            dialog.current?.close();
          }}>기본 60cm로 되돌리기</button>}
          <button type="button" className="text-button" onClick={() => dialog.current?.close()}>닫기</button>
        </div>
      </form>
    </dialog>
  </>;
}
