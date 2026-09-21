import { createApp } from "vue";

import "../shared/tokens.css";
import "./admin.css";
import "../shared/control-center.css";
import App from "./App.vue";
import { installClientErrorLogging, installVueErrorLogging } from "../shared/apiClient.js";

const app = createApp(App);
installClientErrorLogging({ csrfToken: window.__BOOT__?.csrf_token || "" });
installVueErrorLogging(app, { csrfToken: window.__BOOT__?.csrf_token || "" });
app.mount("#app");
