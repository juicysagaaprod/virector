import type { NextConfig } from "next";

const apiOrigin = (
  process.env.VIRECTOR_API_INTERNAL_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
