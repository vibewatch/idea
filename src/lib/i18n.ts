import { withBase } from './paths';

export type Locale = 'en' | 'zh';

export const LOCALES: readonly Locale[] = ['en', 'zh'];

const basePrefix = import.meta.env.BASE_URL.replace(/\/$/, '');

function stripBase(pathname: string): string {
  if (basePrefix && pathname.startsWith(basePrefix)) {
    return pathname.slice(basePrefix.length) || '/';
  }
  return pathname;
}

export function localeFromPath(pathname: string): Locale {
  const path = stripBase(pathname);
  return path === '/zh' || path.startsWith('/zh/') ? 'zh' : 'en';
}

export function localePath(locale: Locale, path = '/'): string {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return withBase(locale === 'zh' ? `/zh${normalized}` : normalized);
}

/** Map the current URL onto its counterpart in another locale. */
export function alternatePath(pathname: string, locale: Locale): string {
  const path = stripBase(pathname);
  const bare = path === '/zh' ? '/' : path.startsWith('/zh/') ? path.slice(3) : path;
  return localePath(locale, bare || '/');
}

export const localeLabels: Record<Locale, string> = {
  en: 'EN',
  zh: '中文',
};

export const htmlLang: Record<Locale, string> = {
  en: 'en',
  zh: 'zh-CN',
};

const en = {
  siteName: 'Idea Signal Desk',
  siteTagline: 'Source-linked builder reports',
  siteDescription: 'Concise, source-linked reports on builder problems, products, and outcomes.',
  nav: { reports: 'Reports', method: 'Method' },
  search: { trigger: 'Search', label: 'Search reports' },
  language: { label: 'Switch language', switchTo: 'Switch to Chinese' },
  theme: { toLight: 'Switch to light theme', toDark: 'Switch to dark theme' },
  home: {
    chip: 'Daily builder intelligence',
    titleLead: 'Builder signal, ',
    titleEm: 'with sources',
    titleTail: '.',
    lede: 'Concise reports on real problems, products, and outcomes — every finding stays linked to the conversation it came from.',
    readLatest: 'Read the latest report',
    howItWorks: 'How reports are made',
    statReports: 'Reports',
    statSources: 'Linked sources',
    statLatest: 'Latest',
    latestEyebrow: 'Fresh off the desk',
    latestTitle: 'Latest report',
    allReports: 'All reports →',
    archiveEyebrow: 'Archive',
    archiveTitle: 'Earlier reports',
    browseAll: 'Browse all →',
    empty: 'No reports have been published yet.',
  },
  archive: {
    chip: 'Archive',
    title: 'Reports',
    lede: 'Published reports, newest first. Every claim links back to its source thread.',
    filterLabel: 'Filter reports',
    filterPlaceholder: 'Filter reports',
    one: ' report',
    many: ' reports',
    emptyTitle: 'No matching reports',
    clear: 'Clear filter',
  },
  method: {
    chip: 'Method',
    title: 'How reports are made',
    lede: 'Reports preserve useful details while keeping claims close to their sources.',
    processTitle: 'Process',
    rulesTitle: 'Editorial rules',
    limitsTitle: 'Limits',
    limits:
      'Community posts are self-selected and outcomes are often self-reported. These reports help identify questions worth testing; they do not replace customer research or market proof.',
    browse: 'Browse reports →',
    steps: [
      { title: 'Collect', text: 'Save daily posts, comments, links, and media references from focused builder communities.' },
      { title: 'Prepare', text: 'Organize sources, remove duplicates, and keep provenance visible.' },
      { title: 'Extract', text: 'Identify concrete projects, problems, ideas, outcomes, and useful visual evidence.' },
      { title: 'Validate', text: 'Check report structure, source links, and media references before publication.' },
    ],
    principles: [
      { title: 'Start with specifics', text: 'Named projects, direct links, workflows, and metrics come before broad themes.' },
      { title: 'Keep evidence distinct', text: 'A complaint, a product idea, and a paying customer are different kinds of evidence.' },
      { title: 'Leave gaps visible', text: 'Unknown traction, conflicting claims, and missing proof remain explicit.' },
    ],
  },
  card: { minutes: 'min read', sources: 'linked sources', read: 'Read report' },
  report: {
    home: 'Home',
    reports: 'Reports',
    published: 'Published',
    reading: 'Reading time',
    minutes: 'min',
    sections: 'Sections',
    sources: 'Linked sources',
    posts: 'Posts reviewed',
    older: '← Older report',
    newer: 'Newer report →',
    contents: 'Contents',
    backToTop: 'Back to top',
  },
  footer: { rights: 'Source-linked builder reports', rss: 'RSS' },
};

type Strings = typeof en;

const zh: Strings = {
  siteName: 'Idea 情报台',
  siteTagline: '每条结论都可回溯来源',
  siteDescription: '面向创造者的简报：真实问题、真实产品、真实结果，每条结论都附来源链接。',
  nav: { reports: '报告', method: '方法' },
  search: { trigger: '搜索', label: '搜索报告' },
  language: { label: '切换语言', switchTo: '切换到英文' },
  theme: { toLight: '切换到浅色主题', toDark: '切换到深色主题' },
  home: {
    chip: '每日创造者情报',
    titleLead: '创造者信号，',
    titleEm: '皆有出处',
    titleTail: '。',
    lede: '聚焦真实问题、产品与结果的简报——每条结论都链回它出自的那场讨论。',
    readLatest: '阅读最新报告',
    howItWorks: '报告如何产出',
    statReports: '报告总数',
    statSources: '引用来源',
    statLatest: '最近更新',
    latestEyebrow: '最新出炉',
    latestTitle: '最新报告',
    allReports: '全部报告 →',
    archiveEyebrow: '往期',
    archiveTitle: '更早的报告',
    browseAll: '浏览全部 →',
    empty: '目前还没有发布任何报告。',
  },
  archive: {
    chip: '往期',
    title: '报告',
    lede: '已发布的报告，从新到旧。每条说法都可回溯到原帖。',
    filterLabel: '筛选报告',
    filterPlaceholder: '筛选报告',
    one: ' 篇报告',
    many: ' 篇报告',
    emptyTitle: '没有匹配的报告',
    clear: '清除筛选',
  },
  method: {
    chip: '方法',
    title: '报告如何产出',
    lede: '在保留有用细节的同时，让每条说法都紧贴来源。',
    processTitle: '流程',
    rulesTitle: '编辑准则',
    limitsTitle: '局限',
    limits:
      '社区帖子由发帖人自行选择呈现，结果也多为作者自述。这些报告帮你找出值得验证的问题，但不能替代用户调研或市场验证。',
    browse: '浏览报告 →',
    steps: [
      { title: '采集', text: '每日抓取重点创造者社区的帖子、评论、链接与媒体引用。' },
      { title: '整理', text: '归置来源、去重，并保留可追溯的出处。' },
      { title: '提炼', text: '识别具体项目、痛点、想法、结果，以及有价值的视觉证据。' },
      { title: '校验', text: '发布前核对报告结构、来源链接与媒体引用。' },
    ],
    principles: [
      { title: '先讲具体', text: '先给出项目名称、直达链接、工作流与指标，再谈宏观主题。' },
      { title: '区分证据类型', text: '一句抱怨、一个产品想法、一个付费用户，是三种不同的证据。' },
      { title: '暴露缺口', text: '增长势头不明、说法互相矛盾、缺少佐证，都会明确写出来。' },
    ],
  },
  card: { minutes: '分钟阅读', sources: '条引用来源', read: '阅读报告' },
  report: {
    home: '首页',
    reports: '报告',
    published: '发布日期',
    reading: '阅读时长',
    minutes: '分钟',
    sections: '章节数',
    sources: '引用来源',
    posts: '分析帖子',
    older: '← 更早的报告',
    newer: '更新的报告 →',
    contents: '目录',
    backToTop: '回到顶部',
  },
  footer: { rights: '每条结论都附来源链接', rss: 'RSS 订阅' },
};

const dictionaries: Record<Locale, Strings> = { en, zh };

export function useStrings(locale: Locale): Strings {
  return dictionaries[locale];
}
