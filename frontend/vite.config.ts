/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// During `npm run dev`, API and WebSocket calls are forwarded to the Python
// service so the page behaves exactly as when the service serves the build.
const SERVICE_URL = process.env.BAJA_STUDIO_URL ?? "http://localhost:8888";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": SERVICE_URL,
      "/ws": { target: SERVICE_URL, ws: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/setup.ts"],
  },
});
