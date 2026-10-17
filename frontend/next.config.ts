import type { NextConfig } from "next";

/**
 * The Content-Security-Policy lives in `src/middleware.ts` rather than here,
 * because it carries a per-request nonce that Next.js must be able to read off
 * the request in order to stamp onto its own inline tags. A static header in
 * next.config cannot do that.
 *
 * `frame-src` still has to name the API origin, since the reading pane's
 * message-body iframe is served by the backend under its own
 * `default-src 'none'` policy.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default nextConfig;