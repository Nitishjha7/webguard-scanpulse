import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Recharts and its d3 dependencies are most of the bundle and change
        // far less often than app code, so they get their own long-lived chunk.
        manualChunks: {
          charts: ["recharts"],
          react: ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    // The API is a sibling container in dev, so the browser talks to /api on
    // this origin and Vite forwards it. Keeps cookies and CORS out of the way.
    proxy: {
      "/api": { target: process.env.VITE_API_TARGET || "http://backend:5000", changeOrigin: true },
      "/status": { target: process.env.VITE_API_TARGET || "http://backend:5000", changeOrigin: true },
      "/health": { target: process.env.VITE_API_TARGET || "http://backend:5000", changeOrigin: true },
    },
  },
});
