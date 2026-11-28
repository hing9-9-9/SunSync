import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

/**
 * The edits are already in the draft while the sheet is open. "저장" keeps them and closes; "닫기" (also Esc and a tap
 * outside) closes at once when nothing changed, otherwise asks whether to keep them. The delete button sits apart, under 저장.
 */
export default function MobileEditorSheet({
  open,
  title,
  onClose,
  onSave,
  onDiscard,
  isChanged,
  onDelete,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  onSave: () => void;
  onDiscard: () => void;
  /** Read when the person asks to close, so it always reflects the latest edits. */
  isChanged: () => boolean;
  onDelete?: { label: string; run: () => void };
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [asking, setAsking] = useState(false);
  const requestClose = () => (isChanged() ? setAsking(true) : onClose());
  if (!open && asking) setAsking(false);
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
        requestClose();
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
          requestClose();
      }}
    >
      <header className="mobile-editor-sheet-head">
        <h2 id="mobile-editor-sheet-title">{title}</h2>
        <button type="button" className="text-button" onClick={requestClose}>
          닫기
        </button>
      </header>
      <div className="mobile-editor-sheet-body">{children}</div>
      <footer>
        {asking ? (
          <>
            <p className="sheet-ask" role="alert">변경한 내용을 저장할까요?</p>
            <button type="button" className="primary" onClick={onSave}>저장</button>
            <button type="button" className="sheet-delete" onClick={onDiscard}>저장 안 함</button>
            <button type="button" className="text-button" onClick={() => setAsking(false)}>계속 편집</button>
          </>
        ) : (
          <>
            <button type="button" className="primary" onClick={onSave}>저장</button>
            {onDelete && <button type="button" className="sheet-delete" onClick={onDelete.run}>{onDelete.label}</button>}
          </>
        )}
      </footer>
    </dialog>
  );
}
