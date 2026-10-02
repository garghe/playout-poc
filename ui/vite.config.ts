import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev (npm run dev) the API/WebSocket are proxied to the backend on :8080.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/ws": { target: "ws://localhost:8080", ws: true },
    },
  },
});
