import type { NextConfig } from "next";

// The browser only ever talks to this origin; /api/* is proxied to the FastAPI
// service at request time by src/app/api/[...path]/route.ts, so the refresh
// cookie stays first-party (HttpOnly, SameSite=Lax).

// Content-Security-Policy. Next.js App Router emits inline bootstrap scripts, so script-src needs
// 'unsafe-inline' unless nonces are introduced; every other directive is restricted to this origin
// (no CDN, analytics or third-party connections: data sovereignty). Development additionally needs
// 'unsafe-eval' and WebSocket connections for hot reloading.
const dev = process.env.NODE_ENV !== "production";
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  `connect-src 'self'${dev ? " ws: wss:" : ""}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");
const hsts = process.env.SCRIPTORIUM_HSTS_SECONDS && Number(process.env.SCRIPTORIUM_HSTS_SECONDS) > 0
  ? [{ key: "Strict-Transport-Security", value: `max-age=${Number(process.env.SCRIPTORIUM_HSTS_SECONDS)}; includeSubDomains` }]
  : [];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [
      {
        // Pages only: /api/* responses keep the API's own, stricter headers (CSP default-src 'none').
        source: "/((?!api/).*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Content-Security-Policy", value: csp },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          ...hsts,
        ],
      },
    ];
  },
};

export default nextConfig;
