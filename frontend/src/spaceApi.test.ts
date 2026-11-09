import test from "node:test";
import assert from "node:assert/strict";
import { blankSpace } from "./model.ts";
import {
  saveServerSpace,
  mergeServerSpaces,
  listServerSpaces,
  predictSunpath,
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

test("401 notifies once, throws the expired message and is never an empty listing", async (t) => {
  const { onUnauthorized } = await import("./auth.ts");
  let notified = 0;
  const off = onUnauthorized(() => notified++);
  t.mock.method(globalThis, "fetch", async () => new Response("<html>login</html>", { status: 401 }));
  await assert.rejects(listServerSpaces(), (e: unknown) => e instanceof SpaceApiError && e.status === 401 && e.message === "로그인이 만료됐어요. 다시 로그인해 주세요.");
  off();
  assert.equal(notified, 1);
});

test("FR-01 overlap errors show names without exposing internal IDs", async (t) => {
  t.mock.method(globalThis, "fetch", async () =>
    new Response(JSON.stringify({ detail: {
      code: "FURNITURE_OVERLAP",
      message: "보호 영역과 가구가 겹쳐 있어 예측할 수 없습니다.",
      target_id: "ui_region_secret",
      target_name: "서가 3 보호 영역",
      related_target_id: "ui_furniture_secret",
      related_target_name: "서가 3",
      context: { sample_index: 20 },
    } }), { status: 422, headers: { "Content-Type": "application/json" } }),
  );
  await assert.rejects(
    predictSunpath("scene-1", 1, "2026-11-09T00:00:00+09:00"),
    (error: unknown) => error instanceof SpaceApiError
      && error.message.includes("서가 3")
      && error.message.includes("서가 3 보호 영역")
      && !error.message.includes("ui_region_secret")
      && !error.message.includes("ui_furniture_secret"),
  );
});

test("FR-01 calculation limits explain what can be reduced", async (t) => {
  t.mock.method(globalThis, "fetch", async () =>
    new Response(JSON.stringify({ detail: {
      code: "INTERSECTION_LIMIT_EXCEEDED",
      message: "공간이 너무 복잡해 예측 계산 한도를 초과했습니다.",
      context: { estimated_checks: 6, max_checks: 5 },
    } }), { status: 422, headers: { "Content-Type": "application/json" } }),
  );
  await assert.rejects(
    predictSunpath("scene-1", 1, "2026-11-09T00:00:00+09:00"),
    (error: unknown) => error instanceof SpaceApiError
      && error.message.includes("시간 간격")
      && error.message.includes("보호 영역")
      && error.message.includes("가구 수"),
  );
});

test("FR-05 virtual film schedule apply, state and logs call scene film endpoints", async (t) => {
  const {
    applyFilmSchedule,
    fetchFilmState,
    fetchFilmLogs,
  } = await import("./spaceApi.ts");
  const calls: string[] = [];
  t.mock.method(globalThis, "fetch", async (url: string) => {
    calls.push(url);
    if (url.includes("/film-schedule-apply")) {
      return new Response(
        JSON.stringify({
          scene_id: "scene-1",
          scene_revision: 1,
          applied_at: "2026-11-09T03:00:00Z",
          windows: [
            {
              scene_id: "scene-1",
              window_id: "ui_window",
              scene_revision: 1,
              mode: "schedule",
              updated_at: "2026-11-09T03:00:00Z",
              cells: [],
            },
          ],
          applied_logs: [],
          target_unmet_count: 0,
        }),
        { status: 200 },
      );
    }
    if (url.includes("/film-state")) {
      return new Response(
        JSON.stringify({
          scene_id: "scene-1",
          scene_revision: 1,
          windows: [],
        }),
        { status: 200 },
      );
    }
    return new Response(
      JSON.stringify({
        scene_id: "scene-1",
        total: 1,
        entries: [
          {
            log_id: 1,
            scene_id: "scene-1",
            scene_revision: 1,
            window_id: "ui_window",
            timestamp: "2026-11-09T03:00:00Z",
            row: 0,
            col: 0,
            state: "opaque",
            opacity: 1,
            reason: "grade_b",
            status: "applied",
            note: "적용됨",
          },
        ],
      }),
      { status: 200 },
    );
  });

  const applied = await applyFilmSchedule(
    "scene-1",
    {
      plan_version: "0.1.0",
      scene_id: "scene-1",
      scene_revision: 1,
      start_time: "2026-11-09T00:00:00+09:00",
      end_time: "2026-11-10T00:00:00+09:00",
      windows: [],
    },
    "2026-11-09T12:00:00+09:00",
  );
  assert.equal(applied.scene_id, "scene-1");
  const state = await fetchFilmState("scene-1");
  assert.equal(state.scene_id, "scene-1");
  const logs = await fetchFilmLogs("scene-1", { limit: 10 });
  assert.equal(logs.entries.length, 1);
  assert.equal(calls.length, 3);
});
