'use client';

import { createContext, useContext, useMemo, useState, type CSSProperties, type HTMLAttributes, type ReactNode } from 'react';
import { ShieldAlert } from 'lucide-react';
import { CITATION_RE, citationNumbers, type AnswerSpace } from '@/lib/knowledge-spaces';
import { useI18n } from '@/i18n/provider';
import type { Source } from '@/types/weave-api';

// --- shared hover state ------------------------------------------------------

/** The source (by its `[N]`) a reader points at, within one answer. */
export interface CitationHighlight {
  messageId: string;
  ref: number;
}

interface CitationHighlightState {
  highlight: CitationHighlight | null;
  setHighlight: (highlight: CitationHighlight | null) => void;
  /** Knowledge-space display names by slug (GET /api/collections). */
  spaceNames: ReadonlyMap<string, string>;
}

const HighlightContext = createContext<CitationHighlightState | null>(null);
const NO_NAMES: ReadonlyMap<string, string> = new Map();

/**
 * Shares which source is hovered between an answer's text and its source
 * lists (inline cards and the right-hand panel). Nested providers defer to
 * the outermost one, so a bubble rendered on its own (tests) still works.
 */
export function CitationHighlightProvider({ spaceNames, children }: { spaceNames?: ReadonlyMap<string, string>; children: ReactNode }) {
  const parent = useContext(HighlightContext);
  const [highlight, setHighlight] = useState<CitationHighlight | null>(null);
  const value = useMemo(() => ({ highlight, setHighlight, spaceNames: spaceNames ?? NO_NAMES }), [highlight, spaceNames]);
  return <HighlightContext.Provider value={parent ?? value}>{children}</HighlightContext.Provider>;
}

export function useCitationHighlight(): CitationHighlightState {
  return useContext(HighlightContext) ?? { highlight: null, setHighlight: () => {}, spaceNames: NO_NAMES };
}

/** Hover/focus handlers that mark one source's passages in its answer. */
export function highlightHandlers(state: CitationHighlightState, messageId: string | undefined, ref: number | null | undefined) {
  if (!messageId || typeof ref !== 'number') return {};
  const on = () => state.setHighlight({ messageId, ref });
  const off = () => state.setHighlight(null);
  return { onMouseEnter: on, onMouseLeave: off, onFocus: on, onBlur: off };
}

export function isHighlighted(state: CitationHighlightState, messageId: string | undefined, ref: number | null | undefined): boolean {
  return !!messageId && state.highlight?.messageId === messageId && state.highlight.ref === ref;
}

/** `--space` for an element coloured by its knowledge space (globals.css). */
export function spaceStyle(color: number | undefined): CSSProperties | undefined {
  return color ? ({ '--space': `var(--space-${color})` } as CSSProperties) : undefined;
}

export function spaceName(state: CitationHighlightState, collection: string | null, fallback: string): string {
  return collection ? (state.spaceNames.get(collection) ?? collection) : fallback;
}

// --- answer text: [N] markers and the passages they back --------------------

interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

const INLINE_TAGS = new Set(['a', 'abbr', 'b', 'br', 'code', 'del', 'em', 'i', 'img', 'input', 'kbd', 'mark', 'q', 's', 'small', 'span', 'strong', 'sub', 'sup', 'u']);

const textNode = (value: string): HastNode => ({ type: 'text', value });
const markerNode = (ref: number): HastNode => ({ type: 'element', tagName: 'span', properties: { dataCiteRef: String(ref) }, children: [textNode(String(ref))] });

/** Splits a text node at its known `[N]` markers: text pieces and marker groups. */
function splitMarkers(value: string, refs: ReadonlySet<number>): Array<string | number[]> {
  const pieces: Array<string | number[]> = [];
  let last = 0;
  for (const match of value.matchAll(CITATION_RE)) {
    const known = citationNumbers(match[1]).filter((ref) => refs.has(ref));
    if (known.length === 0) continue;
    if (match.index > last) pieces.push(value.slice(last, match.index));
    pieces.push(known);
    last = match.index + match[0].length;
  }
  if (last < value.length) pieces.push(value.slice(last));
  return pieces;
}

function markersOnly(node: HastNode, refs: ReadonlySet<number>) {
  if (!node.children || node.tagName === 'code' || node.tagName === 'pre') return;
  node.children = node.children.flatMap((child) => {
    if (child.type !== 'text') {
      markersOnly(child, refs);
      return [child];
    }
    return splitMarkers(child.value ?? '', refs).flatMap((piece) => (typeof piece === 'string' ? [textNode(piece)] : piece.map(markerNode)));
  });
}

/**
 * Wraps each run of inline content that ends in a `[N]` marker into a
 * passage span carrying those refs (`data-cite-refs`), and the markers into
 * badges (`data-cite-ref`). A passage runs from the previous marker (or the
 * start of its block) to its marker; block elements are boundaries.
 */
function segment(parent: HastNode, refs: ReadonlySet<number>) {
  if (!parent.children) return;
  const out: HastNode[] = [];
  let run: HastNode[] = [];
  let lastPassage: HastNode | null = null;
  const hasContent = () => run.some((node) => node.type !== 'text' || (node.value ?? '').trim());
  const flush = (cited?: number[]) => {
    if (cited && hasContent()) {
      lastPassage = { type: 'element', tagName: 'span', properties: { dataCiteRefs: cited.join(' ') }, children: run };
      out.push(lastPassage);
    } else {
      // "[1][2]" or "[1] [2]": the second group backs the same passage.
      if (cited && lastPassage) {
        const properties = lastPassage.properties ?? {};
        properties.dataCiteRefs = `${String(properties.dataCiteRefs ?? '')} ${cited.join(' ')}`.trim();
      } else if (hasContent()) lastPassage = null;
      out.push(...run);
    }
    run = [];
  };
  for (const child of parent.children) {
    if (child.type === 'text') {
      for (const piece of splitMarkers(child.value ?? '', refs)) {
        if (typeof piece === 'string') run.push(textNode(piece));
        else {
          flush(piece);
          out.push(...piece.map(markerNode));
        }
      }
    } else if (child.type === 'element' && INLINE_TAGS.has(child.tagName ?? '')) {
      markersOnly(child, refs);
      run.push(child);
    } else {
      flush();
      lastPassage = null;
      if (child.type === 'element' && child.tagName !== 'pre') segment(child, refs);
      out.push(child);
    }
  }
  flush();
  parent.children = out;
}

/** Rehype plugin (after rehype-sanitize): marks the answer's known `[N]` citations. */
export function rehypeCitations(options: { refs: ReadonlySet<number> }) {
  return (tree: HastNode) => {
    if (options.refs.size > 0) segment(tree, options.refs);
  };
}

interface AnswerCitations {
  messageId: string;
  /** Source by its `[N]`. */
  byRef: ReadonlyMap<number, Source>;
  /** Colour slot by `[N]`, only for an answer mixing knowledge spaces. */
  colorByRef: ReadonlyMap<number, number>;
  mixed: boolean;
  /** The bot's `show_sources`: when off, a marker does not name its document. */
  showSources: boolean;
}

export const AnswerCitationsContext = createContext<AnswerCitations | null>(null);

/** ReactMarkdown's `span`: renders the passages and markers `rehypeCitations` made. */
export function CitationSpan(props: HTMLAttributes<HTMLSpanElement> & { node?: unknown }) {
  const rest = { ...props };
  delete rest.node;
  const attributes = rest as Record<string, unknown>;
  const answer = useContext(AnswerCitationsContext);
  const state = useCitationHighlight();
  const passageRefs = typeof attributes['data-cite-refs'] === 'string' ? (attributes['data-cite-refs'] as string).split(' ').map(Number) : null;
  const markerRef = typeof attributes['data-cite-ref'] === 'string' ? Number(attributes['data-cite-ref']) : null;
  if (!answer || (!passageRefs && markerRef === null)) return <span {...rest} />;

  if (markerRef !== null) {
    const source = answer.byRef.get(markerRef);
    return (
      <span
        {...rest}
        className="cite-marker"
        data-active={isHighlighted(state, answer.messageId, markerRef)}
        style={spaceStyle(answer.colorByRef.get(markerRef))}
        title={answer.showSources ? (source?.original_filename ?? undefined) : undefined}
        {...highlightHandlers(state, answer.messageId, markerRef)}
      />
    );
  }
  const refs = passageRefs ?? [];
  const active = state.highlight?.messageId === answer.messageId && refs.includes(state.highlight.ref);
  return (
    <span
      {...rest}
      className="cite-passage"
      data-mixed={answer.mixed}
      data-active={active}
      style={spaceStyle(answer.colorByRef.get(refs[0]))}
    />
  );
}

// --- end of the answer: knowledge spaces and their notices -------------------

/**
 * Below the answer: for an answer mixing knowledge spaces, which colour is
 * which space; then the spaces' notices (e.g. "Nur intern"), each with its
 * space's colour when mixed. Shown even when the bot hides its sources.
 */
export function AnswerSpacesFooter({ spaces }: { spaces: AnswerSpace[] }) {
  const { t } = useI18n();
  const state = useCitationHighlight();
  const mixed = spaces.length > 1;
  const notices = new Map<string, AnswerSpace[]>();
  for (const space of spaces) {
    if (space.notice) notices.set(space.notice, [...(notices.get(space.notice) ?? []), space]);
  }
  if (!mixed && notices.size === 0) return null;
  return (
    <div className="mt-3 flex flex-col gap-2">
      {mixed ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[var(--muted)]" data-testid="answer-spaces">
          <span className="font-semibold uppercase tracking-wide">{t('chat.messageBubble.spacesLabel')}</span>
          {spaces.map((space) => (
            <span key={space.collection ?? ''} className="inline-flex items-center gap-1.5" style={spaceStyle(space.color)}>
              <span className="space-dot" aria-hidden="true" />
              {spaceName(state, space.collection, t('chat.sources.noCollection'))}
            </span>
          ))}
        </div>
      ) : null}
      {notices.size > 0 ? (
        <div
          role="note"
          aria-label={t('chat.messageBubble.noticeLabel')}
          data-testid="answer-notices"
          className="flex items-start gap-2 rounded-[var(--radius-control)] bg-[var(--warn-bg)] px-3 py-1.5 text-xs font-semibold text-[var(--warn)]"
        >
          <ShieldAlert className="mt-px h-3.5 w-3.5 flex-none" aria-hidden="true" />
          <span>
            {[...notices].map(([notice, noticeSpaces], index) => (
              <span key={notice}>
                {index > 0 ? ' · ' : null}
                {mixed
                  ? noticeSpaces.map((space) => (
                      <span key={space.collection ?? ''} className="space-dot mr-1 align-middle" style={spaceStyle(space.color)} aria-hidden="true" />
                    ))
                  : null}
                {notice}
              </span>
            ))}
          </span>
        </div>
      ) : null}
    </div>
  );
}
