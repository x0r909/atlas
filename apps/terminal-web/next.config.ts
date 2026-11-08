import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The terminal shows live money state. A Next.js server-side cache would serve a stale
  // position and an operator would act on it, so nothing is cached by default here.
  experimental: { staleTimes: { dynamic: 0, static: 0 } },
  poweredByHeader: false,
};

export default nextConfig;
