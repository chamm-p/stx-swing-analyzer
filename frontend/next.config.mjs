/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // Kein 308-Redirect bei Trailing Slash — der MCP-Endpunkt lebt unter
  // /api/mcp/ und muss unverändert durch den Proxy.
  skipTrailingSlashRedirect: true,
  async rewrites() {
    // Alle /api-Requests server-seitig ans Backend proxien —
    // Session-Cookie bleibt dadurch same-origin.
    const backend = process.env.BACKEND_INTERNAL_URL || "http://localhost:8000";
    return [
      // MCP braucht den Trailing Slash (Starlette-Mount würde sonst auf
      // die interne Backend-URL redirecten) — beide Formen explizit mappen.
      { source: "/api/mcp", destination: `${backend}/api/mcp/` },
      { source: "/api/mcp/", destination: `${backend}/api/mcp/` },
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
    ];
  },
};

export default nextConfig;
