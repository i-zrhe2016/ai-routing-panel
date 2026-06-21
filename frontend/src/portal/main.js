import { createApp } from "vue";

import App from "./App.vue";
import TokenApp from "./TokenApp.vue";
import { createPortalRouter } from "./router.js";
import "../shared/tokens.css";

// Two boot modes from the shell's window.__BOOT__:
//   - tenant_token present (served at /tenant/<token>): the account-less token
//     mode — a single read-only subscription view with an inline per-port login.
//   - otherwise (/portal/*): the normal account portal with vue-router.
const boot = (typeof window !== "undefined" && window.__BOOT__) || {};

if (boot.tenant_token) {
  createApp(TokenApp, { tenantToken: boot.tenant_token, csrfToken: boot.csrf_token || "" }).mount("#app");
} else {
  const app = createApp(App);
  app.use(createPortalRouter());
  app.mount("#app");
}
