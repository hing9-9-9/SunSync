import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createHash } from "node:crypto";

function offlineShell() {
  return {
    name: "sunsync-offline-shell",
    closeBundle() {
      function files(dir: string): string[] {
        return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
          entry.isDirectory()
            ? files(join(dir, entry.name))
            : [join(dir, entry.name)],
        );
      }
      const assets = files("dist").filter((file) => !file.endsWith("/sw.js"));
      const hash = createHash("sha256");
      for (const file of assets) hash.update(readFileSync(file));
      const version = hash.digest("hex").slice(0, 12);
      const urls = assets.map((file) => "/" + file.replace(/^dist\//, ""));
      writeFileSync(
        "dist/sw.js",
        `
const CACHE = 'sunsync-ui-${version}';
const ASSETS = ${JSON.stringify(urls)};
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('sunsync-ui-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || new URL(event.request.url).origin !== self.location.origin) return;
  if (new URL(event.request.url).pathname.startsWith('/api/')) return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/index.html')));
  } else {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
  }
});
`,
      );
    },
  };
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), offlineShell()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  preview: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
