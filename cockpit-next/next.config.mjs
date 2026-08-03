/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',           // single-folder deploy on the VPS
  eslint: { ignoreDuringBuilds: true },
};
export default nextConfig;
