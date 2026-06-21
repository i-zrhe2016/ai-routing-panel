import { resolve } from "node:path";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// Two self-contained SFC bundles share this factory: the admin SPA and the
// subscriber portal SPA. Each builds to app/static/<name>/<name>.{js,css}
// (Vue + Naive UI bundled in) so its Jinja shell loads stable paths. They are
// built as two separate vite invocations (see package.json) — vite.config.js
// builds admin by default, vite.portal.config.js builds portal — which keeps
// each bundle a single self-contained file pair with no cross-entry chunking.
export function makeConfig(name) {
  return defineConfig({
    plugins: [vue()],
    build: {
      outDir: resolve(__dirname, `../app/static/${name}`),
      emptyOutDir: true,
      rollupOptions: {
        input: resolve(__dirname, `src/${name}/main.js`),
        output: {
          entryFileNames: `${name}.js`,
          assetFileNames: `${name}.[ext]`,
          chunkFileNames: `${name}-[name].js`,
        },
      },
    },
  });
}

export default makeConfig("admin");
