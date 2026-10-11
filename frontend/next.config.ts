import type { NextConfig } from "next";
const nextConfig: NextConfig = { headers: async () => [{ source: "/(.*)", headers: [{ key: "Content-Security-Policy", value: "default-src 'self'; connect-src 'self' http://localhost:8000; frame-ancestors 'none'; base-uri 'self'" }, { key: "X-Frame-Options", value: "DENY" }] }] };
export default nextConfig;
