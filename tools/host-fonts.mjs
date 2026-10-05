/**
 * Serve the library's web fonts from email-service instead of Google Fonts.
 *
 *   node tools/host-fonts.mjs
 *
 * Inlines every fonts.googleapis.com `@import` of renderer/library/<set>/head.css,
 * downloads each fonts.gstatic.com file into static/library/fonts/, and points the
 * CSS at `{{ system.assets_url }}/fonts/…`. import-demos.mjs runs it on every import.
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const libraryDir = join(root, 'renderer', 'library');
const fontsDir = join(root, 'static', 'library', 'fonts');
const ASSETS_TOKEN = '{{ system.assets_url }}';

// Google Fonts picks the file format from the user agent; this one gets woff2.
const USER_AGENT =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36';

const IMPORT_RE = /@import\s+url\(\s*['"]?(https:\/\/fonts\.googleapis\.com\/[^'")]+)['"]?\s*\)\s*;/g;
const FILE_RE = /url\(\s*['"]?https:\/\/fonts\.gstatic\.com\/s\/([a-zA-Z0-9_./-]+)['"]?\s*\)/g;

async function download(url, init) {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response;
}

async function inlineImports(css) {
  let out = css;
  for (const [rule, url] of css.matchAll(IMPORT_RE)) {
    const response = await download(url.replaceAll('&amp;', '&'), { headers: { 'User-Agent': USER_AGENT } });
    const faces = (await response.text()).replace(/\/\*[\s\S]*?\*\//g, '').trim();
    out = out.replace(rule, faces);
  }
  return out;
}

export async function hostFonts(css) {
  const inlined = await inlineImports(css);
  for (const [, path] of inlined.matchAll(FILE_RE)) {
    const target = join(fontsDir, path);
    if (existsSync(target)) continue;
    const response = await download(`https://fonts.gstatic.com/s/${path}`);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, Buffer.from(await response.arrayBuffer()));
  }
  return inlined.replace(FILE_RE, (_match, path) => `url('${ASSETS_TOKEN}/fonts/${path}')`);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  for (const set of readdirSync(libraryDir).sort()) {
    const file = join(libraryDir, set, 'head.css');
    if (!existsSync(file)) continue;
    writeFileSync(file, await hostFonts(readFileSync(file, 'utf8')));
    console.log(`Hosted the fonts of ${set}.`);
  }
}
