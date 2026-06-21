import { resolve } from "node:path";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// Builds the admin SPA (SFC + Naive UI, Vue bundled in) to app/static/admin/.
// index.html is a thin shell loading admin/admin.js (+ admin.css). A `portal`
// entry will be added in P3. The old IIFE panel.js entry is retired in this
// phase — the SFC admin replaces it.
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: resolve(__dirname, "../app/static/admin"),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(__dirname, "src/admin/main.js"),
      output: {
        entryFileNames: "admin.js",
        assetFileNames: "admin.[ext]",
        chunkFileNames: "admin-[name].js",
      },
    },
  },
});
