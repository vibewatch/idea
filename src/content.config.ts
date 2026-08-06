import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';

const reports = defineCollection({
  loader: glob({
    base: './reports/reddit',
    pattern: ['**/*.md', '!zh/**'],
  }),
});

const reportsZh = defineCollection({
  loader: glob({
    base: './reports/reddit/zh',
    pattern: '**/*.md',
  }),
});

export const collections = { reports, reportsZh };