import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';

const reports = defineCollection({
  loader: glob({
    base: './reports/reddit',
    pattern: '**/*.md',
  }),
});

export const collections = { reports };