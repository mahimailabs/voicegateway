import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  output: 'export',
  reactStrictMode: true,
  // Static export has no image optimizer, so serve pictures as they are.
  images: { unoptimized: true },
};

export default withMDX(config);
