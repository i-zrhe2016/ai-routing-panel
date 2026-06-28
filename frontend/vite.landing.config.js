import { makeConfig } from "./vite.config.js";

// Builds the public marketing landing SPA to app/static/landing/landing.{js,css}.
// publicDir copies the committed self-hosted subset font from
// src/landing/public/ → app/static/landing/ (i.e. fonts/noto-serif-sc-subset.woff2).
export default makeConfig("landing", { publicDir: "src/landing/public" });
