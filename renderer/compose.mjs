/**
 * Compose React Email editor documents into email HTML and plain text.
 * Placeholders such as {{ company_name }} are left as text.
 *
 * stdin: JSON { items: [{ document, head, preheader }] }
 * stdout: JSON { items: [{ html, text }] }
 */
import { composeDocument } from './editor.mjs';

const chunks = [];
for await (const chunk of process.stdin) {
  chunks.push(chunk);
}
const raw = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
const items = [];
for (const item of raw.items ?? []) {
  items.push(
    await composeDocument({
      document: item.document,
      head: item.head || '',
      preheader: item.preheader || '',
    }),
  );
}
process.stdout.write(JSON.stringify({ items }));
