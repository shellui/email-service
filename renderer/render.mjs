/**
 * Render a Shellui email document with React Email.
 * Placeholders such as {{ company_name }} are left as text.
 *
 * stdin: JSON { document: { preview, blocks }, palette } or a bare document.
 * palette colors are #RRGGBB. Anything else falls back to the Shellui palette.
 * stdout: JSON { html, text }
 */
import React from 'react';
import { render } from '@react-email/render';
import {
  Body,
  Button,
  Container,
  Head,
  Heading,
  Html,
  Preview,
  Section,
  Text,
} from '@react-email/components';

const DEFAULT_PALETTE = {
  background: '#ffffff',
  foreground: '#1a1408',
  muted: '#f6f4ef',
  mutedForeground: '#6b645b',
  primary: '#e3a512',
  primaryForeground: '#1a1408',
  border: '#e7e0d4',
};

function color(value, fallback) {
  return typeof value === 'string' && /^#[0-9A-Fa-f]{6}$/.test(value) ? value : fallback;
}

function paletteColors(raw) {
  const source = raw && typeof raw === 'object' ? raw : {};
  const colors = {};
  for (const key of Object.keys(DEFAULT_PALETTE)) {
    colors[key] = color(source[key], DEFAULT_PALETTE[key]);
  }
  return colors;
}

function Email({ document, palette }) {
  const colors = paletteColors(palette);
  const blocks = document.blocks || [];
  return React.createElement(
    Html,
    null,
    React.createElement(Head, null),
    React.createElement(
      Body,
      { style: { backgroundColor: colors.muted, margin: 0, fontFamily: 'Georgia, serif', color: colors.foreground } },
      React.createElement(Preview, null, document.preview || ''),
      React.createElement(
        Container,
        {
          style: {
            maxWidth: '560px',
            margin: '32px auto',
            backgroundColor: colors.background,
            border: `1px solid ${colors.border}`,
            borderRadius: '12px',
            padding: '8px 0 12px',
          },
        },
        React.createElement(
          Text,
          {
            style: {
              color: colors.primary,
              fontSize: '14px',
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              padding: '20px 32px 0',
            },
          },
          'Shellui',
        ),
        ...blocks.map((block, index) => {
          if (block.type === 'heading') {
            return React.createElement(
              Heading,
              {
                key: index,
                style: { color: colors.foreground, fontSize: '26px', padding: '8px 32px', fontWeight: 700 },
              },
              block.text || '',
            );
          }
          if (block.type === 'button') {
            return React.createElement(
              Section,
              { key: index, style: { padding: '8px 32px 20px' } },
              React.createElement(
                Button,
                {
                  href: block.href || '',
                  style: {
                    backgroundColor: colors.primary,
                    color: colors.primaryForeground,
                    borderRadius: '8px',
                    padding: '12px 22px',
                    fontWeight: 700,
                  },
                },
                block.text || '',
              ),
            );
          }
          return React.createElement(
            Text,
            {
              key: index,
              style: {
                color: block.type === 'footer' ? colors.mutedForeground : colors.foreground,
                fontSize: block.type === 'footer' ? '12px' : '16px',
                lineHeight: '1.55',
                padding: '0 32px',
              },
            },
            block.text || '',
          );
        }),
      ),
    ),
  );
}

const chunks = [];
for await (const chunk of process.stdin) {
  chunks.push(chunk);
}
const raw = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
const document = raw && Object.prototype.hasOwnProperty.call(raw, 'document') ? raw.document : raw;
const element = React.createElement(Email, { document: document || {}, palette: raw.palette });
const html = await render(element);
const text = await render(element, { plainText: true });
process.stdout.write(JSON.stringify({ html, text }));
