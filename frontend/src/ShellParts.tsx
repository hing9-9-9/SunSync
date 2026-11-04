import type { ReactNode } from "react";
import type { Space } from "./model";
import { syncStatus } from "./spaceSync";

export function Icon({ name }: { name: "sun" }) {
  return <img className="icon" src={`/design/${name}.svg`} alt="" />;
}
export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={`card ${className}`}>{children}</section>;
}
/** Server sync state shown on every space in the web and mobile lists. */
export function SyncBadge({ space }: { space: Space }) {
  const status = syncStatus(space);
  return (
    <small className={`sync-badge ${status}`}>
      {status === "saved" ? "서버에 저장됨" : status === "pending" ? "서버에 저장 중" : "위치 입력 후 서버 저장"}
    </small>
  );
}
export function SyncRetry({ space, failed, onRetry }: { space: Space; failed: string[]; onRetry: (id: string) => void }) {
  if (!failed.includes(space.id)) return null;
  return (
    <button className="sync-retry" onClick={() => onRetry(space.id)}>
      저장 안 됨 · 다시 저장
    </button>
  );
}
