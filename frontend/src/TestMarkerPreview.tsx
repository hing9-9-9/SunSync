import { useId, useRef } from "react";
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
      <p>ArUco ID {id}</p>
      {hasPattern ? (
        <svg viewBox={`-1 -1 ${width + 2} ${cells.length + 2}`} role="img"
          aria-label={`이번 촬영에 배정된 ArUco 마커 ID ${id}`} shapeRendering="crispEdges">
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
