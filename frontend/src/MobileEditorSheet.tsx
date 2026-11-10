import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

export default function MobileEditorSheet({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    if (open && !node?.open) {
      node?.showModal();
      // Start on the sheet itself so no button (닫기) shows a focus ring the moment it opens.
      node?.focus();
    }
    else if (!open && node?.open) node.close();
  }, [open]);
  return (
    <dialog
      ref={dialog}
      tabIndex={-1}
      className="mobile-editor-sheet"
      aria-labelledby="mobile-editor-sheet-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClose={onClose}
      onClick={(e) => {
        if (e.target !== e.currentTarget) return;
        const r = e.currentTarget.getBoundingClientRect();
        if (
          e.clientX < r.left ||
          e.clientX > r.right ||
          e.clientY < r.top ||
          e.clientY > r.bottom
        )
          onClose();
      }}
    >
      <header className="mobile-editor-sheet-head">
        <h2 id="mobile-editor-sheet-title">{title}</h2>
        <button type="button" className="text-button" onClick={onClose}>
          닫기
        </button>
      </header>
      <div className="mobile-editor-sheet-body">{children}</div>
      <footer>
        <button type="button" className="primary" onClick={onClose}>
          도면으로 돌아가기
        </button>
      </footer>
    </dialog>
  );
}
