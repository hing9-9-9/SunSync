// Runs the generated /sw.js against a small fake of the browser's cache API.
import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { serviceWorkerSource } from "./swTemplate.ts";

type Handler = (event: any) => void;

class FakeCache {
  entries = new Map<string, string>();
  storedWith: string[] = [];
  label: string;
  async addAll(requests: Array<string | { url: string; cache?: string }>) {
    for (const request of requests) {
      const url = typeof request === "string" ? request : request.url;
      const path = new URL(url, "https://x.test").pathname;
      this.entries.set(path, `${this.label}:${path}`);
      this.storedWith.push(typeof request === "string" ? "" : (request.cache ?? ""));
    }
  }
  async match(key: string | { url: string }) {
    const url = typeof key === "string" ? key : new URL(key.url).pathname;
    const body = this.entries.get(url);
    return body === undefined ? undefined : { body };
  }
  constructor(label: string) {
    this.label = label;
  }
}

function browser(version: string, urls: string[], existing: Record<string, FakeCache> = {}) {
  const stores = existing;
  const handlers: Record<string, Handler> = {};
  const log = { skipWaiting: 0, claimed: 0, reloadRequests: [] as string[] };
  let online = true;
  const caches = {
    open: async (name: string) => (stores[name] ??= new FakeCache(name)),
    keys: async () => Object.keys(stores),
    delete: async (name: string) => delete stores[name],
    // The real CacheStorage.match searches every cache, oldest first; the worker must not use it.
    match: async () => {
      throw new Error("caches.match must not be used");
    },
  };
  class FakeRequest {
    url: string;
    cache?: string;
    mode: string;
    method = "GET";
    constructor(input: string | FakeRequest, init: { cache?: string } = {}) {
      this.url = typeof input === "string" ? new URL(input, "https://x.test").href : input.url;
      this.mode = typeof input === "string" ? "cors" : input.mode;
      this.cache = init.cache;
    }
  }
  const network = async (request: FakeRequest) => {
    if (!online) throw new TypeError("Failed to fetch");
    log.reloadRequests.push(`${new URL(request.url).pathname}|${request.cache ?? ""}`);
    return { body: `network:${new URL(request.url).pathname}` };
  };
  const context: any = {
    self: {
      location: { origin: "https://x.test" },
      addEventListener: (name: string, fn: Handler) => (handlers[name] = fn),
      skipWaiting: () => void log.skipWaiting++,
      clients: { claim: async () => void log.claimed++ },
    },
    caches,
    Request: FakeRequest,
    Response: { error: () => ({ error: true }) },
    URL,
    Promise,
    fetch: network,
  };
  vm.runInNewContext(serviceWorkerSource(version, urls), context);
  const send = async (name: string, event: any = {}) => {
    const waits: Promise<unknown>[] = [];
    event.waitUntil = (p: Promise<unknown>) => waits.push(p);
    handlers[name](event);
    await Promise.all(waits);
  };
  const request = async (path: string, mode = "cors", method = "GET", origin = "https://x.test") => {
    const req = new FakeRequest(origin + path);
    req.mode = mode;
    req.method = method;
    let answer: Promise<unknown> | undefined;
    handlers.fetch({ request: req, respondWith: (p: Promise<unknown>) => (answer = p) });
    return answer === undefined ? "not handled" : answer;
  };
  return { stores, log, send, request, setOnline: (value: boolean) => (online = value) };
}

const URLS = ["/index.html", "/assets/index-NEW.js", "/manifest.webmanifest", "/icons/icon.svg"];

test("a new worker takes over at once and stores every file of its build", async () => {
  const b = browser("new", URLS);
  await b.send("install");
  assert.equal(b.log.skipWaiting, 1);
  assert.deepEqual([...b.stores["sunsync-ui-new"].entries.keys()], URLS);
});

test("the activation deletes the caches of older builds and claims the pages", async () => {
  const old = new FakeCache("old");
  const b = browser("new", URLS, { "sunsync-ui-old": old, "other-site": new FakeCache("x") });
  await b.send("install");
  await b.send("activate");
  assert.deepEqual(Object.keys(b.stores).sort(), ["other-site", "sunsync-ui-new"]);
  assert.equal(b.log.claimed, 1);
});

test("an offline page opens with the index.html of this build, not of an older cache", async () => {
  const old = new FakeCache("OLD");
  old.entries.set("/index.html", "OLD:/index.html");
  const b = browser("new", URLS, { "sunsync-ui-old": old });
  await b.send("install");
  b.setOnline(false);
  const page: any = await b.request("/", "navigate");
  assert.equal(page.body, "sunsync-ui-new:/index.html");
});

test("a page that can be fetched comes from the server, not from the cache", async () => {
  const b = browser("new", URLS);
  await b.send("install");
  const page: any = await b.request("/", "navigate");
  assert.equal(page.body, "network:/");
});

test("a hashed file is read from the cache of this build and an older copy is ignored", async () => {
  const old = new FakeCache("OLD");
  old.entries.set("/assets/index-NEW.js", "OLD:/assets/index-NEW.js");
  const b = browser("new", URLS, { "sunsync-ui-old": old });
  await b.send("install");
  const file: any = await b.request("/assets/index-NEW.js");
  assert.equal(file.body, "sunsync-ui-new:/assets/index-NEW.js");
});

test("a fixed-name file is asked from the server first and checked for changes", async () => {
  const b = browser("new", URLS);
  await b.send("install");
  const file: any = await b.request("/icons/icon.svg");
  assert.equal(file.body, "network:/icons/icon.svg");
  assert.ok(b.log.reloadRequests.includes("/icons/icon.svg|no-cache"));
});

test("a fixed-name file offline comes from this build's cache", async () => {
  const old = new FakeCache("OLD");
  old.entries.set("/icons/icon.svg", "OLD:/icons/icon.svg");
  const b = browser("new", URLS, { "sunsync-ui-old": old });
  await b.send("install");
  b.setOnline(false);
  const file: any = await b.request("/icons/icon.svg");
  assert.equal(file.body, "sunsync-ui-new:/icons/icon.svg");
});

test("the files are stored with the browser's own cache switched off", async () => {
  const b = browser("new", URLS);
  await b.send("install");
  const cache = b.stores["sunsync-ui-new"];
  assert.equal(cache.storedWith.length, URLS.length);
  assert.ok(cache.storedWith.every((mode) => mode === "reload"));
});

test("the api, the measurement server, other sites and writes are left alone", async () => {
  const b = browser("new", URLS);
  await b.send("install");
  assert.equal(await b.request("/api/ui/spaces"), "not handled");
  assert.equal(await b.request("/measure/health"), "not handled");
  assert.equal(await b.request("/assets/index-NEW.js", "cors", "GET", "https://fonts.example"), "not handled");
  assert.equal(await b.request("/assets/index-NEW.js", "cors", "POST"), "not handled");
});

test("a file that exists nowhere is an error, not an older page", async () => {
  const b = browser("new", URLS);
  await b.send("install");
  b.setOnline(false);
  const file: any = await b.request("/design/missing.svg");
  assert.deepEqual(file, { error: true });
});
