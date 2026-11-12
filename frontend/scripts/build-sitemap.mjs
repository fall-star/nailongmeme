import { readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const SITE = 'https://nailong.fall-star.top';
const dist = resolve(import.meta.dirname, '../dist');

async function walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const out = [];
  for (const entry of entries) {
    const path = resolve(dir, entry.name);
    if (entry.isDirectory()) out.push(...await walk(path));
    else if (entry.isFile() && entry.name === 'index.html') out.push(path);
  }
  return out;
}

const files = await walk(dist);
const routes = files.map((file) => {
  const relative = file.slice(dist.length).replaceAll('\\', '/').replace(/index\.html$/, '');
  return relative === '/' ? '/' : relative.replace(/\/$/, '');
}).filter((route) => route !== '/404' && route !== '/account').sort();

const escape = (value) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');
const urls = routes.map((route) => `  <url><loc>${escape(new URL(route, SITE).href)}</loc></url>`).join('\n');
const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`;
await writeFile(resolve(dist, 'sitemap.xml'), xml);
console.log(`sitemap: ${routes.length} URLs -> ${resolve(dist, 'sitemap.xml')}`);
