import type { Space } from "./model.ts";
import type { TempDraft } from "./navigation.ts";

// Local-only work of the previous person is parked here while someone else is signed in,
// and comes back when that person signs in again.
export type Stash = { spaces: Space[]; tempDrafts: TempDraft[] };
export const stashKey = (email: string) => `sunsync-ui-stash:${email.trim().toLowerCase()}`;

/** Server copies are dropped (they return from the server); everything local-only is kept for the stash. */
export function splitForStash(spaces: Space[], draft: Space | null, tempDrafts: TempDraft[]): Stash {
  const savedAt = new Date().toISOString();
  const drafts = [...tempDrafts];
  // An open draft becomes a temporary draft so it survives as the person's own unsaved work.
  if (draft && !drafts.some((t) => t.space.id === draft.id)) drafts.push({ space: draft, page: "register", savedAt });
  return { spaces: spaces.filter((s) => !s.serverRevision), tempDrafts: drafts };
}

export function mergeStash(current: Stash, stash: Stash): Stash {
  const spaceIds = new Set(current.spaces.map((s) => s.id));
  const draftIds = new Set(current.tempDrafts.map((t) => t.space.id));
  return {
    spaces: [...current.spaces, ...stash.spaces.filter((s) => !spaceIds.has(s.id))],
    tempDrafts: [...current.tempDrafts, ...stash.tempDrafts.filter((t) => !draftIds.has(t.space.id))],
  };
}

export const isEmptyStash = (s: Stash) => !s.spaces.length && !s.tempDrafts.length;

function readStash(storage: Pick<Storage, "getItem">, email: string): Stash | null {
  try {
    const raw = JSON.parse(storage.getItem(stashKey(email)) ?? "null");
    if (!raw || !Array.isArray(raw.spaces) || !Array.isArray(raw.tempDrafts)) return null;
    return { spaces: raw.spaces, tempDrafts: raw.tempDrafts };
  } catch {
    return null;
  }
}

/** Parks the previous person's local work under their email (added to anything already parked). */
export function saveStash(storage: Pick<Storage, "getItem" | "setItem" | "removeItem">, email: string, stash: Stash) {
  if (!email.trim()) return;
  try {
    const merged = mergeStash(stash, readStash(storage, email) ?? { spaces: [], tempDrafts: [] });
    if (isEmptyStash(merged)) storage.removeItem(stashKey(email));
    else storage.setItem(stashKey(email), JSON.stringify(merged));
  } catch {
    /* storage blocked */
  }
}

/** Returns what was parked for this person and removes it from storage. */
export function takeStash(storage: Pick<Storage, "getItem" | "removeItem">, email: string): Stash | null {
  if (!email.trim()) return null;
  const stash = readStash(storage, email);
  try {
    storage.removeItem(stashKey(email));
  } catch {
    /* storage blocked */
  }
  return stash && !isEmptyStash(stash) ? stash : null;
}
