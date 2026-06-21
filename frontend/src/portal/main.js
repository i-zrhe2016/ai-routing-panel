import { createApp } from "vue";

import App from "./App.vue";
import { createPortalRouter } from "./router.js";
import "../shared/tokens.css";

const app = createApp(App);
app.use(createPortalRouter());
app.mount("#app");
