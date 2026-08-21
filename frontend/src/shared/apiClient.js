// Shared JSON API client for both SPAs. Standardizes on the backend envelope
// { ok, message?, level?, data? }: a non-ok response (HTTP error or ok:false)
// throws an ApiError carrying the server message; a 401 triggers the
// unauthorized handler (redirect to login by default). CSRF is sent as the
// X-CSRF-Token header on mutating requests, matching the existing admin API.

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

const reportedErrors = new WeakSet();

function safeUrlPath(url) {
  try {
    const parsed = new URL(url, typeof window !== "undefined" ? window.location.origin : "http://localhost");
    return parsed.pathname
      .split("/")
      .map((segment) => {
        if (!segment) return segment;
        if (/^\d+$/.test(segment)) return ":id";
        if (segment.length >= 24) return ":redacted";
        return segment;
      })
      .join("/")
      .slice(0, 300);
  } catch (_error) {
    return "unknown";
  }
}

function markErrorReported(error) {
  if (!error || typeof error !== "object") return false;
  if (reportedErrors.has(error)) return true;
  reportedErrors.add(error);
  return false;
}

export function reportClientError({
  url = typeof window !== "undefined" ? window.location.pathname : "unknown",
  method = "RUNTIME",
  error,
  csrfToken = "",
  status = null,
  source = "fetch",
} = {}) {
  if (typeof fetch !== "function" || url === "/api/client-errors") return;
  if (markErrorReported(error)) return;
  const payload = {
    error_name: error?.name || "Error",
    message: error?.message || String(error || "客户端请求失败。"),
    stack: error?.stack || "",
    source,
    method: String(method || "RUNTIME").toUpperCase(),
    online: typeof navigator === "undefined" || navigator.onLine !== false,
    status,
    url_path: safeUrlPath(url),
  };
  const headers = { Accept: "application/json", "Content-Type": "application/json" };
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  fetch("/api/client-errors", {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    credentials: "same-origin",
    keepalive: true,
  }).catch(() => {});
}

export function installClientErrorLogging({ csrfToken = "" } = {}) {
  if (typeof window === "undefined" || window.__clientErrorLoggingInstalled) return () => {};
  window.__clientErrorLoggingInstalled = true;
  const onError = (event) => {
    const error = event.error || new Error(event.message || "未捕获的前端异常");
    reportClientError({ error, csrfToken, source: "window.error" });
  };
  const onUnhandledRejection = (event) => {
    const reason = event.reason instanceof Error ? event.reason : new Error(String(event.reason || "未处理的 Promise 异常"));
    reportClientError({ error: reason, csrfToken, source: "unhandledrejection" });
  };
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onUnhandledRejection);
  return () => {
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onUnhandledRejection);
    window.__clientErrorLoggingInstalled = false;
  };
}

export function installVueErrorLogging(app, { csrfToken = "" } = {}) {
  if (!app) return;
  app.config.errorHandler = (error, _instance, info) => {
    reportClientError({ error, csrfToken, source: `vue.${info || "errorHandler"}` });
  };
}

export function createApiClient({ csrfToken = "", loginUrl = "/login", onUnauthorized } = {}) {
  let token = csrfToken;

  function handleUnauthorized(target) {
    const dest = target || loginUrl;
    if (onUnauthorized) {
      onUnauthorized(dest);
    } else if (typeof window !== "undefined") {
      window.location.assign(dest);
    }
  }

  async function request(url, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (method !== "GET" && token) {
      headers["X-CSRF-Token"] = token;
    }

    let body = options.body;
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.json);
    }
    // For FormData (multipart, e.g. payment proof) we deliberately leave
    // Content-Type unset so the browser adds the multipart boundary.

    let response;
    try {
      response = await fetch(url, { method, headers, body, credentials: "same-origin" });
    } catch (error) {
      reportClientError({ url, method, error, csrfToken: token, source: "fetch" });
      throw error;
    }
    let rawText;
    try {
      rawText = await response.text();
    } catch (error) {
      reportClientError({ url, method, error, csrfToken: token, status: response.status, source: "response.read" });
      throw error;
    }
    let data = {};
    if (rawText) {
      try {
        data = JSON.parse(rawText);
      } catch (_error) {
        const parseError = new ApiError(`服务返回了无法解析的响应（${response.status}）。`, response.status, null);
        reportClientError({ url, method, error: parseError, csrfToken: token, status: response.status, source: "response.parse" });
        if (response.status === 401) {
          handleUnauthorized();
        }
        throw parseError;
      }
    }

    if (response.status === 401) {
      handleUnauthorized(data.login_url);
      const error = new ApiError(data.message || "登录已失效，请重新登录。", 401, data);
      reportClientError({ url, method, error, csrfToken: token, status: 401, source: "http" });
      throw error;
    }
    if (!response.ok || data.ok === false) {
      const error = new ApiError(data.message || `请求失败（${response.status}）。`, response.status, data);
      reportClientError({ url, method, error, csrfToken: token, status: response.status, source: "http" });
      throw error;
    }
    return data;
  }

  return {
    request,
    get: (url, options) => request(url, { ...options, method: "GET" }),
    post: (url, json, options) => request(url, { ...options, method: "POST", json }),
    put: (url, json, options) => request(url, { ...options, method: "PUT", json }),
    del: (url, options) => request(url, { ...options, method: "DELETE" }),
    postForm: (url, formData, options) => request(url, { ...options, method: "POST", body: formData }),
    setCsrfToken: (next) => {
      token = next;
    },
  };
}
