/**
 * Render a Shellui block document with one of the five Tailwind templates in email.mjs.
 * Placeholders such as {{ company_name }} are left as text.
 *
 * stdin: JSON { document, palette, theme, spec }
 * stdout: JSON { html, text }
 */
import { render } from '@react-email/render';
import { DEFAULT_TEMPLATE, createEmail } from './email.mjs';

const chunks = [];
for await (const chunk of process.stdin) {
  chunks.push(chunk);
}
const raw = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
const document = raw && Object.prototype.hasOwnProperty.call(raw, 'document') ? raw.document : raw;
const element = createEmail({
  template: raw.theme || DEFAULT_TEMPLATE,
  spec: raw.spec || {},
  colors: raw.palette || {},
  document: document || {},
});
const html = await render(element);
// Uppercased headings would turn {{ name }} into {{ NAME }}, which no longer substitutes.
const text = await render(element, {
  plainText: true,
  htmlToTextOptions: {
    selectors: [{ selector: 'h1', options: { uppercase: false } }],
  },
});
process.stdout.write(JSON.stringify({ html, text }));
