import { useEffect, useRef } from "react";

const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex], [contenteditable="true"]';

// Shared keyboard behavior for the mobile drawer and confirmation dialog.
// Keep the dismiss callback current without reopening the focus scope when busy changes.
export function useDialogFocus(containerRef, open, onDismiss) {
  const dismissRef = useRef(onDismiss);
  dismissRef.current = onDismiss;

  useEffect(() => {
    const container = containerRef.current;
    if (!open || !container) return undefined;
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function focusableElements() {
      return [...container.querySelectorAll(FOCUSABLE)].filter((element) => {
        const style = window.getComputedStyle(element);
        return element.tabIndex >= 0 && !element.matches(":disabled") &&
          !element.closest("[hidden], [inert]") && style.display !== "none" && style.visibility !== "hidden";
      });
    }

    function focusStart() {
      const elements = focusableElements();
      (elements.find((element) => element.hasAttribute("data-dialog-autofocus")) || elements[0] || container).focus();
    }

    function onKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        dismissRef.current?.();
      } else if (event.key === "Tab") {
        const elements = focusableElements();
        const first = elements[0];
        const last = elements[elements.length - 1];
        if (!elements.length) {
          event.preventDefault();
          container.focus();
        } else if (!container.contains(document.activeElement) ||
          (event.shiftKey && (document.activeElement === first || document.activeElement === container)) ||
          (!event.shiftKey && document.activeElement === last)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        }
      }
    }

    function onFocusIn(event) {
      if (!container.contains(event.target)) focusStart();
    }

    focusStart();
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("focusin", onFocusIn);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [containerRef, open]);
}
