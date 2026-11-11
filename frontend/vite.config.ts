import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the dashboard calls /api on the dev stack (local/devstack.py), or on a
// deployed API with PARCHI_API=https://<id>.execute-api.<region>.amazonaws.com.
// In production CloudFront serves /api/* from API Gateway on the same domain.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.PARCHI_API ?? "http://127.0.0.1:3001", changeOrigin: true } },
  },
  test: { environment: "jsdom" },
});
