/**
 * Source of /sw.js, written into dist at build time (see vite.config.ts).
 *
 * Why it looks like this (a deployment once showed the whole old screen again):
 * - Only the cache of this build is read. `caches.match()` without a cache name searches every
 *   cache, oldest first, so a leftover cache of an earlier build won over the current one.
 * - A new worker takes over at once (`skipWaiting`) and deletes the other caches, so caches of
 *   several builds never pile up while an old tab keeps the previous worker alive.
 * - Files whose name has a hash (`/assets/...`) never change, so the cache comes first for them.
 *   Files with a fixed name (manifest, icons, images) are asked from the server first.
 * - A page that cannot be fetched falls back to the index.html of this build, never an older one.
 * - The files are stored with `cache: 'reload'`, so the browser's own HTTP cache cannot hand a
 *   stale copy of a fixed-name file to the new cache.
 */
export function serviceWorkerSource(version: string, urls: string[]): string {
  return `
const CACHE = 'sunsync-ui-${version}';
const ASSETS = ${JSON.stringify(urls)};
function current() { return caches.open(CACHE); }
function fromCurrent(key) {
  return current().then(function (cache) { return cache.match(key); });
}
self.addEventListener('install', function (event) {
  self.skipWaiting();
  event.waitUntil(current().then(function (cache) {
    return cache.addAll(ASSETS.map(function (url) { return new Request(url, { cache: 'reload' }); }));
  }));
});
self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys
          .filter(function (key) { return key.indexOf('sunsync-ui-') === 0 && key !== CACHE; })
          .map(function (key) { return caches.delete(key); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});
self.addEventListener('fetch', function (event) {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.indexOf('/api/') === 0 || url.pathname.indexOf('/measure/') === 0) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(function () {
      return fromCurrent('/index.html').then(function (page) { return page || Response.error(); });
    }));
  } else if (url.pathname.indexOf('/assets/') === 0) {
    event.respondWith(fromCurrent(request).then(function (cached) { return cached || fetch(request); }));
  } else {
    event.respondWith(fetch(new Request(request, { cache: 'no-cache' })).catch(function () {
      return fromCurrent(request).then(function (cached) { return cached || Response.error(); });
    }));
  }
});
`;
}
