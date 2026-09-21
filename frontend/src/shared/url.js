// Keeps an API-provided redirect on the panel's own origin. The server sends
// `login_url` on 401; without this check a spoofed value could bounce the
// operator to an attacker-controlled page, so anything off-origin or
// unparseable falls back to /login.
export function sameOriginLoginUrl(value, location = window.location) {
  try {
    const destination = new URL(value || "/login", location.origin);
    return destination.origin === location.origin ? destination.href : "/login";
  } catch (_error) {
    return "/login";
  }
}
