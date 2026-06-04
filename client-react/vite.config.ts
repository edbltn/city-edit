import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Where the dev proxy forwards /api and /ws. Defaults to the host-run Flask on
// localhost:5001 (plain `npm run dev`). In the Docker dev compose, Vite runs in
// a container where "flask" is the service hostname, so that service sets
// VITE_API_PROXY=http://flask:5001 to override.
const apiTarget = process.env.VITE_API_PROXY || "http://localhost:5001";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: true,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
      "/ws": { target: apiTarget, ws: true },
    },
  },
});
