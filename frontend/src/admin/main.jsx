import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "../shared/tokens.css";
import "./admin.css";
import "../shared/control-center.css";
import App from "./App.jsx";
import { PanelProvider } from "./state/PanelProvider.jsx";

// React replaces the previous Vue admin entry. The bundle contract is unchanged:
// Vite still emits app/static/admin/admin.js + admin.css from this file.
createRoot(document.getElementById("app")).render(
  <StrictMode>
    <PanelProvider>
      <App />
    </PanelProvider>
  </StrictMode>,
);
