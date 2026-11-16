/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The camera node's page (ADR 0023): its own small build, served by the node
// from dist-node/. It shares the studio's components, styles and state socket.
const NODE_URL = process.env.BAJA_NODE_URL ?? "http://localhost:8891";

export default defineConfig({
  root: "node",
  plugins: [react()],
  server: {
    proxy: {
      "/api": NODE_URL,
      "/ws": { target: NODE_URL, ws: true },
    },
  },
  build: {
    outDir: "../dist-node",
    emptyOutDir: true,
  },
});
