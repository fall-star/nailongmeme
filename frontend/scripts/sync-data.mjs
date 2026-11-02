import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const API_BASE = (process.env.API_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '');
const pageSize = 100;
const items = [];
let total = null;
let offset = 0;

while (total === null || offset < total) {
  const response = await fetch(`${API_BASE}/api/gallery?limit=${pageSize}&offset=${offset}`);
  if (!response.ok) throw new Error(`gallery fetch failed: ${response.status}`);
  const body = await response.json();
  total = body.total;
  for (const item of body.items) {
    items.push({
      id: item.id,
      name: item.name,
      tag: item.tag,
      format: item.format,
      width: item.width,
      height: item.height,
      bytes: item.bytes,
      image: `/api/images/${item.id}`,
      download: `/api/images/${item.id}?download=1`,
      thumb320: `/api/thumbs/${item.id}/320`,
      thumb640: `/api/thumbs/${item.id}/640`,
    });
  }
  offset += body.items.length;
  if (!body.items.length) break;
}

const file = resolve(dirname(fileURLToPath(import.meta.url)), '../src/data/gallery.json');
await mkdir(dirname(file), { recursive: true });
await writeFile(file, JSON.stringify({ total: items.length, generatedAt: new Date().toISOString(), items }, null, 2) + '\n');
console.log(`wrote ${items.length}/${total} public items -> ${file}`);
