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

    const response = await fetch(url, { method, headers, body, credentials: "same-origin" });
    const rawText = await response.text();
    let data = {};
    if (rawText) {
      try {
        data = JSON.parse(rawText);
      } catch (_error) {
        if (response.status === 401) {
          handleUnauthorized();
          throw new ApiError("登录已失效，请重新登录。", 401, null);
        }
        throw new ApiError(`服务返回了无法解析的响应（${response.status}）。`, response.status, null);
      }
    }

    if (response.status === 401) {
      handleUnauthorized(data.login_url);
      throw new ApiError(data.message || "登录已失效，请重新登录。", 401, data);
    }
    if (!response.ok || data.ok === false) {
      throw new ApiError(data.message || `请求失败（${response.status}）。`, response.status, data);
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
