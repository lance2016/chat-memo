import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Keep the development compiler and production validation isolated. Running
  // `next build` while `next dev` is alive otherwise rewrites the same `.next`
  // directory and can leave the HMR server unavailable until it is restarted.
  distDir: process.env.NODE_ENV === "production" ? ".next-build" : ".next-dev",
};

export default nextConfig;
