import type { Space } from "./model.ts";
import { getServerSpace, saveServerSpace, SpaceApiError, userMessage } from "./spaceApi.ts";

/** Saved on the server, waiting to upload, or blocked until the building location is entered. */
export type SyncStatus = "saved" | "pending" | "needs-site";
export const syncStatus = (s: Space): SyncStatus =>
  s.serverRevision ? "saved" : s.site ? "pending" : "needs-site";

/** Save failures keep the edits on this device; offline adds when to try again. */
export function saveErrorMessage(error: unknown, fallback: string) {
  if (error instanceof SpaceApiError && error.status === 0)
    return "서버에 연결할 수 없어 저장하지 못했습니다. 고친 내용은 이 기기에 남아 있으니, 서버가 돌아오면 다시 저장을 눌러 주세요.";
  return userMessage(error, fallback);
}

/**
 * Uploads spaces that exist only on this device. A 409 means the server already has the
 * space, so its copy is taken. Going offline stops the run; the rest stay pending.
 */
export async function uploadLocalSpaces(
  spaces: Space[],
  save: (s: Space) => Promise<Space> = saveServerSpace,
  fetchOne: (id: string) => Promise<Space> = getServerSpace,
) {
  const saved: Space[] = [],
    failed: string[] = [];
  for (const space of spaces.filter((s) => syncStatus(s) === "pending")) {
    try {
      saved.push(await save(space));
    } catch (error) {
      // Offline or signed out: stop; the rest stay pending and nothing is dropped.
      if (!(error instanceof SpaceApiError) || error.status === 0 || error.status === 401) break;
      if (error.status === 409) {
        try {
          saved.push(await fetchOne(space.id));
          continue;
        } catch {
          // Fall through: shown as not saved, with a retry button.
        }
      }
      failed.push(space.id);
    }
  }
  return { saved, failed };
}
