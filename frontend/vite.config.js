import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig(({ mode }) => ({
  root: fileURLToPath(new URL(".", import.meta.url)),
  // The admin console is React; the customer portal and landing page are still
  // Vue, so both plugins stay registered. Only src/admin/main.jsx is a build
  // input, and it never imports a .vue file.
  plugins: [react(), vue()],
  resolve: mode === "test"
    ? { alias: { "vue-router": fileURLToPath(new URL("./src/test/vue-router.js", import.meta.url)) } }
    : undefined,
  build: {
    outDir: fileURLToPath(new URL("../app/static/admin", import.meta.url)),
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: fileURLToPath(new URL("./src/admin/main.jsx", import.meta.url)),
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
    include: ["src/**/*.test.js", "src/**/*.test.jsx", "src/**/*.spec.js"],
  },
}));
