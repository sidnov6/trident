/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
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
