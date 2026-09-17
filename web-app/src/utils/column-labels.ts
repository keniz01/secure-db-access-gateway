const ACRONYMS = new Set([
  'id',
  'url',
  'sql',
  'api',
  'uuid',
  'http',
  'https',
  'json',
  'csv',
  'db',
  'ip',
  'os',
  'sku',
  'isbn',
]);

const humanizeWord = (word: string): string => {
  const lower = word.toLowerCase();
  if (ACRONYMS.has(lower)) {
    return lower.toUpperCase();
  }
  return lower.charAt(0).toUpperCase() + lower.slice(1);
};

export const humanizeColumn = (key: string): string => {
  const spaced = key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim();
  if (!spaced) {
    return key;
  }
  return spaced.split(/\s+/).map(humanizeWord).join(' ');
};

export const resolveColumnLabel = (
  key: string,
  labels?: Record<string, string> | null
): string => {
  const provided = labels?.[key];
  if (typeof provided === 'string' && provided.trim()) {
    return provided.trim();
  }
  return humanizeColumn(key);
};
