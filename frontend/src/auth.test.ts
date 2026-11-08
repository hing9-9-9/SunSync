import test from "node:test";
import assert from "node:assert/strict";
import { EXPIRED_MESSAGE, LOGIN_FAILED_MESSAGE, fetchSession, googleLoginUrl, logout, noticeRole, notifyUnauthorized, onUnauthorized } from "./auth.ts";

const mockFetch = (t: test.TestContext, impl: (url: string, init?: RequestInit) => Promise<Response>) =>
  t.mock.method(globalThis, "fetch", impl as typeof fetch);

test("a signed-in session returns the Google user", async (t) => {
  mockFetch(t, async () => new Response(JSON.stringify({ auth_enabled: true, user: { name: "김선", email: "sun@example.com" } }), { status: 200 }));
  assert.deepEqual(await fetchSession(), { status: "in", user: { name: "김선", email: "sun@example.com" }, authEnabled: true });
});

test("auth off still counts as signed in and says so", async (t) => {
  mockFetch(t, async () => new Response(JSON.stringify({ auth_enabled: false, user: { name: "개발", email: "dev@local" } }), { status: 200 }));
  const session = await fetchSession();
  assert.equal(session.status === "in" && session.authEnabled, false);
});

test("401 means signed out, whatever the body is", async (t) => {
  mockFetch(t, async () => new Response("<html>login</html>", { status: 401 }));
  assert.deepEqual(await fetchSession(), { status: "out" });
});

test("a missing route, a server error, a bad body or no network is unavailable", async (t) => {
  for (const impl of [
    async () => new Response("{}", { status: 404 }),
    async () => new Response("{}", { status: 502 }),
    async () => new Response("{}", { status: 200 }),
    async () => { throw new TypeError("offline"); },
  ]) {
    mockFetch(t, impl);
    assert.deepEqual(await fetchSession(), { status: "unavailable" });
  }
});

test("an aborted check rethrows instead of looking unavailable", async (t) => {
  mockFetch(t, async () => { throw new DOMException("aborted", "AbortError"); });
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(fetchSession(controller.signal));
});

test("the Google URL carries the encoded internal path", () => {
  assert.equal(googleLoginUrl("/?mode=app&handoff=a b#/capture"), "/api/auth/google/login?next=" + encodeURIComponent("/?mode=app&handoff=a b#/capture"));
  assert.equal(googleLoginUrl(), "/api/auth/google/login?next=%2F");
});

test("logout posts and ignores network errors", async (t) => {
  const calls: { url: string; method?: string }[] = [];
  const fetchMock = mockFetch(t, async (url, init) => { calls.push({ url, method: init?.method }); return new Response(null, { status: 204 }); });
  await logout();
  assert.deepEqual(calls, [{ url: "/api/auth/logout", method: "POST" }]);
  fetchMock.mock.mockImplementation((async () => { throw new TypeError("offline"); }) as typeof fetch);
  await logout();
});

test("unauthorized listeners are called until they unsubscribe", () => {
  let count = 0;
  const off = onUnauthorized(() => count++);
  notifyUnauthorized();
  off();
  notifyUnauthorized();
  assert.equal(count, 1);
});

test("a failed login is an alert, an expired session a status", () => {
  assert.equal(noticeRole(LOGIN_FAILED_MESSAGE), "alert");
  assert.equal(noticeRole(EXPIRED_MESSAGE), "status");
});
