/** Configure frontend development and proxy API requests to the local backend. */
import { defineConfig } from "vite";

export default defineConfig({
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
