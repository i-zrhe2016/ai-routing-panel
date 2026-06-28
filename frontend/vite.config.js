import { resolve } from "node:path";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// Self-contained SFC bundles share this factory: the admin SPA, the subscriber
// portal SPA, and the marketing landing SPA. Each builds to
// app/static/<name>/<name>.{js,css} so its Jinja shell loads stable paths. They
// are built as separate vite invocations (see package.json) which keeps each
// bundle a single self-contained file pair with no cross-entry chunking.
//
// opts.publicDir (relative to this frontend dir) lets a build copy committed
// static assets (e.g. the landing page's self-hosted subset font) into its
// outDir. Defaults to false so admin/portal copy nothing.
export function makeConfig(name, opts = {}) {
  return defineConfig({
    plugins: [vue()],
    publicDir: opts.publicDir ? resolve(__dirname, opts.publicDir) : false,
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
