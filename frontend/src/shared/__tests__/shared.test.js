import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createApiClient, ApiError, installClientErrorLogging, installVueErrorLogging } from "../apiClient.js";
import { copyText, fallbackCopyText } from "../clipboard.js";
import { humanBytes, usageFraction } from "../formatters.js";
import { naiveThemeOverrides, statusTone, tokens } from "../tokens.js";
import CopyField from "../ui/CopyField.vue";
import StatusPill from "../ui/StatusPill.vue";
import TrafficRing from "../ui/TrafficRing.vue";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("tokens", () => {
  it("maps known statuses to tones and unknown to neutral", () => {
    expect(statusTone("active")).toBe("success");
    expect(statusTone("expired")).toBe("warning");
    expect(statusTone("disabled")).toBe("danger");
    expect(statusTone("payment_submitted")).toBe("info");
    expect(statusTone("nonsense")).toBe("neutral");
  });

  it("builds naive theme overrides from the token palette", () => {
    const t = naiveThemeOverrides();
    expect(t.common.primaryColor).toBe(tokens.color.primary);
    expect(t.common.errorColor).toBe(tokens.color.danger);
    expect(t.common.borderRadius).toBe(tokens.radius.md);
  });
});

describe("formatters", () => {
  it("humanBytes formats like the legacy admin mixin", () => {
    expect(humanBytes(0)).toBe("0 B");
    expect(humanBytes(512)).toBe("512 B");
    expect(humanBytes(1024)).toBe("1.00 KB");
    expect(humanBytes(1024 * 1024)).toBe("1.00 MB");
  });

  it("usageFraction clamps and treats no limit as unlimited (0)", () => {
    expect(usageFraction(512, 1024)).toBe(0.5);
    expect(usageFraction(4096, 1024)).toBe(1);
    expect(usageFraction(100, 0)).toBe(0);
  });
});

function jsonResponse(status, bodyObj) {
  return Promise.resolve({
    status,
    ok: status >= 200 && status < 300,
    text: () => Promise.resolve(bodyObj === undefined ? "" : JSON.stringify(bodyObj)),
  });
}

describe("apiClient", () => {
  it("returns the parsed envelope on success", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(200, { ok: true, data: { hello: "world" } })));
    const api = createApiClient({ csrfToken: "tok" });
    const data = await api.get("/api/x");
    expect(data.data.hello).toBe("world");
  });

  it("attaches X-CSRF-Token on mutating requests only", async () => {
    const fetchMock = vi.fn(() => jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createApiClient({ csrfToken: "csrf-123" });
    await api.get("/api/x");
    expect(fetchMock.mock.calls[0][1].headers["X-CSRF-Token"]).toBeUndefined();
    await api.post("/api/x", { a: 1 });
    const postInit = fetchMock.mock.calls[1][1];
    expect(postInit.headers["X-CSRF-Token"]).toBe("csrf-123");
    expect(postInit.headers["Content-Type"]).toBe("application/json");
    expect(postInit.body).toBe(JSON.stringify({ a: 1 }));
  });

  it("does not set Content-Type for multipart FormData", async () => {
    const fetchMock = vi.fn(() => jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createApiClient({ csrfToken: "t" });
    await api.postForm("/api/upload", new FormData());
    expect(fetchMock.mock.calls[0][1].headers["Content-Type"]).toBeUndefined();
  });

  it("throws ApiError with the server message when ok:false", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(400, { ok: false, message: "套餐已下架。" })));
    const api = createApiClient({});
    await expect(api.post("/api/x", {})).rejects.toMatchObject({ name: "ApiError", status: 400, message: "套餐已下架。" });
  });

  it("invokes the unauthorized handler on 401", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(401, { ok: false, login_url: "/customer/login" })));
    const onUnauthorized = vi.fn();
    const api = createApiClient({ onUnauthorized });
    await expect(api.get("/api/customer/me")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledWith("/customer/login");
  });

  it("reports browser fetch failures without replacing the original error", async () => {
    const networkError = new TypeError("Failed to fetch");
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(networkError)
      .mockResolvedValueOnce(jsonResponse(202, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("navigator", { onLine: true, userAgent: "test-agent" });
    const api = createApiClient({ csrfToken: "csrf-123" });

    await expect(api.get("/api/dashboard?token=secret")).rejects.toBe(networkError);
    await Promise.resolve();

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/client-errors",
      expect.objectContaining({ method: "POST", keepalive: true }),
    );
    const report = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(report.message).toBe("Failed to fetch");
    expect(report.url_path).toBe("/api/dashboard");
    expect(fetchMock.mock.calls[1][1].headers["X-CSRF-Token"]).toBe("csrf-123");
  });

  it("reports HTTP failures with status and source", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(503, { ok: false, message: "服务暂不可用。" }))
      .mockResolvedValueOnce(jsonResponse(202, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createApiClient({ csrfToken: "csrf-123" });

    await expect(api.get("/api/dashboard")).rejects.toMatchObject({ name: "ApiError", status: 503 });
    await Promise.resolve();

    const report = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(report.source).toBe("http");
    expect(report.status).toBe(503);
    expect(report.url_path).toBe("/api/dashboard");
  });

  it("reports uncaught window errors", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(202, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const cleanup = installClientErrorLogging({ csrfToken: "csrf-123" });
    const error = new Error("render failed");
    window.dispatchEvent(new ErrorEvent("error", { error, message: error.message }));
    await Promise.resolve();
    cleanup();

    const report = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(report.source).toBe("window.error");
    expect(report.message).toBe("render failed");
  });

  it("reports Vue handler errors", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(202, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const app = { config: {} };
    installVueErrorLogging(app, { csrfToken: "csrf-123" });
    const error = new Error("component failed");
    app.config.errorHandler(error, null, "render");
    await Promise.resolve();

    const report = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(report.source).toBe("vue.render");
    expect(report.message).toBe("component failed");
  });
});

describe("clipboard", () => {
  it("uses the async Clipboard API in a secure context", async () => {
    const writeText = vi.fn().mockResolvedValue();
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    vi.stubGlobal("isSecureContext", true);
    await copyText("vless://abc");
    expect(writeText).toHaveBeenCalledWith("vless://abc");
  });

  it("falls back to execCommand when clipboard is unavailable", () => {
    const execCommand = vi.fn().mockReturnValue(true);
    document.execCommand = execCommand;
    expect(fallbackCopyText("hello")).toBe(true);
    expect(execCommand).toHaveBeenCalledWith("copy");
  });
});

describe("StatusPill", () => {
  // Vue serializes inline style colors to rgb(), so compare against rgb values.
  it("renders the label and colors by explicit tone", () => {
    const wrapper = mount(StatusPill, { props: { label: "已启用", tone: "success" } });
    expect(wrapper.text()).toContain("已启用");
    // success #188038 -> rgb(24, 128, 56)
    expect(wrapper.find(".status-pill").attributes("style")).toContain("rgb(24, 128, 56)");
  });

  it("derives tone from a raw status string", () => {
    const wrapper = mount(StatusPill, { props: { label: "已过期", status: "expired" } });
    // warning #b06000 -> rgb(176, 96, 0)
    expect(wrapper.find(".status-pill").attributes("style")).toContain("rgb(176, 96, 0)");
  });
});

describe("CopyField", () => {
  it("copies the value and flips the label, emitting copied", async () => {
    const writeText = vi.fn().mockResolvedValue();
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    vi.stubGlobal("isSecureContext", true);
    const wrapper = mount(CopyField, { props: { value: "clash://link", label: "Clash" } });
    expect(wrapper.find(".copy-field__value").attributes("data-copy-value")).toBe("clash://link");
    expect(wrapper.find(".copy-field__btn").text()).toBe("复制");
    await wrapper.find(".copy-field__btn").trigger("click");
    await Promise.resolve();
    await wrapper.vm.$nextTick();
    expect(writeText).toHaveBeenCalledWith("clash://link");
    expect(wrapper.emitted().copied[0]).toEqual(["clash://link"]);
    expect(wrapper.find(".copy-field__btn").text()).toBe("已复制");
  });
});

describe("TrafficRing", () => {
  it("shows a percentage for a bounded quota", () => {
    const wrapper = mount(TrafficRing, { props: { used: 512, limit: 1024 } });
    expect(wrapper.find("strong").text()).toBe("50%");
    expect(wrapper.find("small").text()).toContain("512 B");
  });

  it("shows infinity when there is no limit", () => {
    const wrapper = mount(TrafficRing, { props: { used: 1024, limit: 0 } });
    expect(wrapper.find("strong").text()).toBe("∞");
  });
});
