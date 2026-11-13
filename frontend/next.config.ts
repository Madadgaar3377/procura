import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  distDir: process.env.NEXT_BUILD_DIR || ".next",
  images: { unoptimized: true },
  async headers() {
    const securityHeaders = [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "no-referrer" },
      { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
      { key: "Content-Security-Policy", value: "base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'" },
      { key: "Content-Security-Policy-Report-Only", value: "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'" },
    ];
    const publicRoutes = [
      "/",
      "/how-it-works",
      "/suppliers",
      "/suppliers/:path*",
      "/industries",
      "/pricing",
      "/about",
      "/contact",
      "/faq",
      "/privacy",
      "/terms",
    ];
    return [
      { source: "/:path*", headers: securityHeaders },
      ...publicRoutes.map((source) => ({
        source,
        headers: [{ key: "Cache-Control", value: "private, no-cache, must-revalidate" }],
      })),
    ];
  },
};

export default nextConfig;
