/**
 * Report tables must never scroll sideways. Tables are wrapped and every cell is
 * labelled with its column header so narrow screens can stack rows as cards.
 */
const DENSE_MIN_COLUMNS = 6;

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
