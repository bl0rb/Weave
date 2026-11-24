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

// A line that is only a sources heading: "Quellen", "**Quellen:**", "### Sources" …
const SOURCES_HEADING =
  /^\s*(?:#{1,6}\s+)?(?:\*\*|__)?\s*(Quellen(?:angaben)?|Belege|Referenzen|Sources|References)\s*:?\s*(?:\*\*|__)?\s*:?\s*$/i;

export interface AnswerSourcesSplit {
  body: string;
  /** The heading as the model wrote it ("Quellen"), or null without a sources section. */
  sourcesLabel: string | null;
  sources: string;
}

/**
 * Splits a source list the model appended to its answer ("Quellen" heading
 * plus everything after it) off the answer body, so the chat can show it
 * collapsed. Only the last such heading outside code fences counts, and only
 * when there is answer text before it and list text after it.
 */
export function splitAnswerSources(content: string): AnswerSourcesSplit {
  const lines = content.split('\n');
  let fenced = false;
  let headingIndex = -1;
  let label: string | null = null;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    if (fenced) continue;
    const match = SOURCES_HEADING.exec(line);
    if (match) {
      headingIndex = index;
      label = match[1];
    }
  }
  if (headingIndex < 0 || label === null) return { body: content, sourcesLabel: null, sources: '' };
  const body = lines.slice(0, headingIndex).join('\n').trimEnd();
  const sources = lines.slice(headingIndex + 1).join('\n').trim();
  if (!body || !sources) return { body: content, sourcesLabel: null, sources: '' };
  return { body, sourcesLabel: label, sources };
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
