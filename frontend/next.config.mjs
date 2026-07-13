/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    // Alle /api-Requests server-seitig ans Backend proxien —
    // Session-Cookie bleibt dadurch same-origin.
    const backend = process.env.BACKEND_INTERNAL_URL || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
