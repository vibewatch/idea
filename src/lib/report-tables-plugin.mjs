/**
 * Report tables must never scroll sideways. Tables are wrapped and every cell is
 * labelled with its column header so narrow screens can stack rows as cards.
 */
const DENSE_MIN_COLUMNS = 6;
const VISUAL_COLUMN_RE = /^(?:visual proof|视觉证据)$/i;
const EVIDENCE_SECTION_RE = /^(?:2\. Evidence Ledger|2\. 证据台账)$/i;
const CASE_FIELD_PATTERNS = [
  [/^Primary link:/i, 'primary-link'],
  [/^主要链接[：:]/, 'primary-link'],
  [/^Stage:/i, 'stage'],
  [/^阶段[：:]/, 'stage'],
  [/^User or problem:/i, 'user-or-problem'],
  [/^用户或问题[：:]/, 'user-or-problem'],
  [/^Build, test, or event:/i, 'build-test-event'],
  [/^构建、测试或事件[：:]/, 'build-test-event'],
  [/^Evidence:/i, 'evidence'],
  [/^证据[：:]/, 'evidence'],
  [/^Visual proof:/i, 'visual-proof'],
  [/^视觉证据[：:]/, 'visual-proof'],
  [/^Limitation or next proof:/i, 'limitation'],
  [/^局限或下一步证据[：:]/, 'limitation'],
  [/^Reddit source:/i, 'reddit-source'],
  [/^Reddit 来源[：:]/i, 'reddit-source'],
];
const DIRECT_IMAGE_HOSTS = new Set([
  'i.redd.it',
  'preview.redd.it',
  'external-preview.redd.it',
  'i.imgur.com',
]);
const PENDING_CLASSES = new WeakMap();

function childElements(node, tagName) {
  if (!Array.isArray(node?.children)) return [];
  return node.children.filter(
    (child) => child.type === 'element' && (!tagName || child.tagName === tagName),
  );
}

function addClass(node, ctx, className) {
  const current = node.properties?.className;
  const classes =
    PENDING_CLASSES.get(node) ??
    (Array.isArray(current)
      ? [...current]
      : typeof current === 'string'
        ? current.split(/\s+/).filter(Boolean)
        : []);
  if (!classes.includes(className)) {
    classes.push(className);
    PENDING_CLASSES.set(node, classes);
    ctx.setProperty(node, 'className', classes);
  }
}

function headerLabels(table, ctx) {
  const [head] = childElements(table, 'thead');
  const [row] = childElements(head, 'tr');
  if (!row) return [];
  return childElements(row)
    .filter(({ tagName }) => tagName === 'th' || tagName === 'td')
    .map((cell) => ctx.textContent(cell).replace(/\s+/g, ' ').trim());
}

function labelBodyCells(table, ctx, labels) {
  let columns = labels.length;

  for (const body of childElements(table, 'tbody')) {
    for (const row of childElements(body, 'tr')) {
      const cells = childElements(row, 'td');
      columns = Math.max(columns, cells.length);
      cells.forEach((cell, index) => {
        const label = labels[index];
        if (label) ctx.setProperty(cell, 'data-label', label);
      });
    }
  }

  return columns;
}

function directImageUrl(value) {
  if (typeof value !== 'string') return undefined;

  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') return undefined;
    if (
      DIRECT_IMAGE_HOSTS.has(url.hostname.toLowerCase()) ||
      /\.(?:avif|gif|jpe?g|png|webp)$/i.test(url.pathname)
    ) {
      return url.href;
    }
  } catch {
    return undefined;
  }

  return undefined;
}

function imageIcon() {
  return {
    type: 'element',
    tagName: 'svg',
    properties: {
      className: ['media-preview__icon'],
      viewBox: '0 0 20 20',
      ariaHidden: 'true',
      focusable: 'false',
    },
    children: [
      {
        type: 'element',
        tagName: 'path',
        properties: {
          d: 'M3.25 4.25h13.5v11.5H3.25zM5.5 13l3.15-3.35 2.35 2.2 1.55-1.55 2.2 2.7M13.35 7.25h.01',
          fill: 'none',
          stroke: 'currentColor',
          strokeWidth: '1.5',
        },
        children: [],
      },
    ],
  };
}

function imagePreview(node, ctx, locale) {
  const candidate =
    node.tagName === 'a' ? node.properties?.href : ctx.textContent(node).trim();
  const source = directImageUrl(candidate);
  if (!source) return undefined;

  const originalLabel = ctx.textContent(node).replace(/\s+/g, ' ').trim();
  const label = locale === 'zh' ? '查看图片' : 'Preview image';
  const alt = originalLabel && originalLabel !== source ? originalLabel : label;

  return {
    type: 'element',
    tagName: 'details',
    properties: {
      className: ['media-preview'],
    },
    children: [
      {
        type: 'element',
        tagName: 'summary',
        properties: {
          className: ['media-preview__trigger'],
        },
        children: [
          imageIcon(),
          {
            type: 'element',
            tagName: 'span',
            properties: {},
            children: [{ type: 'text', value: label }],
          },
        ],
      },
      {
        type: 'element',
        tagName: 'a',
        properties: {
          className: ['media-preview__link'],
          href: source,
          target: '_blank',
          rel: ['noreferrer'],
          title: locale === 'zh' ? '在新标签页打开原图' : 'Open original image in a new tab',
        },
        children: [
          {
            type: 'element',
            tagName: 'img',
            properties: {
              src: source,
              alt,
              loading: 'lazy',
              decoding: 'async',
              referrerPolicy: 'no-referrer',
            },
            children: [],
          },
        ],
      },
    ],
  };
}

function tableCell(node, ctx) {
  let parent = ctx.parent(node);
  while (parent?.type === 'element') {
    if (parent.tagName === 'td') return parent;
    if (parent.tagName === 'table') return undefined;
    parent = ctx.parent(parent);
  }
  return undefined;
}

function ancestorWithProperty(node, ctx, property, value) {
  let parent = ctx.parent(node);
  while (parent?.type === 'element') {
    if (parent.properties?.[property] === value) return parent;
    if (parent.tagName === 'table' || parent.tagName === 'h2') return undefined;
    parent = ctx.parent(parent);
  }
  return undefined;
}

function hasAncestor(node, ctx, tagName) {
  let parent = ctx.parent(node);
  while (parent?.type === 'element') {
    if (parent.tagName === tagName) return true;
    if (parent.tagName === 'td') return false;
    parent = ctx.parent(parent);
  }
  return false;
}

function sectionHeading(node, ctx) {
  const parent = ctx.parent(node);
  const index = ctx.indexOf(node);
  if (!parent?.children || index === undefined) return undefined;
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const sibling = parent.children[cursor];
    if (sibling?.type === 'element' && sibling.tagName === 'h2') {
      return ctx.textContent(sibling).replace(/\s+/g, ' ').trim();
    }
  }
  return undefined;
}

function caseField(text) {
  const normalized = text.replace(/\s+/g, ' ').trim();
  return CASE_FIELD_PATTERNS.find(([pattern]) => pattern.test(normalized))?.[1];
}

export const reportCasesPlugin = {
  name: 'report-cases',
  element: {
    filter: ['h3'],
    visit(node, ctx) {
      if (!EVIDENCE_SECTION_RE.test(sectionHeading(node, ctx) ?? '')) {
        return undefined;
      }

      const parent = ctx.parent(node);
      const index = ctx.indexOf(node);
      if (!parent?.children || index === undefined) return undefined;

      let caseNumber = 1;
      for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
        const sibling = parent.children[cursor];
        if (sibling?.type !== 'element') continue;
        if (sibling.tagName === 'h2') break;
        if (sibling.tagName === 'h3') caseNumber += 1;
      }

      addClass(node, ctx, 'report-case__title');
      ctx.setProperty(node, 'data-case-index', String(caseNumber).padStart(2, '0'));

      const content = [];
      for (let cursor = index + 1; cursor < parent.children.length; cursor += 1) {
        const sibling = parent.children[cursor];
        if (sibling?.type !== 'element') continue;
        if (sibling.tagName === 'h2' || sibling.tagName === 'h3' || sibling.tagName === 'hr') {
          break;
        }
        addClass(sibling, ctx, 'report-case__content');
        if (sibling.tagName === 'p') {
          const field = caseField(ctx.textContent(sibling));
          if (field) {
            addClass(sibling, ctx, 'report-case__field');
            ctx.setProperty(sibling, 'data-case-field', field);
          }
        }
        content.push(sibling);
      }
      const last = content.at(-1);
      if (last) addClass(last, ctx, 'report-case__end');
      return undefined;
    },
  },
};

export const reportMediaPlugin = {
  name: 'report-media',
  element: {
    filter: ['a', 'code'],
    visit(node, ctx) {
      if (node.tagName === 'code' && hasAncestor(node, ctx, 'a')) {
        return undefined;
      }
      const cell = tableCell(node, ctx);
      const label = cell?.properties?.['data-label'];
      const visualCase = ancestorWithProperty(
        node,
        ctx,
        'data-case-field',
        'visual-proof',
      );
      const inVisualTableCell =
        typeof label === 'string' && VISUAL_COLUMN_RE.test(label);
      if (!inVisualTableCell && !visualCase) {
        return undefined;
      }

      const visualCaseText = visualCase ? ctx.textContent(visualCase).trim() : '';
      const locale =
        (typeof label === 'string' && label.includes('视觉')) ||
        visualCaseText.startsWith('视觉证据')
          ? 'zh'
          : 'en';
      if (node.tagName === 'a' && childElements(node, 'img').length) {
        const source = directImageUrl(node.properties?.href);
        if (!source) return undefined;
        addClass(node, ctx, 'report-case__media-link');
        ctx.setProperty(node, 'target', '_blank');
        ctx.setProperty(node, 'rel', ['noreferrer']);
        ctx.setProperty(
          node,
          'title',
          locale === 'zh' ? '在新标签页打开原图' : 'Open original image in a new tab',
        );
        for (const image of childElements(node, 'img')) {
          addClass(image, ctx, 'report-case__media-image');
          ctx.setProperty(image, 'loading', 'lazy');
          ctx.setProperty(image, 'decoding', 'async');
          ctx.setProperty(image, 'referrerPolicy', 'no-referrer');
        }
        return undefined;
      }
      return imagePreview(node, ctx, locale);
    }
  },
};

export const reportTablesPlugin = {
  name: 'report-tables',
  element: {
    filter: ['table'],
    visit(node, ctx) {
      const labels = headerLabels(node, ctx);
      const columns = labelBodyCells(node, ctx, labels);

      ctx.wrapNode(node, {
        type: 'element',
        tagName: 'figure',
        properties: {
          className: ['report-table'],
          'data-columns': String(columns),
          'data-density': columns >= DENSE_MIN_COLUMNS ? 'dense' : 'regular',
        },
        children: [],
      });
    },
  },
};
