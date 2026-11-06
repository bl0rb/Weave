/**
 * Markdown fixes for LLM answers, applied before/while ReactMarkdown parses
 * them (components/chat/message-bubble.tsx):
 *
 * - a single line break the model wrote is kept as a line break (address
 *   lines, "Schritt 1: … / Schritt 2: …") instead of collapsing into one line;
 * - an HTML `<br>` (common inside table cells) becomes a line break instead
 *   of being dropped -- raw HTML is otherwise not rendered at all;
 * - "•"-style bullets become Markdown list items.
 */

interface MdastNode {
  type: string;
  value?: string;
  children?: MdastNode[];
}

const BREAK: MdastNode = { type: 'break' };
const HTML_BREAK = /^<br\s*\/?>$/i;

function withBreaks(node: MdastNode) {
  if (!node.children) return;
  node.children = node.children.flatMap((child): MdastNode[] => {
    if (child.type === 'text' && child.value?.includes('\n')) {
      return child.value.split('\n').flatMap((part, index) => [
        ...(index > 0 ? [{ ...BREAK }] : []),
        ...(part ? [{ type: 'text', value: part }] : []),
      ]);
    }
    if (child.type === 'html' && HTML_BREAK.test((child.value ?? '').trim())) return [{ ...BREAK }];
    withBreaks(child);
    return [child];
  });
}

/** Remark plugin: soft line breaks and inline `<br>` become hard breaks. */
export function remarkChatBreaks() {
  return (tree: MdastNode) => withBreaks(tree);
}

/** "• Punkt" (and ◦ ▪ ‣ ·) at the start of a line becomes "- Punkt", outside code fences. */
export function normalizeAnswerMarkdown(content: string): string {
  let fenced = false;
  return content
    .split('\n')
    .map((line) => {
      if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
      return fenced ? line : line.replace(/^(\s*)[•◦▪‣·]\s+/, '$1- ');
    })
    .join('\n');
}
