import { createApp } from "vue";

import App from "./App.vue";
import TokenApp from "./TokenApp.vue";
import { createPortalRouter } from "./router.js";
import { installClientErrorLogging, installVueErrorLogging } from "../shared/apiClient.js";
import "../shared/tokens.css";

// Two boot modes from the shell's window.__BOOT__:
//   - tenant_token present (served at /tenant/<token>): the account-less token
//     mode — a single read-only subscription view with an inline per-port login.
//   - otherwise (/portal/*): the normal account portal with vue-router.
const boot = (typeof window !== "undefined" && window.__BOOT__) || {};
installClientErrorLogging({ csrfToken: boot.csrf_token || "" });

if (boot.tenant_token) {
  const app = createApp(TokenApp, { tenantToken: boot.tenant_token, csrfToken: boot.csrf_token || "" });
  installVueErrorLogging(app, { csrfToken: boot.csrf_token || "" });
  app.mount("#app");
} else {
  const app = createApp(App);
  installVueErrorLogging(app, { csrfToken: boot.csrf_token || "" });
  app.use(createPortalRouter());
  app.mount("#app");
}
