/* 表格：表头排序与就地筛选，只影响展示，不动数据 */
function isNumeric(text) {
  return text !== '' && !Number.isNaN(Number(text.replace(/[, ]/g, '')));
}

function sortTable(table, index, ascending) {
  const body = table.tBodies[0];
  if (!body) return;
  const rows = Array.from(body.rows).filter((row) => row.cells.length > index);
  rows.sort((left, right) => {
    const a = left.cells[index].textContent.trim();
    const b = right.cells[index].textContent.trim();
    const diff = isNumeric(a) && isNumeric(b)
      ? Number(a.replace(/[, ]/g, '')) - Number(b.replace(/[, ]/g, ''))
      : a.localeCompare(b, 'zh-Hans-CN');
    return ascending ? diff : -diff;
  });
  rows.forEach((row) => body.appendChild(row));
}

function wireSort(table) {
  Array.from(table.tHead ? table.tHead.rows : []).forEach((row) => {
    Array.from(row.cells).forEach((cell, index) => {
      cell.style.cursor = 'pointer';
      cell.title = '排序';
      cell.addEventListener('click', () => {
        const ascending = cell.dataset.order !== 'asc';
        cell.dataset.order = ascending ? 'asc' : 'desc';
        sortTable(table, index, ascending);
      });
    });
  });
}

function wireFilter(input) {
  const scope = input.closest('section') || document;
  const table = scope.querySelector('table');
  if (!table || !table.tBodies[0]) return;
  input.addEventListener('input', () => {
    const needle = input.value.trim().toLowerCase();
    Array.from(table.tBodies[0].rows).forEach((row) => {
      const text = row.textContent.toLowerCase();
      row.hidden = needle !== '' && !text.includes(needle);
    });
  });
}

document.querySelectorAll('table').forEach(wireSort);
document.querySelectorAll('input[data-filter]').forEach(wireFilter);
