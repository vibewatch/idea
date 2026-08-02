import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';
import { getReportMeta, sortReports } from '../lib/reports';
import { withBase } from '../lib/paths';

export async function GET(context: { site?: URL }) {
  const reports = sortReports(await getCollection('reports'));
  const site = context.site ?? new URL('https://idea.genisisiq.com');

  return rss({
    title: 'Idea Signal Desk',
    description: 'Source-linked builder intelligence: projects, pain points, founder ideas, launches, outcomes, and useful media.',
    site,
    items: reports.map((entry) => {
      const meta = getReportMeta(entry);
      return {
        title: meta.title,
        description: meta.description,
        pubDate: new Date(`${meta.date}T00:00:00Z`),
        link: new URL(withBase(`/reports/${meta.date}/`), site).href,
        categories: ['Reddit', 'SaaS', 'Builder intelligence'],
      };
    }),
    customData: '<language>en-us</language>',
  });
}