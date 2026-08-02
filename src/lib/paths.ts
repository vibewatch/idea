const basePrefix = import.meta.env.BASE_URL.replace(/\/$/, '');

export function withBase(path = '/'): string {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return `${basePrefix}${normalized}` || '/';
}