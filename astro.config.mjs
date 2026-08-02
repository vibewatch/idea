import sitemap from '@astrojs/sitemap';
import { defineConfig } from 'astro/config';

const site = process.env.ASTRO_SITE ?? 'https://vibewatch.github.io';
const base = process.env.ASTRO_BASE ?? '/idea';

export default defineConfig({
  site,
  base,
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