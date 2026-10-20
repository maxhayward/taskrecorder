import { defineConfig } from "vite";

// `yarn dev` proxies the API to `wl serve --no-open` (port 8765).
// `yarn build` writes into the Python package so `wl serve` can ship it.
export default defineConfig({
  build: { outDir: "../taskrecorder/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
