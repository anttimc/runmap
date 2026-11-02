/** Configure frontend development and proxy API requests to the local backend. */
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    rollupOptions: {
      input: { main: "index.html", environment: "environment.html" },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
