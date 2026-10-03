/**
 * Render a Shellui block document in one of the five official themes.
 * Placeholders such as {{ company_name }} are left as text.
 *
 * Theme layout, palette, type, and fonts are adapted from the MIT-licensed
 * React Email demos (see renderer/themes/LICENSE). Sample copy is not used.
 *
 * stdin: JSON { document, palette, theme, spec }
 * stdout: JSON { html, text }
 */
import React from 'react';
import { render } from '@react-email/render';
import {
  Body,
  Button,
  Container,
  Font,
  Head,
  Heading,
  Html,
  Preview,
  Section,
  Text,
} from '@react-email/components';

function Email({ document, spec, colors }) {
  const blocks = document.blocks || [];
  const align = spec.align || 'left';
  const layout = spec.layout || 'card';
  const outline = spec.button_style === 'outline';
  const buttonBg = outline && layout === 'serif' ? 'transparent' : colors.primary;
  const buttonFg = outline && layout === 'serif' ? colors.foreground : colors.primaryForeground;
  const buttonBorder =
    outline && layout === 'serif'
      ? `1px solid ${colors.foreground}`
      : outline
        ? `1px solid ${colors.border}`
        : '0';
  const cardBorder = layout === 'card' || layout === 'inset' ? `1px solid ${colors.border}` : '0';
  const innerBg = layout === 'inset' ? colors.inner : colors.background;
  const fonts = spec.fonts || [];
  return React.createElement(
    Html,
    null,
    React.createElement(
      Head,
      null,
      ...fonts.map((font, index) =>
        React.createElement(Font, {
          key: index,
          fontFamily: font.family,
          fallbackFontFamily: String(font.fallback || 'Arial, sans-serif')
            .split(',')
            .map((part) => part.trim().replace(/^'|'$/g, '')),
          webFont: { url: font.url, format: font.format || 'woff2' },
          fontWeight: font.weight || 400,
          fontStyle: 'normal',
        }),
      ),
    ),
    React.createElement(
      Body,
      {
        style: {
          backgroundColor: colors.muted,
          margin: 0,
          fontFamily: spec.font,
          color: colors.foreground,
        },
      },
      React.createElement(Preview, null, document.preview || ''),
      React.createElement(
        Container,
        {
          style: {
            maxWidth: spec.max_width || '640px',
            margin: '32px auto',
            backgroundColor: colors.background,
            border: cardBorder,
            borderRadius: spec.card_radius || '0',
            boxShadow: spec.shadow && spec.shadow !== 'none' ? spec.shadow : undefined,
          },
        },
        layout === 'studio'
          ? React.createElement(Section, {
              style: { backgroundColor: colors.foreground, height: '8px', fontSize: '0', lineHeight: '0' },
            })
          : null,
        React.createElement(
          Section,
          {
            style: {
              backgroundColor: innerBg,
              padding: layout === 'inset' ? '28px 24px' : '40px 32px',
              textAlign: align,
              borderRadius: spec.card_radius || '0',
            },
          },
          ...blocks.map((block, index) => {
            if (block.type === 'heading') {
              return React.createElement(
                Heading,
                {
                  key: index,
                  as: 'h1',
                  style: {
                    fontFamily: spec.heading_font,
                    fontSize: spec.heading_size,
                    fontWeight: spec.heading_weight,
                    lineHeight: '1.2',
                    letterSpacing: '-0.02em',
                    textTransform: spec.heading_transform || 'none',
                    color: colors.foreground,
                    margin: '0 0 16px',
                  },
                },
                block.text || '',
              );
            }
            if (block.type === 'button') {
              return React.createElement(
                Section,
                { key: index, style: { margin: '8px 0 4px', textAlign: align } },
                React.createElement(
                  Button,
                  {
                    href: block.href || '',
                    style: {
                      backgroundColor: buttonBg,
                      color: buttonFg,
                      borderRadius: spec.button_radius || '0',
                      padding: spec.button_pad || '12px 20px',
                      fontFamily: spec.font,
                      fontSize: '15px',
                      fontWeight: 500,
                      border: buttonBorder,
                      boxShadow: outline && spec.shadow && spec.shadow !== 'none' ? spec.shadow : undefined,
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
                  fontFamily: spec.font,
                  color: block.type === 'footer' ? colors.mutedForeground : colors.body,
                  fontSize: block.type === 'footer' ? '12px' : spec.text_size,
                  lineHeight: '1.5',
                  margin: '0 0 16px',
                },
              },
              block.text || '',
            );
          }),
        ),
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
const element = React.createElement(Email, {
  document: document || {},
  spec: raw.spec || {},
  colors: raw.palette || {},
});
const html = await render(element);
const text = await render(element, { plainText: true });
process.stdout.write(JSON.stringify({ html, text }));
