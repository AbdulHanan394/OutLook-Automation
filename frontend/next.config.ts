import type { NextConfig } from "next";

/**
 * The API origin, used for both XHR and the sandboxed message-body iframe.
 * The Docker build passes port 8001 while local development uses 8000, so this
 * cannot be hardcoded -- an earlier version pinned 8000 and silently blocked
 * every request in the containerised app.
 */
const apiOrigin = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

/**
 * Content-Security-Policy.
 *
 * `script-src` and `style-src` are stated explicitly rather than inherited from
 * `default-src`, so that it is obvious on sight that no 'unsafe-inline' is
 * permitted in the app document. There is no inline script or style anywhere in
 * the mail client: email markup is rendered by the API into a separate document
 * that carries its own policy (see routes_mail.render_message), loaded into an
 * iframe with an empty `sandbox` attribute.
 *
 * `frame-src` must name the API origin because the reading pane's iframe points
 * there. Nothing else is framable.
 */
function contentSecurityPolicy(): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self' " + apiOrigin,
    "frame-src 'self' " + apiOrigin,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

const nextConfig: NextConfig = {
  headers: async () => [
    {
      source: "/(.*)",
      headers: [
        { key: "Content-Security-Policy", value: contentSecurityPolicy() },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "same-origin" },
      ],
    },
  ],
};

export default nextConfig;