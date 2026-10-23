import type { Space } from "./model";

export const pages = [
  "home",
  "spaces",
  "register",
  "capture",
  "processing",
  "review",
  "protect",
  "detail",
  "guide",
  "more",
  "account",
] as const;
export type Page = (typeof pages)[number];
export type BrowserLocation = {
  page: Page;
  guideIndex: number | null;
  activeId: string | null;
  draft: Space | null;
};
export const isEditingPage = (page: Page) =>
  ["register", "capture", "processing", "review", "protect"].includes(page);

export function shouldConfirmNavigation(
  hasDraft: boolean,
  current: Page,
  next: Page,
): boolean {
  return hasDraft && current !== next && next !== "account";
}

export function locationUrl(route: BrowserLocation): string {
  if (route.page === "home") return "#/";
  if (route.page === "guide" && route.guideIndex !== null)
    return `#/guide/${route.guideIndex + 1}`;
  if (route.page === "detail" && route.activeId)
    return `#/detail/${encodeURIComponent(route.activeId)}`;
  return `#/${route.page}`;
}
export function locationKey(route: BrowserLocation): string {
  return `${locationUrl(route)}:${isEditingPage(route.page) ? (route.draft?.id ?? "") : ""}`;
}
export function readLocation(state: unknown, hash: string): BrowserLocation {
  const raw =
    state && typeof state === "object" && "sunSyncNavigation" in state
      ? (state.sunSyncNavigation as Partial<BrowserLocation>)
      : null;
  const parts = hash.replace(/^#\/?/, "").split("/");
  const page = raw?.page ?? parts[0];
  const validPage: Page = pages.includes(page as Page)
    ? (page as Page)
    : "home";
  const index =
    raw?.guideIndex ??
    (validPage === "guide" && parts[1] ? Number(parts[1]) - 1 : null);
  let hashId: string | null = null;
  try {
    if (validPage === "detail" && parts[1])
      hashId = decodeURIComponent(parts[1]);
  } catch {
    /* Invalid URLs fall back to the space list. */
  }
  return {
    page: validPage,
    guideIndex:
      typeof index === "number" &&
      Number.isInteger(index) &&
      index >= 0 &&
      index < 6
        ? index
        : null,
    activeId: typeof raw?.activeId === "string" ? raw.activeId : hashId,
    draft:
      raw?.draft &&
      typeof raw.draft.id === "string" &&
      Array.isArray(raw.draft.furniture) &&
      Array.isArray(raw.draft.regions) &&
      Array.isArray(raw.draft.zones)
        ? raw.draft
        : null,
  };
}

export function resolveLocation(
  route: BrowserLocation,
  signedIn: boolean,
  currentDraft: Space | null,
  spaces: Space[],
): BrowserLocation {
  const result = { ...route };
  if (!signedIn && !["home", "guide"].includes(result.page))
    return { ...result, page: "home", draft: null };
  if (isEditingPage(result.page)) {
    result.draft =
      currentDraft?.id === result.draft?.id ? currentDraft : result.draft;
    if (!result.draft) result.page = "home";
  } else if (currentDraft) result.draft = currentDraft;
  if (result.page === "detail" && !spaces.some((s) => s.id === result.activeId))
    result.page = "spaces";
  return result;
}
