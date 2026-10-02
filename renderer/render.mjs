/**
 * Render a Shellui email document with React Email.
 * Placeholders such as {{ company_name }} are left as text.
 *
 * stdin: JSON document { preview, blocks: [{type, text, href?}] }
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

function Email({ document }) {
  const blocks = document.blocks || [];
  return React.createElement(
    Html,
    null,
    React.createElement(Head, null),
    React.createElement(
      Body,
      { style: { backgroundColor: '#f6f4ef', margin: 0, fontFamily: 'Georgia, serif' } },
      React.createElement(Preview, null, document.preview || ''),
      React.createElement(
        Container,
        {
          style: {
            maxWidth: '560px',
            margin: '32px auto',
            backgroundColor: '#ffffff',
            border: '1px solid #e7e0d4',
            borderRadius: '12px',
            padding: '8px 0 12px',
          },
        },
        React.createElement(
          Text,
          {
            style: {
              color: '#8a6a12',
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
                style: { color: '#1a1408', fontSize: '26px', padding: '8px 32px', fontWeight: 700 },
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
                    backgroundColor: '#e3a512',
                    color: '#1a1408',
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
                color: block.type === 'footer' ? '#6b645b' : '#3f3a32',
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
const document = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
const element = React.createElement(Email, { document });
const html = await render(element);
const text = await render(element, { plainText: true });
process.stdout.write(JSON.stringify({ html, text }));
