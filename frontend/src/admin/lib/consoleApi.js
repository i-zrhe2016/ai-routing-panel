// Console-side API wiring: one place that knows about CSRF, the same-origin
// redirect rule, and how a failed request is reported.
import { createApiClient, installClientErrorLogging, reportClientError } from "../../shared/apiClient.js";
import { sameOriginLoginUrl } from "../../shared/url.js";

export function createConsoleApi({ csrfToken = "", onUnauthorized } = {}) {
  return createApiClient({
    csrfToken,
    onUnauthorized: onUnauthorized || ((target) => {
      if (typeof window !== "undefined") {
        window.location.assign(sameOriginLoginUrl(target));
      }
    }),
  });
}

export { installClientErrorLogging, reportClientError };
