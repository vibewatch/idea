import sitemap from '@astrojs/sitemap';
import { defineConfig } from 'astro/config';

const site = process.env.ASTRO_SITE ?? 'https://idea.genisisiq.com';
const base = process.env.ASTRO_BASE;

export default defineConfig({
  site,
  ...(base ? { base } : {}),
  output: 'static',
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  integrations: [sitemap()],
  prefetch: {
    prefetchAll: true,
    defaultStrategy: 'viewport',
  },
  markdown: {
    shikiConfig: {
      themes: {
        light: 'github-light',
        dark: 'github-dark',
      },
    },
  },
});