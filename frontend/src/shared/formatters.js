// Shared display formatters. humanBytes is moved verbatim from the old admin
// mixin so byte rendering stays identical across admin and portal.

export function humanBytes(value) {
  let size = Number(value || 0);
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  for (let index = 0; index < units.length; index += 1) {
    const unit = units[index];
    if (size < 1024 || unit === units[units.length - 1]) {
      if (unit === "B") {
        return `${Math.trunc(size)} ${unit}`;
      }
      return `${size.toFixed(2)} ${unit}`;
    }
    size /= 1024;
  }
  return "0 B";
}

// Fraction (0..1) of a quota consumed, clamped. used/limit in bytes; a missing
// or zero limit means "unlimited" -> 0.
export function usageFraction(usedBytes, limitBytes) {
  const limit = Number(limitBytes || 0);
  if (limit <= 0) return 0;
  const used = Number(usedBytes || 0);
  return Math.max(0, Math.min(1, used / limit));
}
