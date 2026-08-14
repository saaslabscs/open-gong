import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `next dev` only trusts the hostname it was started with (localhost) and
  // returns 403 for /_next/* assets requested from any other origin. That kills
  // hydration silently — the page still server-renders, so every button looks
  // dead rather than broken. Trust the other ways this machine is reachable.
  allowedDevOrigins: [
    "127.0.0.1",
    "[::1]",
    "*.local", // mDNS, e.g. rahul-mac.local
    "192.168.*.*", // private LAN — phones/tablets testing against the dev box
    "10.*.*.*",
  ],
};

export default nextConfig;
