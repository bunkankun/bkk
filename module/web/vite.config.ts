import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// In dev, the SPA runs at :5173 and proxies /api -> 127.0.0.1:8000.
// In prod, the SPA is mounted at the same origin as the API by FastAPI.
// Backend routes live under /api in both setups, so no path rewrite.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
});
