// Shared portal state + API client. The shell injects window.__BOOT__ with the
// CSRF token and the current identity (or null). A 401 from any call bounces to
// the existing /customer/login page (modernized in P5) with a next= back-link.
import { reactive } from "vue";

import { createApiClient } from "../shared/apiClient.js";

const boot = (typeof window !== "undefined" && window.__BOOT__) || { csrf_token: "", me: null };

export const portal = reactive({
  me: boot.me || null,
  csrfToken: boot.csrf_token || "",
});

export const api = createApiClient({
  csrfToken: portal.csrfToken,
  loginUrl: "/customer/login",
  onUnauthorized: () => {
    if (typeof window !== "undefined") {
      const next = encodeURIComponent(window.location.pathname);
      window.location.assign(`/customer/login?next=${next}`);
    }
  },
});

export async function refreshMe() {
  try {
    const res = await api.get("/api/customer/me");
    portal.me = res.data.customer;
    return portal.me;
  } catch (_error) {
    portal.me = null;
    return null;
  }
}

export async function logout() {
  try {
    await api.post("/api/customer/auth/logout", {});
  } finally {
    if (typeof window !== "undefined") window.location.assign("/customer/login");
  }
}
