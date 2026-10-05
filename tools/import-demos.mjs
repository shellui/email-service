/**
 * Turn the vendored React Email demos into library seed files.
 *
 *   node tools/import-demos.mjs
 *
 * Writes renderer/library/<set>/<name>.json and renderer/library/<set>/head.css,
 * with the fonts that CSS uses copied into static/library/fonts/.
 * Rerun by hand after updating renderer/demos/, then commit the output.
 */
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { jsx } from 'react/jsx-runtime';
import { render } from 'react-email';
import { composeDocument, compactDocument, createEditor, parseHtml } from '../renderer/editor.mjs';
import { hostFonts } from './host-fonts.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const demosDir = join(root, 'renderer', 'demos');
const libraryDir = join(root, 'renderer', 'library');
const assetsDir = join(root, 'static', 'library');

const SETS = [
  { folder: '01-Barebone', set: 'barebone' },
  { folder: '02-Matte', set: 'matte' },
  { folder: '03-Protocol', set: 'protocol' },
  { folder: '04-Arcane', set: 'arcane' },
  { folder: '05-Studio', set: 'studio' },
];

const LIFECYCLE = [
  'activation',
  'feature-announcement',
  'password-reset',
  'product-update',
  'subscription-confirmation',
  'subscription-update',
  'text-only',
  'welcome',
];
const COMMERCE = [
  'abandoned-cart',
  'activation',
  'newsletter',
  'order-confirmation',
  'order-shipping',
  'password-reset',
  'promo',
  'welcome',
];
export const EXPECTED = {
  barebone: LIFECYCLE,
  matte: LIFECYCLE,
  protocol: LIFECYCLE,
  arcane: COMMERCE,
  studio: COMMERCE,
};

const NAMES = {
  activation: 'Activation',
  'feature-announcement': 'Feature announcement',
  'password-reset': 'Password reset',
  'product-update': 'Product update',
  'subscription-confirmation': 'Subscription confirmation',
  'subscription-update': 'Subscription update',
  'text-only': 'Text only',
  welcome: 'Welcome',
  'abandoned-cart': 'Abandoned cart',
  newsletter: 'Newsletter',
  'order-confirmation': 'Order confirmation',
  'order-shipping': 'Order shipping',
  promo: 'Promo',
};

const SUBJECTS = {
  activation: 'Confirm your email for {{ company_name }}',
  'feature-announcement': 'New in {{ company_name }}',
  'password-reset': 'Reset your {{ company_name }} password',
  'product-update': 'What changed at {{ company_name }}',
  'subscription-confirmation': 'Your {{ company_name }} subscription is active',
  'subscription-update': 'Your {{ company_name }} subscription changed',
  'text-only': 'A note from {{ company_name }}',
  welcome: 'Welcome to {{ company_name }}',
  'abandoned-cart': 'You left something in your cart',
  newsletter: 'News from {{ company_name }}',
  'order-confirmation': 'Your {{ company_name }} order is confirmed',
  'order-shipping': 'Your {{ company_name }} order is on its way',
  promo: 'An offer from {{ company_name }}',
};

const ASSETS_HOST = 'shellui-assets.invalid';
const ASSETS_TOKEN = '{{ system.assets_url }}';
const PLACEHOLDER_PROPS = { companyName: '{{ company_name }}', url: '{{ action_url }}' };

function withoutDashes(value) {
  return value.replace(/\s*[\u2013\u2014]\s*/g, ' - ');
}

function withAssetToken(html) {
  return html.replaceAll(`https://${ASSETS_HOST}/static/`, `${ASSETS_TOKEN}/`);
}

function checkAssets(html, key) {
  for (const match of html.matchAll(/\{\{ system\.assets_url \}\}\/([a-zA-Z0-9_./-]+)/g)) {
    if (!existsSync(join(assetsDir, match[1]))) {
      throw new Error(`${key}: missing asset static/library/${match[1]}`);
    }
  }
}

/**
 * Image nodes are blocks, so a run of linked icons becomes one table row to stay side by side.
 * Unsubscribe links point at the per-recipient link the service fills in.
 */
function prepareBody(html) {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
  for (const link of doc.body.querySelectorAll('a[href]')) {
    if (/unsubscribe/i.test(link.textContent || '')) link.setAttribute('href', '{{ system.unsubscribe_url }}');
  }
  for (const element of [...doc.body.querySelectorAll('*')]) {
    const links = [...element.children];
    const iconRun =
      links.length > 1 &&
      links.every((link) => link.tagName === 'A' && link.children.length === 1 && link.firstElementChild.tagName === 'IMG') &&
      ![...element.childNodes].some((child) => child.nodeType === 3 && child.textContent.trim());
    if (!iconRun) continue;
    const table = doc.createElement('table');
    for (const [name, value] of Object.entries({ align: 'center', role: 'presentation', border: '0', cellpadding: '0', cellspacing: '0' })) {
      table.setAttribute(name, value);
    }
    const row = doc.createElement('tr');
    for (const link of links) {
      const cell = doc.createElement('td');
      const padding = (link.getAttribute('style') || '')
        .split(';')
        .filter((declaration) => declaration.trim().startsWith('padding'));
      cell.setAttribute('style', [...padding, 'vertical-align:middle'].join(';'));
      cell.appendChild(link);
      row.appendChild(cell);
    }
    const tbody = doc.createElement('tbody');
    tbody.appendChild(row);
    table.appendChild(tbody);
    element.replaceChildren(table);
  }
  return doc.body.innerHTML;
}

/** Split CSS into top-level rules so a set's head keeps one copy of each. */
function cssRules(css) {
  const rules = [];
  let depth = 0;
  let start = 0;
  let parens = 0;
  let quote = '';
  for (let index = 0; index < css.length; index += 1) {
    const char = css[index];
    if (quote) {
      if (char === quote) quote = '';
      continue;
    }
    if (char === '"' || char === "'") quote = char;
    if (char === '(') parens += 1;
    if (char === ')') parens -= 1;
    if (char === '{') depth += 1;
    if ((char === '}' && --depth === 0) || (char === ';' && depth === 0 && parens === 0)) {
      rules.push(css.slice(start, index + 1).trim());
      start = index + 1;
    }
  }
  rules.push(css.slice(start).trim());
  return rules.filter(Boolean);
}

/** `@import` only works before every other rule. */
function headCss(rules) {
  const imports = rules.filter((rule) => rule.startsWith('@import'));
  return [...imports, ...rules.filter((rule) => !rule.startsWith('@import'))].join('\n');
}

async function loadDemo(file, workDir) {
  const outfile = join(workDir, `${file.replace(/[^a-z0-9]/gi, '_')}.mjs`);
  await build({
    entryPoints: [file],
    bundle: true,
    format: 'esm',
    platform: 'node',
    jsx: 'automatic',
    outfile,
    packages: 'external',
    logLevel: 'error',
  });
  const mod = await import(pathToFileURL(outfile).href);
  return mod.default;
}

export async function importDemos() {
  process.env.VERCEL_URL = ASSETS_HOST;
  mkdirSync(join(root, 'node_modules', '.cache'), { recursive: true });
  const workDir = mkdtempSync(join(root, 'node_modules', '.cache', 'shellui-demos-'));
  const written = [];
  try {
    for (const { folder, set } of SETS) {
      const setDir = join(libraryDir, set);
      rmSync(setDir, { recursive: true, force: true });
      mkdirSync(setDir, { recursive: true });
      const headRules = new Set();
      const files = readdirSync(join(demosDir, folder))
        .filter((name) => name.endsWith('.tsx') && !name.endsWith('-fonts.tsx'))
        .sort();
      for (const fileName of files) {
        const name = fileName.replace(/\.tsx$/, '');
        const key = `${set}.${name}`;
        const Component = await loadDemo(join(demosDir, folder, fileName), workDir);
        const props = { ...(Component.PreviewProps ?? {}), ...PLACEHOLDER_PROPS };
        const parsed = parseHtml(await render(jsx(Component, props)));
        for (const rule of cssRules(parsed.head)) headRules.add(rule);
        const body = prepareBody(withAssetToken(withoutDashes(parsed.body)));
        checkAssets(body, key);
        const editor = createEditor({ content: body });
        const document = compactDocument(editor.getJSON(), editor.schema);
        editor.destroy();
        const preheader = withoutDashes(parsed.preheader);
        await composeDocument({ document, preheader });
        const seed = { key, set, name: NAMES[name] ?? name, subject: SUBJECTS[name] ?? '', preheader, document };
        writeFileSync(join(setDir, `${name}.json`), `${JSON.stringify(seed)}\n`);
        written.push(key);
      }
      writeFileSync(join(setDir, 'head.css'), `${await hostFonts(headCss([...headRules]))}\n`);
    }
  } finally {
    rmSync(workDir, { recursive: true, force: true });
  }
  return written;
}

export function checkKeys(keys) {
  const expected = Object.entries(EXPECTED).flatMap(([set, names]) => names.map((name) => `${set}.${name}`));
  const missing = expected.filter((key) => !keys.includes(key));
  const extra = keys.filter((key) => !expected.includes(key));
  if (missing.length || extra.length) {
    throw new Error(`Library keys differ. Missing: ${missing.join(', ') || 'none'}. Extra: ${extra.join(', ') || 'none'}.`);
  }
  return expected.length;
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const keys = await importDemos();
  const count = checkKeys(keys);
  console.log(`Imported ${count} library templates into renderer/library/.`);
}
