import sitemap from '@astrojs/sitemap';
import { satteri } from '@astrojs/markdown-satteri';
import { defineConfig } from 'astro/config';
import { reportTablesPlugin } from './src/lib/report-tables-plugin.mjs';

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
    processor: satteri({ hastPlugins: [reportTablesPlugin] }),
    shikiConfig: {
      themes: {
        light: 'github-light',
        dark: 'github-dark',
      },
    },
  },
});