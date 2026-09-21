import { createApp } from "vue";

import App from "./App.vue";
import TokenApp from "./TokenApp.vue";
import { createPortalRouter } from "./router.js";
import { installClientErrorLogging, installVueErrorLogging } from "../shared/apiClient.js";
import "../shared/tokens.css";
import "../shared/control-center.css";
import "./portal.css";

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
