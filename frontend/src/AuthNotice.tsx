import { useEffect, useState, type ReactNode } from "react";
import { noticeRole } from "./auth";
import "./AuthNotice.css";

/** The text arrives a moment after mount: a live region announces text added to it, not text that was already there. */
const FILL_DELAY_MS = 50;
function useAfterMount(value: string) {
  const [shown, setShown] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setShown(value), FILL_DELAY_MS);
    return () => clearTimeout(timer);
  }, [value]);
  return shown;
}

/** One empty alert region and one empty status region stay in the page; the text goes into the matching one. */
function Regions({ shown }: { shown: string }) {
  const role = shown ? noticeRole(shown) : "";
  return (
    <>
      <p className="auth-notice" role="alert">{role === "alert" ? shown : ""}</p>
      <p className="auth-notice" role="status">{role === "status" ? shown : ""}</p>
    </>
  );
}

/** The description line of a login surface. A notice takes its place (same box, nothing moves) and the description returns without it. */
export function AuthLead({ description, message, className }: { description: string; message: string; className?: string }) {
  const shown = useAfterMount(message);
  return (
    <div className="auth-lead" data-notice={shown ? "on" : undefined}>
      <p className={className}>{description}</p>
      <Regions shown={shown} />
    </div>
  );
}

/** For a login screen without a description: the notice sits under the button, outside the layout flow. */
export function AuthAction({ message, children }: { message: string; children: ReactNode }) {
  const shown = useAfterMount(message);
  return (
    <div className="auth-action">
      {children}
      <Regions shown={shown} />
    </div>
  );
}
