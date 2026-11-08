export type SessionUser = { name: string; email: string };
export type Session =
  | { status: "in"; user: SessionUser; authEnabled: boolean }
  | { status: "out" }
  // The auth routes are missing (older server) or the server cannot be reached.
  | { status: "unavailable" };

export const EXPIRED_MESSAGE = "로그인이 만료됐어요. 다시 로그인해 주세요.";
export const LOGIN_FAILED_MESSAGE = "로그인하지 못했어요. 다시 시도해 주세요.";

/** A failed login is announced at once (alert); an ended session is a calm status. */
export function noticeRole(message: string): "alert" | "status" {
  return message === LOGIN_FAILED_MESSAGE ? "alert" : "status";
}

/** The page the person is on, as an internal path the server may redirect back to. */
export function currentPath(): string {
  const l = globalThis.location;
  return l ? l.pathname + l.search + l.hash : "/";
}

export function googleLoginUrl(next: string = currentPath()): string {
  return "/api/auth/google/login?next=" + encodeURIComponent(next);
}

const validUser = (u: unknown): u is SessionUser =>
  !!u && typeof (u as SessionUser).name === "string" && typeof (u as SessionUser).email === "string";

export async function fetchSession(signal?: AbortSignal): Promise<Session> {
  let response: Response;
  try {
    response = await fetch("/api/auth/session", { signal, cache: "no-store", headers: { Accept: "application/json" } });
  } catch (error) {
    if (signal?.aborted) throw error;
    return { status: "unavailable" };
  }
  if (response.status === 401) return { status: "out" };
  if (!response.ok) return { status: "unavailable" }; // 404 (no route yet), 5xx
  const data = await response.json().catch(() => null);
  if (!validUser(data?.user)) return { status: "unavailable" };
  return { status: "in", user: { name: data.user.name, email: data.user.email }, authEnabled: data.auth_enabled !== false };
}

export async function logout(): Promise<void> {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch {
    /* Offline: the screen still returns to the login page. */
  }
}

// Any request answered 401 tells the app the session ended.
const listeners = new Set<() => void>();
export function notifyUnauthorized() {
  for (const listener of [...listeners]) listener();
}
export function onUnauthorized(listener: () => void): () => void {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}
