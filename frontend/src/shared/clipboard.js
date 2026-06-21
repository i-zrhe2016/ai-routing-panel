// Clipboard helper shared by every copy-to-clipboard surface (subscription
// links, credentials). Uses the async Clipboard API in secure contexts and
// falls back to the execCommand textarea trick — same behavior as the old
// admin mixin, extracted so the portal reuses it.

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

// Resolves true on success, throws on failure so callers can surface an error.
export async function copyText(value) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return true;
  }
  if (!fallbackCopyText(value)) {
    throw new Error("复制失败。");
  }
  return true;
}
