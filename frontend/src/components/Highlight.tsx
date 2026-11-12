/**
 * 安全高亮渲染：后端已对文本做 HTML 转义，只有命中词被 `<mark>` 包裹。
 * 这里再做一次白名单清洗（仅允许 <mark>），并重新转义所有文本节点，
 * 避免任何未转义内容被当作 HTML 注入（架构 §8）。
 */
import { useMemo } from 'react';

const ALLOWED_TAGS = new Set(['MARK']);

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** 把任意 HTML 片段清洗为「纯文本 + <mark>」结构。 */
export function sanitizeHighlight(input: string): string {
  if (!input) {
    return '';
  }
  if (typeof DOMParser === 'undefined') {
    return escapeHtml(input);
  }
  const parsed = new DOMParser().parseFromString(`<div>${input}</div>`, 'text/html');
  const root = parsed.body.firstElementChild;
  if (!root) {
    return '';
  }

  const walk = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) {
      return escapeHtml(node.textContent ?? '');
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return '';
    }
    const element = node as HTMLElement;
    const inner = Array.from(element.childNodes)
      .map((child) => walk(child))
      .join('');
    if (ALLOWED_TAGS.has(element.tagName)) {
      return `<mark>${inner}</mark>`;
    }
    return inner;
  };

  return Array.from(root.childNodes)
    .map((child) => walk(child))
    .join('');
}

export interface HighlightProps {
  /** 后端返回的已转义高亮片段（含 <mark>）。 */
  html: string;
  className?: string;
}

/** 高亮文本组件。 */
export default function Highlight({ html, className }: HighlightProps): JSX.Element {
  const safeHtml = useMemo(() => sanitizeHighlight(html), [html]);
  return <span className={className} dangerouslySetInnerHTML={{ __html: safeHtml }} />;
}
