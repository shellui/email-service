/**
 * Headless React Email editor: the same node set the admin editor uses, so a
 * document composes to the same HTML on both sides.
 */
import { Window } from 'happy-dom';

const window = new Window();
for (const key of ['window', 'document', 'navigator', 'DOMParser', 'Node', 'HTMLElement', 'Element', 'getComputedStyle', 'MutationObserver']) {
  if (key === 'window' || !(key in globalThis)) {
    Object.defineProperty(globalThis, key, {
      value: key === 'window' ? window : window[key],
      configurable: true,
      writable: true,
    });
  }
}

const { Editor, Extension } = await import('@tiptap/core');
const { Container: BaseContainer, StarterKit } = await import('@react-email/editor/extensions');
const { EmailNode, composeReactEmail } = await import('@react-email/editor/core');
const { Body, Container: EmailContainer, Head, Html, Img, Link, Preview } = await import('react-email');
const { jsx, jsxs } = await import('react/jsx-runtime');
const { toPlainText } = await import('@react-email/render');

export function cssToJs(css) {
  const style = {};
  for (const declaration of String(css || '').split(';')) {
    const colon = declaration.indexOf(':');
    if (colon < 1) continue;
    const name = declaration.slice(0, colon).trim();
    const value = declaration.slice(colon + 1).trim();
    if (!name || !value) continue;
    style[name.startsWith('--') ? name : name.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
  }
  return style;
}

/** React Email images are blocks, so auto margins place them. */
function imageAlignStyle(alignment) {
  if (alignment === 'center') return { marginLeft: 'auto', marginRight: 'auto' };
  if (alignment === 'right') return { marginLeft: 'auto', marginRight: 0 };
  return {};
}

const Image = EmailNode.create({
  name: 'image',
  group: 'block',
  atom: true,
  draggable: true,
  addAttributes() {
    return {
      src: { default: '' },
      alt: { default: '' },
      width: { default: 'auto' },
      height: { default: 'auto' },
      href: { default: null },
    };
  },
  parseHTML() {
    return [
      {
        tag: 'img[src]',
        getAttrs: (element) => ({ href: element.closest('a[href]')?.getAttribute('href') ?? null }),
      },
    ];
  },
  renderHTML({ HTMLAttributes }) {
    return ['img', HTMLAttributes];
  },
  renderToReactEmail({ node, style }) {
    if (!node.attrs?.src) return null;
    const img = jsx(Img, {
      alt: node.attrs?.alt ?? '',
      className: node.attrs?.class || undefined,
      height: node.attrs?.height === 'auto' ? undefined : node.attrs?.height,
      src: node.attrs.src,
      style: { ...style, ...cssToJs(node.attrs?.style), ...imageAlignStyle(node.attrs?.alignment) },
      width: node.attrs?.width === 'auto' ? undefined : node.attrs?.width,
    });
    return node.attrs?.href ? jsx(Link, { href: node.attrs.href, children: img }) : img;
  },
});

// The stock container drops its inline style, which carries the design's max width.
const Container = BaseContainer.extend({
  addAttributes() {
    return {
      style: { default: '', parseHTML: (element) => element.getAttribute('style') || '' },
      class: { default: '', parseHTML: (element) => element.getAttribute('class') || '' },
    };
  },
  renderToReactEmail({ children, node }) {
    return jsx(EmailContainer, {
      className: node.attrs?.class || undefined,
      style: cssToJs(node.attrs?.style),
      children,
    });
  },
});

function Document({ children, previewText, head }) {
  return jsxs(Html, {
    children: [
      jsxs(Head, {
        children: [
          jsx('meta', { content: 'width=device-width', name: 'viewport' }),
          jsx('meta', { name: 'x-apple-disable-message-reformatting' }),
          head ? jsx('style', { dangerouslySetInnerHTML: { __html: head } }) : null,
        ],
      }),
      jsxs(Body, {
        style: { margin: 0 },
        children: [previewText ? jsx(Preview, { children: previewText }) : null, children],
      }),
    ],
  });
}

function serializer(head) {
  return Extension.create({
    name: 'shelluiSerializer',
    addOptions() {
      return {
        serializerPlugin: {
          getNodeStyles: () => ({}),
          BaseTemplate: (props) => Document({ ...props, head }),
        },
      };
    },
  });
}

// Translations match blocks by this id. It is stored, never rendered.
const TextId = Extension.create({
  name: 'shelluiTextId',
  addGlobalAttributes() {
    return [
      {
        types: ['paragraph', 'heading', 'button', 'codeBlock'],
        attributes: { textId: { default: null, rendered: false, keepOnSplit: false } },
      },
    ];
  },
});

export function extensions(head = '') {
  return [StarterKit.configure({ Container: false }), Container, Image, TextId, serializer(head)];
}

export function createEditor({ content, head = '' }) {
  return new Editor({ element: null, extensions: extensions(head), content });
}

/** Drop attributes that hold their schema default so stored documents stay small. */
export function compactDocument(node, schema) {
  const out = { type: node.type };
  const spec = (schema.nodes[node.type] ?? schema.marks[node.type])?.spec.attrs ?? {};
  if (node.attrs) {
    const attrs = Object.fromEntries(
      Object.entries(node.attrs).filter(([name, value]) => {
        const fallback = spec[name]?.default ?? null;
        return value !== fallback && !(value === '' && fallback === null);
      }),
    );
    if (Object.keys(attrs).length) out.attrs = attrs;
  }
  if (node.marks?.length) out.marks = node.marks.map((mark) => compactDocument(mark, schema));
  if (typeof node.text === 'string') out.text = node.text;
  if (node.content?.length) out.content = node.content.map((child) => compactDocument(child, schema));
  return out;
}

// Uppercased headings would turn {{ name }} into {{ NAME }}, which no longer substitutes.
const TEXT_SELECTORS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'].map((selector) => ({
  selector,
  options: { uppercase: false },
}));

export async function composeDocument({ document, head = '', preheader = '' }) {
  const editor = createEditor({ content: document, head });
  try {
    const { unformattedHtml } = await composeReactEmail({ editor, preview: preheader });
    return { html: unformattedHtml, text: toPlainText(unformattedHtml, { selectors: TEXT_SELECTORS }) };
  } finally {
    editor.destroy();
  }
}

export function parseHtml(html) {
  const parsed = new window.DOMParser().parseFromString(html, 'text/html');
  const head = [...parsed.querySelectorAll('head style')].map((style) => style.textContent).join('\n');
  const previewNode = parsed.querySelector('[data-skip-in-text]');
  const preheader = previewNode?.firstChild?.textContent?.trim() ?? '';
  previewNode?.remove();
  return { head, preheader, body: parsed.body.innerHTML };
}
