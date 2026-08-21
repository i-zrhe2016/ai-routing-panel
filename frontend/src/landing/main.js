import { createApp } from "vue";

import App from "./App.vue";
import "./landing.css";
import { installClientErrorLogging, installVueErrorLogging } from "../shared/apiClient.js";

// Public marketing landing page (served at /home). The shell injects
// window.__BOOT__ = { csrf_token }; the page itself is read-only and only
// fetches the public GET /api/customer/plans to render live pricing.
const boot = (typeof window !== "undefined" && window.__BOOT__) || {};
installClientErrorLogging({ csrfToken: boot.csrf_token || "" });

const app = createApp(App, { csrfToken: boot.csrf_token || "" });
installVueErrorLogging(app, { csrfToken: boot.csrf_token || "" });
app.mount("#app");
