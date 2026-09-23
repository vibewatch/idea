/**
 * Report tables must never scroll sideways. Tables are wrapped and every cell is
 * labelled with its column header so narrow screens can stack rows as cards.
 */
const DENSE_MIN_COLUMNS = 6;
const VISUAL_COLUMN_RE = /^(?:visual proof|视觉证据)$/i;
const DIRECT_IMAGE_HOSTS = new Set([
  'i.redd.it',
  'preview.redd.it',
  'external-preview.redd.it',
  'i.imgur.com',
]);

function childElements(node, tagName) {
  if (!Array.isArray(node?.children)) return [];
  return node.children.filter(
    (child) => child.type === 'element' && (!tagName || child.tagName === tagName),
  );
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

function hasAncestor(node, ctx, tagName) {
  let parent = ctx.parent(node);
  while (parent?.type === 'element') {
    if (parent.tagName === tagName) return true;
    if (parent.tagName === 'td') return false;
    parent = ctx.parent(parent);
  }
  return false;
}

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
      if (typeof label !== 'string' || !VISUAL_COLUMN_RE.test(label)) {
        return undefined;
      }
      return imagePreview(node, ctx, label.includes('视觉') ? 'zh' : 'en');
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
