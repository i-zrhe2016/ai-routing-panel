import { createApp } from "vue";

import "../shared/tokens.css";
import App from "./App.vue";
import { installClientErrorLogging, installVueErrorLogging } from "../shared/apiClient.js";

// Admin SPA entry. Vue + Naive UI are bundled (no CDN). The shell (index.html)
// provides #app and window.__BOOT__ { csrf_token, auth_enabled }; the app fetches
// GET /api/dashboard on mount to populate. Section switching is in-component (no
// router needed for a single-page admin — vue-router is reserved for the portal).
const app = createApp(App);
installClientErrorLogging({ csrfToken: window.__BOOT__?.csrf_token || "" });
installVueErrorLogging(app, { csrfToken: window.__BOOT__?.csrf_token || "" });
app.mount("#app");
