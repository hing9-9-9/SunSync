import { useEffect, useRef, useState } from "react";
import "./SpaceMenu.css";

export type SpaceMenuIcon = "layout" | "info" | "external" | "trash";
export type SpaceMenuItem = { label: string; onSelect: () => void; danger?: boolean; icon?: SpaceMenuIcon };

const ICON_PATH: Record<SpaceMenuIcon, string> = {
  layout: "M4 20h4L19 9l-4-4L4 16v4ZM13.5 6.5l4 4",
  info: "M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6M17.5 3.5a2.1 2.1 0 0 1 3 3L13 14l-4 1 1-4 7.5-7.5Z",
  external: "M14 4h6v6M20 4l-9 9M18 14v5H5V6h5",
  trash: "M4 7h16M9 7V5.2A1.2 1.2 0 0 1 10.2 4h3.6A1.2 1.2 0 0 1 15 5.2V7M6.2 7l.8 11.2A2 2 0 0 0 9 20h6a2 2 0 0 0 2-1.8L17.8 7M10 11v5M14 11v5",
};
export function SpaceMenuGlyph({ name }: { name: SpaceMenuIcon }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICON_PATH[name]} /></svg>
  );
}

// One "..." button that opens a short action list. Closes on outside click, Escape, or choosing an item.
export function SpaceMenu({ label, items }: { label: string; items: SpaceMenuItem[] }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <div className="space-menu" ref={box}>
      <button type="button" className="space-menu-trigger" aria-label={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="19" cy="12" r="1.7" /></svg>
      </button>
      {open && (
        <div className="space-menu-list" role="menu">
          {items.map((item) => (
            <button key={item.label} type="button" role="menuitem" className={item.danger ? "danger" : undefined} onClick={() => { setOpen(false); item.onSelect(); }}>
              {item.icon && <SpaceMenuGlyph name={item.icon} />}
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
