import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 开发模式：Vite 代理 /api 到本地服务；生产模式：产物由后端挂载
const backend = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: false },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    setupFiles: ["src/test-setup.ts"],
  },
});
