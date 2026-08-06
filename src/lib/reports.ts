import type { CollectionEntry } from 'astro:content';

export type ReportEntry = CollectionEntry<'reports'> | CollectionEntry<'reportsZh'>;

export type ReportLocale = 'en' | 'zh';

export interface ReportSection {
  title: string;
  body: string;
}

export interface SignalGroup {
  key: 'projects' | 'pain' | 'ideas' | 'media';
  label: string;
  count: number;
  items: string[];
}

export interface ReportMeta {
  id: string;
  date: string;
  year: string;
  title: string;
  description: string;
  displayDate: string;
  shortDate: string;
  readingMinutes: number;
  postCount: number | null;
  sectionCount: number;
  citationCount: number;
  tableCount: number;
  sections: ReportSection[];
  signals: SignalGroup[];
}

const SECTION_PATTERN = /^##\s+(.+)$/gm;
const REDDIT_LINK_PATTERN = /https:\/\/(?:www\.)?reddit\.com\/r\/[^\s)]+/gi;

function cleanInlineMarkdown(value: string): string {
  return value
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/<br\s*\/?>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/[`*_~>#]/g, '')
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

function truncateAtWord(value: string, maximum: number): string {
  if (value.length <= maximum) {
    return value;
  }
  const shortened = value.slice(0, maximum + 1);
  const boundary = shortened.lastIndexOf(' ');
  return `${shortened.slice(0, boundary > maximum * 0.7 ? boundary : maximum).trim()}…`;
}

function extractTitle(body: string, fallback: string): string {
  const match = body.match(/^#\s+(.+)$/m);
  return cleanInlineMarkdown(match?.[1] ?? fallback);
}

export function extractSections(body: string): ReportSection[] {
  const matches = [...body.matchAll(SECTION_PATTERN)];
  return matches.map((match, index) => ({
    title: cleanInlineMarkdown(match[1] ?? ''),
    body: body.slice(
      (match.index ?? 0) + match[0].length,
      matches[index + 1]?.index ?? body.length,
    ).trim(),
  }));
}

function proseFromSection(section: ReportSection | undefined): string {
  if (!section) {
    return '';
  }
  const prose = section.body
    .split('\n')
    .filter((line) => !line.trim().startsWith('|') && line.trim() !== '---')
    .join(' ');
  return cleanInlineMarkdown(prose);
}

function tableLabels(section: ReportSection | undefined): string[] {
  if (!section) {
    return [];
  }
  const tableLines = section.body
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|') && line.endsWith('|'));
  if (tableLines.length < 3) {
    return [];
  }
  return tableLines
    .slice(2)
    .filter((line) => !/^\|\s*:?-{3}/.test(line))
    .map((line) => cleanInlineMarkdown(line.slice(1, -1).split('|')[0] ?? ''))
    .filter((label, index, labels) => Boolean(label) && labels.indexOf(label) === index);
}

function findSection(
  sections: ReportSection[],
  patterns: RegExp[],
): ReportSection | undefined {
  for (const pattern of patterns) {
    const section = sections.find(({ title }) => pattern.test(title));
    if (section) {
      return section;
    }
  }
  return undefined;
}

function signalGroups(sections: ReportSection[]): SignalGroup[] {
  const definitions: Array<{
    key: SignalGroup['key'];
    label: string;
    patterns: RegExp[];
  }> = [
    {
      key: 'projects',
      label: 'Projects & launches',
      patterns: [/new projects/i, /shipped products/i, /launches.*traction/i],
    },
    {
      key: 'pain',
      label: 'Customer pain',
      patterns: [/customer problems/i, /customer pain/i],
    },
    {
      key: 'ideas',
      label: 'Founder ideas',
      patterns: [/founder ideas/i],
    },
    {
      key: 'media',
      label: 'Visual evidence',
      patterns: [/visual.*demo/i, /media evidence/i],
    },
  ];

  return definitions.map(({ key, label, patterns }) => {
    const items = tableLabels(findSection(sections, patterns));
    return { key, label, count: items.length, items: items.slice(0, 3) };
  });
}

function reportDate(entry: ReportEntry): string {
  const match = entry.id.match(/\d{4}-\d{2}-\d{2}/);
  if (!match) {
    throw new Error(`Report id must contain an ISO date: ${entry.id}`);
  }
  return match[0];
}

function formatDate(date: string, options: Intl.DateTimeFormatOptions, locale: ReportLocale = 'en'): string {
  return new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en-US', {
    timeZone: 'UTC',
    ...options,
  }).format(new Date(`${date}T00:00:00Z`));
}

// Chinese prose has no word spacing, so characters and Latin words are counted separately.
function readingMinutes(text: string): number {
  const hanCharacters = (text.match(/[\u3400-\u4dbf\u4e00-\u9fff]/g) ?? []).length;
  const latinWords = (text.match(/[A-Za-z0-9][A-Za-z0-9'’-]*/g) ?? []).length;
  return Math.max(1, Math.ceil(latinWords / 220 + hanCharacters / 400));
}

function extractPostCount(body: string): number | null {
  const match = body.match(/(?:combined\s+)?([\d,]+)-post corpus/i);
  return match ? Number.parseInt(match[1]?.replaceAll(',', '') ?? '', 10) : null;
}

export function getReportMeta(entry: ReportEntry, locale: ReportLocale = 'en'): ReportMeta {
  const date = reportDate(entry);
  const sections = extractSections(entry.body ?? '');
  const summary = proseFromSection(sections[0]);
  const cleaned = cleanInlineMarkdown(entry.body ?? '');
  const citations = new Set((entry.body ?? '').match(REDDIT_LINK_PATTERN) ?? []);

  return {
    id: entry.id,
    date,
    year: date.slice(0, 4),
    title: extractTitle(entry.body ?? '', `Builder intelligence — ${date}`),
    description: truncateAtWord(
      summary || 'Evidence-grounded projects, customer pain, founder ideas, and builder outcomes.',
      260,
    ),
    displayDate: formatDate(date, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    }, locale),
    shortDate: formatDate(date, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    }, locale),
    readingMinutes: readingMinutes(cleaned),
    postCount: extractPostCount(entry.body ?? ''),
    sectionCount: sections.length,
    citationCount: citations.size,
    tableCount: (entry.body ?? '').split('\n').filter((line) => /^\|\s*:?-{3}/.test(line)).length,
    sections,
    signals: signalGroups(sections),
  };
}

export function sortReports(entries: ReportEntry[]): ReportEntry[] {
  return [...entries].sort(
    (left, right) => getReportMeta(right).date.localeCompare(getReportMeta(left).date),
  );
}