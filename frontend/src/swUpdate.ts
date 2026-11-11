/** Registers the service worker and tells the person when a newer build has taken over. */

export type ServiceWorkerContainerLike = {
  controller: unknown;
  addEventListener(type: "controllerchange", listener: () => void): void;
};

/**
 * Calls `onNewVersion` once when a worker replaces the one that controlled this page.
 * The first worker of a page that had none is not a new version.
 */
export function watchForNewVersion(container: ServiceWorkerContainerLike, onNewVersion: () => void) {
  const hadController = Boolean(container.controller);
  let announced = false;
  container.addEventListener("controllerchange", () => {
    if (!hadController || announced) return;
    announced = true;
    onNewVersion();
  });
}

/** The script of a page's entry point, as written in its index.html (the file name carries a hash). */
export function entryScript(html: string): string | null {
  const tag = html.match(/<script\b[^>]*\btype="module"[^>]*>/i)?.[0];
  return tag?.match(/\bsrc="([^"]+)"/i)?.[1] ?? null;
}

/**
 * True when the server's index.html starts a different script than the one this page runs.
 * A failed request says nothing, so it is not "outdated".
 */
export async function pageIsOutdated(runningScript: string | null, fetchHtml: () => Promise<string>) {
  if (!runningScript) return false;
  try {
    const latest = entryScript(await fetchHtml());
    return latest !== null && latest !== runningScript;
  } catch {
    return false;
  }
}

export const CHECK_EVERY_MS = 5 * 60 * 1000;

/** A page that stays open (a phone app in the background) asks again when it comes back, at most this often. */
export function shouldCheckAgain(lastCheck: number, now: number, every = CHECK_EVERY_MS) {
  return now - lastCheck >= every;
}

export const UPDATE_NOTICE_ID = "sunsync-update-notice";

/** A small notice at the bottom of the page. It does not reload by itself: unsaved input would be lost. */
export function showUpdateNotice(doc: Document, reload: () => void) {
  if (doc.getElementById(UPDATE_NOTICE_ID)) return;
  const box = doc.createElement("div");
  box.id = UPDATE_NOTICE_ID;
  box.setAttribute("role", "status");
  box.setAttribute("aria-live", "polite");
  box.style.cssText =
    "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:2147483000;display:flex;align-items:center;gap:12px;max-width:calc(100vw - 32px);padding:10px 12px 10px 16px;border:1px solid rgba(0,0,0,.16);border-radius:14px;background:#fff;color:#1b1b1b;font:500 14px/1.4 inherit;box-shadow:0 6px 24px rgba(0,0,0,.14)";
  const text = doc.createElement("span");
  text.textContent = "새 버전이 있어요. 저장한 뒤 새로고침해 주세요.";
  const refresh = doc.createElement("button");
  refresh.type = "button";
  refresh.textContent = "새로고침";
  refresh.style.cssText =
    "min-height:36px;padding:6px 14px;border:0;border-radius:999px;background:#1b1b1b;color:#fff;font:inherit;cursor:pointer";
  refresh.addEventListener("click", reload);
  const later = doc.createElement("button");
  later.type = "button";
  later.textContent = "나중에";
  later.style.cssText =
    "min-height:36px;padding:6px 10px;border:0;border-radius:999px;background:transparent;color:inherit;font:inherit;cursor:pointer";
  later.addEventListener("click", () => box.remove());
  box.append(text, refresh, later);
  doc.body.append(box);
}

export function registerServiceWorker(win: Window = window) {
  const doc = win.document;
  const running = () => {
    const src = doc.querySelector('script[type="module"][src]')?.getAttribute("src") ?? null;
    return src ? new URL(src, win.location.href).pathname : null;
  };
  const announceIfOutdated = async () => {
    const outdated = await pageIsOutdated(running(), async () => {
      const response = await win.fetch("/index.html", { cache: "no-store" });
      return response.text();
    });
    if (outdated) showUpdateNotice(doc, () => win.location.reload());
  };
  // A lazily loaded part of an older page is gone from the server after a deployment.
  win.addEventListener("vite:preloadError", () => showUpdateNotice(doc, () => win.location.reload()));

  const container = win.navigator.serviceWorker;
  if (!container) return;
  win.addEventListener("load", () => {
    container
      .register("/sw.js")
      .then((registration) => {
        let last = Date.now();
        doc.addEventListener("visibilitychange", () => {
          if (doc.visibilityState !== "visible") return;
          if (!shouldCheckAgain(last, Date.now())) return;
          last = Date.now();
          void registration.update().catch(() => {});
        });
      })
      .catch(() => {
        // The online UI remains usable when offline installation is unavailable.
      });
  });
  watchForNewVersion(container, () => void announceIfOutdated());
}
