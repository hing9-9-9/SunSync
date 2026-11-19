import type { ReactNode } from "react";
import type { SpaceApp } from "./useSpaceApp";
import { CaptureHelp } from "./CaptureHelp";

/**
 * Dialog contents shared by the web and the app layouts (delete, reload, sign out, discard, capture help).
 * The login and add-furniture dialogs differ between the layouts, so the add-furniture content comes in as `addFurniture`.
 */
export function DialogBodies({ app, addFurniture, captureHelpStep }: { app: SpaceApp; addFurniture: ReactNode; captureHelpStep?: number }) {
  const { active, confirmDelete, confirmDeleteTempDraft, deleteSpace, deleting, dismissModal, leaveEditor, modal, pendingDelete, reloadServerDraft, setModal, signOut, tempDeleteId, tempDrafts } = app;
  return (
    <>
          {modal === "delete-space" && active && (
            <>
              <h2>'{active.name}'을 삭제할까요?</h2>
              <p className="muted">
                이 공간의 가구·보호 영역 정보도 함께 사라지고 되돌릴 수 없습니다.
              </p>
              <button className="primary destructive" disabled={deleting} onClick={deleteSpace}>
                {deleting ? "삭제 중…" : "삭제"}
              </button>
              <button className="secondary" onClick={dismissModal}>
                취소
              </button>
            </>
          )}
          {modal === "delete-item" && pendingDelete && (
            <>
              <h2>{pendingDelete.title}</h2>
              <p className="muted">
                {pendingDelete.body ? `${pendingDelete.body} ` : ""}삭제한 내용은 저장할 때 반영됩니다.
              </p>
              <button className="primary destructive" onClick={confirmDelete}>
                삭제
              </button>
              <button className="secondary" onClick={dismissModal}>
                취소
              </button>
            </>
          )}
          {modal === "delete-temp" && (
            <>
              <h2>
                '{tempDrafts.find((t) => t.space.id === tempDeleteId)?.space.name || "이름 없는 공간"}'의 작성 중인 내용을 삭제할까요?
              </h2>
              <p className="muted">
                이 기기에 임시 저장한 내용이 사라지고 되돌릴 수 없습니다. 서버에 저장된 공간은 그대로 남습니다.
              </p>
              <button className="primary destructive" onClick={confirmDeleteTempDraft}>
                삭제
              </button>
              <button className="secondary" onClick={dismissModal}>
                취소
              </button>
            </>
          )}
          {modal === "reload-server" && (
            <>
              <h2>서버의 최신 내용으로 바꿀까요?</h2>
              <p className="muted">
                저장하지 않은 현재 수정 내용은 사라집니다.
              </p>
              <button className="primary" onClick={reloadServerDraft}>
                최신 내용 불러오기
              </button>
              <button className="secondary" onClick={dismissModal}>
                계속 편집
              </button>
            </>
          )}
          {modal === "logout" && (
            <>
              <h2>로그아웃하시겠어요?</h2>
              <button
                className="primary destructive"
                onClick={() => void signOut()}
              >
                로그아웃
              </button>
              <button className="secondary" onClick={() => setModal(null)}>
                취소
              </button>
            </>
          )}
          {modal === "add-furniture" && addFurniture}
          {modal === "capture-help" && <CaptureHelp initialStep={captureHelpStep} onClose={() => setModal(null)} />}
          {modal === "discard" && (
            <>
              <button type="button" className="dialog-close" aria-label="계속 편집" onClick={() => setModal(null)}>
                <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
              </button>
              <h2>저장하지 않은 변경 내용이 있습니다</h2>
              <p className="muted">
                임시 저장하면 입력한 데까지 이 기기에 남고, 내 공간의 작성 중인 공간에서 이어서 편집할 수 있습니다.
              </p>
              <button className="primary" onClick={() => leaveEditor(true)}>
                임시 저장하고 나가기
              </button>
              <button className="primary destructive" onClick={() => leaveEditor(false)}>
                저장하지 않고 나가기
              </button>
            </>
          )}
    </>
  );
}
