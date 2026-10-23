import test from "node:test";
import assert from "node:assert/strict";
import { blankSpace } from "./model.ts";
import { mergeServerSpaces, SpaceApiError } from "./spaceApi.ts";
import { saveErrorMessage, syncStatus, uploadLocalSpaces } from "./spaceSync.ts";
import type { Space } from "./model.ts";

const site = { latitude: 37.5, longitude: 127, timezone: "Asia/Seoul", northConfirmed: true };
const local = (name: string): Space => ({ ...blankSpace(), name, site } as Space);

test("sync status separates server, pending and location-less spaces", () => {
  assert.equal(syncStatus({ ...local("a"), serverRevision: 2 }), "saved");
  assert.equal(syncStatus(local("b")), "pending");
  assert.equal(syncStatus({ ...local("c"), site: undefined } as Space), "needs-site");
});

test("a space the server lost is kept and becomes a new upload", () => {
  const lost = { ...local("lost"), serverRevision: 4, serverSceneId: "scene" };
  const [kept] = mergeServerSpaces([lost], []);
  assert.equal(kept.name, "lost");
  assert.equal(kept.serverRevision, undefined);
  assert.equal(syncStatus(kept), "pending");
  assert.deepEqual(mergeServerSpaces([lost], [], [{ scene_id: lost.id, reason: "x" }]), []);
});

test("upload saves pending spaces and skips saved or location-less ones", async () => {
  const sent: string[] = [];
  const result = await uploadLocalSpaces(
    [local("a"), { ...local("b"), serverRevision: 1 }, { ...local("c"), site: undefined } as Space],
    async (s) => {
      sent.push(s.name);
      return { ...s, serverRevision: 1 };
    },
  );
  assert.deepEqual(sent, ["a"]);
  assert.equal(result.saved[0].serverRevision, 1);
  assert.deepEqual(result.failed, []);
});

test("409 takes the server copy; other errors are marked failed", async () => {
  const a = local("a"),
    b = local("b");
  const result = await uploadLocalSpaces(
    [a, b],
    async (s) => {
      throw new SpaceApiError(s === a ? 409 : 422, "x");
    },
    async (id) => ({ ...a, id, serverRevision: 7 }),
  );
  assert.deepEqual(result.saved.map((s) => s.serverRevision), [7]);
  assert.deepEqual(result.failed, [b.id]);
});

test("offline stops the upload and leaves spaces pending", async () => {
  let calls = 0;
  const result = await uploadLocalSpaces([local("a"), local("b")], async () => {
    calls++;
    throw new SpaceApiError(0, "offline");
  });
  assert.equal(calls, 1);
  assert.deepEqual(result, { saved: [], failed: [] });
});

test("offline save message tells people their edits are kept", () => {
  assert.match(saveErrorMessage(new SpaceApiError(0, "x"), "f"), /서버가 돌아오면 다시 저장/);
  assert.equal(saveErrorMessage(new SpaceApiError(409, "충돌"), "f"), "충돌");
});
