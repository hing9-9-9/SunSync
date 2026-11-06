import test from "node:test";
import assert from "node:assert/strict";
import { blankSpace } from "./model.ts";
import {
  saveServerSpace,
  mergeServerSpaces,
  listServerSpaces,
  SpaceApiError,
} from "./spaceApi.ts";

test("new and existing spaces use POST and revision-protected PUT", async (t) => {
  const calls: { url: string; init?: RequestInit }[] = [];
  t.mock.method(
    globalThis,
    "fetch",
    async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(init?.body as string, { status: 200 });
    },
  );
  const space = blankSpace();
  await saveServerSpace(space);
  await saveServerSpace({ ...space, serverRevision: 3 });
  assert.equal(calls[0].init?.method, "POST");
  assert.equal(calls[1].init?.method, "PUT");
  assert.match(calls[1].url, /\/api\/ui\/spaces\//);
  assert.equal(JSON.parse(calls[1].init?.body as string).serverRevision, 3);
});

test("failed server saves reject without pretending local storage succeeded", async (t) => {
  t.mock.method(
    globalThis,
    "fetch",
    async () =>
      new Response(JSON.stringify({ detail: "다른 곳에서 수정" }), {
        status: 409,
      }),
  );
  await assert.rejects(
    saveServerSpace(blankSpace()),
    (e: unknown) => e instanceof SpaceApiError && e.status === 409,
  );
});

test("lists collect pages and exclude local-only legacy spaces", async (t) => {
  let calls = 0;
  const remote = { ...blankSpace(), serverRevision: 2 };
  t.mock.method(
    globalThis,
    "fetch",
    async () =>
      new Response(
        JSON.stringify({
          spaces: calls++ ? [] : [remote],
          unavailable: [],
          has_more: calls === 1,
        }),
      ),
  );
  assert.deepEqual((await listServerSpaces()).spaces, [remote]);
  assert.equal(calls, 2);
  const local = blankSpace();
  assert.deepEqual(
    mergeServerSpaces([{ ...remote, serverRevision: 1 }, local], [remote]),
    [remote],
  );
});
