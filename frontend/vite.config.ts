import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // Pas de timeout cote proxy : les gros PDF peuvent prendre plusieurs
        // minutes a uploader sur des reseaux lents et on ne veut pas que le
        // navigateur recoive un ERR_NETWORK parce que Vite a coupe avant le
        // backend.
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
});
