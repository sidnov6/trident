/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  // HF single-container Space: build a static export (served by FastAPI on one
  // origin). Gated by HF_EXPORT so the normal `next start` server build is
  // unaffected.
  ...(process.env.HF_EXPORT
    ? { output: "export", images: { unoptimized: true } }
    : {}),
  transpilePackages: [
    "@deck.gl/core",
    "@deck.gl/react",
    "@deck.gl/layers",
    "@deck.gl/aggregation-layers",
    "@deck.gl/geo-layers",
    "@deck.gl/mapbox",
    "deck.gl",
  ],
  webpack: (config) => {
    // maplibre-gl ships ESM workers; leave fallback empty for node builtins.
    config.resolve.fallback = { ...config.resolve.fallback, fs: false };
    return config;
  },
};

module.exports = nextConfig;
