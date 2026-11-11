import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { serviceWorkerSource } from "./src/swTemplate.ts";

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
      writeFileSync("dist/sw.js", serviceWorkerSource(version, urls));
    },
  };
}

// The measurement server (measure/registration-core) runs beside the backend; the web reaches it as /measure.
const proxies = {
  "/api": process.env.SUNSYNC_API_TARGET || "http://127.0.0.1:8000",
  "/measure": {
    target: process.env.SUNSYNC_MEASURE_TARGET || "http://127.0.0.1:8001",
    rewrite: (path: string) => path.replace(/^\/measure/, ""),
  },
};

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), offlineShell()],
  server: { proxy: proxies },
  preview: { proxy: proxies },
});
