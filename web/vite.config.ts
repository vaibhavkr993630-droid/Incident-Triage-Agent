import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Live mode: the dev server proxies /api to the console API (uvicorn triage.api:app).
// Static mode (`npm run build:static`): data is read from exported JSON in public/data, so
// the built site needs no backend and can be hosted on any static host.
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  base: mode === "static" ? process.env.VITE_BASE ?? "./" : "/",
  // Set here, not in a .env file: the repo ignores .env.* files, so an env file would be
  // missing on CI and the "static" build would silently become a live build.
  define: mode === "static" ? { "import.meta.env.VITE_DATA_MODE": JSON.stringify("static") } : {},
  server: { proxy: { "/api": "http://localhost:8100" } },
}));
