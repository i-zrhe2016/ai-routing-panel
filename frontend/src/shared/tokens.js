// Single source of truth for the redesigned design system.
//
// Consumed two ways:
//   1. As CSS custom properties on :root (see tokens.css, kept in sync) so the
//      SPA shells and the server-rendered public/login pages share identical
//      colors/spacing/radii.
//   2. As Naive UI themeOverrides via naiveThemeOverrides() so the component
//      library renders in the same palette.
//
// The status-color vocabulary (primary/success/warning/danger) is preserved
// from the old style.css :root so the server-driven status_tone/status_label
// values keep mapping to the right colors. Neutrals, spacing and radii are
// refined toward a flatter, minimal SaaS look (Linear/Vercel-ish).

export const tokens = {
  color: {
    // Brand + status vocabulary (preserved from the original :root).
    primary: "#1a73e8",
    primaryStrong: "#174ea6",
    primarySoft: "#e8f0fe",
    success: "#188038",
    successSoft: "#e6f4ea",
    warning: "#b06000",
    warningSoft: "#fef7e0",
    danger: "#d93025",
    dangerSoft: "#fce8e6",
    info: "#1a73e8",

    // Neutral ramp (new — cooler, flatter).
    gray50: "#f8fafc",
    gray100: "#f1f4f8",
    gray200: "#e6eaf0",
    gray300: "#d6dce5",
    gray400: "#aab4c2",
    gray500: "#7b8595",
    gray600: "#5b6573",
    gray700: "#3f4753",
    gray800: "#272d36",
    gray900: "#171b21",

    // Surfaces / text / lines.
    bg: "#f7f8fa",
    surface: "#ffffff",
    surfaceMuted: "#f6f8fb",
    text: "#1a1f26",
    textMuted: "#5b6573",
    textSoft: "#8a94a3",
    line: "#e6e9ee",
    lineStrong: "#d4dae2",
  },
  radius: { sm: "8px", md: "12px", lg: "16px", xl: "20px" },
  shadow: {
    sm: "0 1px 2px rgba(16, 24, 40, 0.06)",
    md: "0 6px 20px rgba(16, 24, 40, 0.08)",
  },
  space: { 1: "4px", 2: "8px", 3: "12px", 4: "16px", 5: "20px", 6: "24px", 7: "32px", 8: "40px" },
  font: {
    sans: '"Google Sans Text", "Roboto", "Noto Sans SC", "PingFang SC", system-ui, sans-serif',
  },
};

// Maps a semantic status tone to its color pair. `tone` comes straight from the
// backend (status_tone) or is derived from a status string by statusTone().
export const TONES = {
  success: { color: tokens.color.success, soft: tokens.color.successSoft },
  warning: { color: tokens.color.warning, soft: tokens.color.warningSoft },
  danger: { color: tokens.color.danger, soft: tokens.color.dangerSoft },
  info: { color: tokens.color.info, soft: tokens.color.primarySoft },
  neutral: { color: tokens.color.textMuted, soft: tokens.color.gray100 },
};

// Common port/order status strings -> tone, so a StatusPill can be fed a raw
// status when the backend did not pre-compute a tone.
const STATUS_TONE = {
  active: "success",
  ok: "success",
  fulfilled: "success",
  healthy: "success",
  disabled: "danger",
  bad: "danger",
  payment_rejected: "danger",
  cancelled: "neutral",
  expired: "warning",
  quota: "warning",
  warn: "warning",
  pending_payment: "warning",
  payment_submitted: "info",
  unknown: "neutral",
};

export function statusTone(status) {
  return STATUS_TONE[String(status || "").toLowerCase()] || "neutral";
}

// Builds a Naive UI themeOverrides object from the tokens so the component
// library matches the token palette. Light theme; a dark variant can be added
// later by swapping the surface/text/line values.
export function naiveThemeOverrides() {
  const c = tokens.color;
  return {
    common: {
      primaryColor: c.primary,
      primaryColorHover: c.primaryStrong,
      primaryColorPressed: c.primaryStrong,
      successColor: c.success,
      warningColor: c.warning,
      errorColor: c.danger,
      infoColor: c.info,
      textColorBase: c.text,
      textColor1: c.text,
      textColor2: c.textMuted,
      textColor3: c.textSoft,
      bodyColor: c.bg,
      cardColor: c.surface,
      borderColor: c.line,
      borderRadius: tokens.radius.md,
      borderRadiusSmall: tokens.radius.sm,
      fontFamily: tokens.font.sans,
      boxShadow1: tokens.shadow.sm,
      boxShadow2: tokens.shadow.md,
    },
  };
}
