import { resolve } from "node:path";
import { defineConfig } from "vite";

// Builds the admin panel into a single IIFE bundle emitted as app/static/panel.js.
// Vue is kept EXTERNAL and mapped to the global `Vue` so the bundle keeps using the
// CDN build loaded by index.html (vue.global.prod.js), which ships the runtime
// template compiler that the in-DOM template in index.html needs. The runtime
// deployment is therefore unchanged: index.html still loads CDN Vue + this file.
export default defineConfig({
  build: {
    outDir: resolve(__dirname, "../app/static"),
    emptyOutDir: false,
    minify: false,
    lib: {
      entry: resolve(__dirname, "src/main.js"),
      formats: ["iife"],
      name: "PanelApp",
      fileName: () => "panel.js",
    },
    rollupOptions: {
      external: ["vue"],
      output: {
        globals: { vue: "Vue" },
        entryFileNames: "panel.js",
      },
    },
  },
});
