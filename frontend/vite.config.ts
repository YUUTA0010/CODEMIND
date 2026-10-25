import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const backend = process.env.CODEMIND_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: true, ws: true },
    },
  },
  build: { chunkSizeWarningLimit: 6000 },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], globals: true },
});
