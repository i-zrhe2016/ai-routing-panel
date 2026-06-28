import { createApp } from "vue";

import App from "./App.vue";
import "./landing.css";

// Public marketing landing page (served at /home). The shell injects
// window.__BOOT__ = { csrf_token }; the page itself is read-only and only
// fetches the public GET /api/customer/plans to render live pricing.
const boot = (typeof window !== "undefined" && window.__BOOT__) || {};

createApp(App, { csrfToken: boot.csrf_token || "" }).mount("#app");
