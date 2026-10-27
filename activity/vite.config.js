import { defineConfig } from "vite";

export default defineConfig({
  server: { port: 5173, strictPort: true, proxy: { "/api": "http://127.0.0.1:8081", "/healthz": "http://127.0.0.1:8081" } },
  build: { outDir: "dist", emptyOutDir: true },
});
