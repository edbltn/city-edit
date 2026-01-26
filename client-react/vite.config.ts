import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: true,
    proxy: {
      "/api": { target: "http://flask:5001", changeOrigin: true },
      "/ws": { target: "http://flask:5001", ws: true },
    },
  },
});
