import { NextResponse, type NextRequest } from "next/server";

/**
 * Strict CSP with per-request nonces.
 *
 * Next.js emits inline <script> tags carrying the RSC payload and the theme
 * bootstrap. A static header cannot allow those without 'unsafe-inline', so the
 * nonce is generated per request, echoed back to Next in `x-nonce` (which Next
 * stamps onto its own tags), and embedded in the policy.
 *
 * Nothing in this application needs to execute inline script, so no application
 * code ever reads the nonce -- it exists purely for the framework's own output.
 * Email HTML is never rendered here: it is served by the API under its own
 * `default-src 'none'` policy into a fully sandboxed iframe.
 */

function buildPolicy(nonce: string, apiOrigin: string, isDev: boolean): string {
  const directives = [
    "default-src 'self'",
    // Nonce for Next's own tags. In dev, React also needs eval for its hooks
    // inspector and fast-refresh; that is a development-only concession and is
    // absent from production.
    `script-src 'self' 'nonce-${nonce}'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self' " + apiOrigin,
    // The reading pane's message-body iframe is served by the API.
    "frame-src 'self' " + apiOrigin,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  return directives.join("; ");
}

export function middleware(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const isDev = process.env.NODE_ENV !== "production";
  const apiOrigin =
    process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

  const csp = buildPolicy(nonce, apiOrigin, isDev);

  // Next reads the nonce off the request headers and applies it to the scripts
  // it renders.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    // Everything except API-ish static assets, which need no document policy.
    {
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};