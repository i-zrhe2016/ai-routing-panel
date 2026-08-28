import { fileURLToPath, URL } from "node:url";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig(({ mode }) => ({
  root: fileURLToPath(new URL(".", import.meta.url)),
  plugins: [vue()],
  resolve: mode === "test"
    ? { alias: { "vue-router": fileURLToPath(new URL("./src/test/vue-router.js", import.meta.url)) } }
    : undefined,
  build: {
    outDir: fileURLToPath(new URL("../app/static/admin", import.meta.url)),
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: fileURLToPath(new URL("./src/admin/main.js", import.meta.url)),
      output: {
        entryFileNames: "admin.js",
        assetFileNames: (assetInfo) => assetInfo.name?.endsWith(".css") ? "admin.css" : "[name][extname]",
        inlineDynamicImports: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.js", "src/**/*.spec.js"],
  },
}));
