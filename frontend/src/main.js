import { createApp } from "vue";
import { createPanelApp } from "./createPanelApp.js";

// `vue` is externalized to the global `Vue` (the CDN build in index.html), so
// createApp here is window.Vue.createApp — same runtime, same in-DOM template
// compilation. Guard and mount are identical to the former mountPanelApp().
function mountPanelApp() {
  const root = document.getElementById("app");
  if (!root || !window.Vue) {
    return;
  }
  createApp(createPanelApp(window.__PANEL_STATE__)).mount(root);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountPanelApp, { once: true });
} else {
  mountPanelApp();
}
