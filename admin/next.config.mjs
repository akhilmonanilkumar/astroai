/**
 * Admin console. Runs as a small Node server (`output: "standalone"`): `/api/*` is proxied
 * to the backend admin API at runtime (app/api/[...path]/route.ts), so the browser only
 * ever talks to this origin and the same image works in every environment.
 */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Cache-Control", value: "no-store" },
        ],
      },
    ];
  },
};

export default nextConfig;
