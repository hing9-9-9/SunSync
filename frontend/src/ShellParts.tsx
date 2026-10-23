import type { ReactNode } from "react";

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
