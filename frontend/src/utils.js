export function createEmptyPortForm() {
  return {
    listen_port: "",
    expires_at: "",
    traffic_limit: "",
    note: "",
  };
}

export function createEmptyPlanForm() {
  return {
    slug: "",
    name: "",
    description: "",
    price_fen: "",
    duration_days: "",
    traffic_limit: "",
    enabled: true,
    sort_order: "0",
  };
}

export function fallbackCopyText(value) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "absolute";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);
  return copied;
}
