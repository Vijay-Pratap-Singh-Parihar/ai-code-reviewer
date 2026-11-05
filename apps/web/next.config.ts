import type { NextConfig } from "next";

/**
 * Security headers for every page (fixed at build time, like the
 * NEXT_PUBLIC_ variables they read).
 *
 * The Content-Security-Policy allows only this site's own scripts, styles
 * and fonts (next/font self-hosts them), API calls to the revu API, and
 * form posts to GitHub (the "Create GitHub App" manifest flow). Scripts
 * still need 'unsafe-inline': Next.js and the theme switcher inline small
 * bootstrap scripts, and nonces would make every page dynamic. Development
 * also needs 'unsafe-eval' and a websocket for hot reload.
 */
const isDev = process.env.NODE_ENV !== "production";
const apiOrigin = new URL(process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").origin;
// GitHub Enterprise Server: where its "new App" form lives.
const githubWeb = process.env.NEXT_PUBLIC_GITHUB_WEB_URL ?? "https://github.com";

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://avatars.githubusercontent.com",
  "font-src 'self' data:",
  `connect-src 'self' ${apiOrigin}${isDev ? " ws: wss:" : ""}`,
  `form-action 'self' ${new URL(githubWeb).origin}`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ...(isDev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
