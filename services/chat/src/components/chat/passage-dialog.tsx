'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ImgHTMLAttributes, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import { ExternalLink, TextQuote, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/i18n/provider';
import { toProxiedImageUrl } from '@/lib/portal-artifact-url';
import type { PassageChunk, PassageResponse, Source } from '@/types/weave-api';

/**
 * „Textstelle öffnen“: the cited passage behind a source — the chunk the
 * answer drew on plus its neighbours, loaded from GET /api/sources/passage
 * (Weave-API's GET /v1/sources/passage, same read authority as the chat's
 * own search). Opened from a `[N]` marker (citations.tsx) or the button on
 * a source card / sources panel entry; all of them go through
 * `PassageDialogProvider` below, which chat-app.tsx mounts once. Without a
 * provider (isolated component tests) no passage controls render at all.
 *
 * Only for a real document chunk: knowledge-memory sources (`memory_kind`)
 * have no passage, and a `document_id` that is not a UUID (an n8n flow's own
 * id) can never resolve upstream, so neither offers the control.
 */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** Neighbouring chunks shown on each side of the cited one (Weave-API: 0..3). */
const CONTEXT_CHUNKS = 1;

export function canOpenPassage(source: Source | null | undefined): source is Source {
  return (
    !!source &&
    !source.memory_kind &&
    typeof source.document_id === 'string' &&
    UUID_RE.test(source.document_id) &&
    typeof source.chunk_id === 'number' &&
    Number.isInteger(source.chunk_id) &&
    source.chunk_id >= 0
  );
}

/**
 * The original's address with a text fragment (`#:~:text=`) for the start of
 * the cited chunk, so a browser that supports it scrolls to and highlights
 * the passage on the web page.
 *
 * A text directive matches only whole words and never across block
 * elements, while Weave-Knowledge's chunker (`_assemble_chunks`) joins
 * blocks with a blank line and may open a chunk with an overlap cut from
 * the previous chunk's tail by character count. So the words come from one
 * line of one block: the overlap is dropped (verified against `previous`,
 * the preceding chunk's text), leading headings, tables, code and comments
 * are skipped, and list/quote markers and markdown syntax are removed (the
 * page shows rendered text). `previous` is `''` when there is no preceding
 * chunk and `null` when it is unknown — then the first word of the chunk's
 * first block may be a cut-off fragment and is dropped. `-` must be escaped
 * too, it is the fragment syntax's own separator.
 */
export function withTextFragment(url: string, text: string, previous: string | null = '', maxWords = 8): string {
  const words = fragmentWords(text, previous).slice(0, maxWords);
  if (words.length === 0) return url;
  const fragment = `:~:text=${encodeURIComponent(words.join(' ')).replace(/-/g, '%2D')}`;
  return url.includes('#') ? `${url}${fragment}` : `${url}#${fragment}`;
}

const BLOCK_SEP = '\n\n';
const HEADING_RE = /^#{1,6}[ \t]/;
const SKIPPED_BLOCK_RE = /^(?:\||```|~~~|<!--)/;
const LINE_MARKER_RE = /^(?:[-*+]|\d+[.)]|>)[ \t]+/;

function plainWords(line: string): string[] {
  let rest = line.trim();
  for (let prev = ''; prev !== rest; ) {
    prev = rest;
    rest = rest.replace(LINE_MARKER_RE, '');
  }
  return rest
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[#*_`>|~[\]]/g, ' ')
    .split(/\s+/)
    .filter(Boolean);
}

function fragmentWords(text: string, previous: string | null): string[] {
  let body = text.trim();
  let overlapRemoved = false;
  if (previous) {
    // The overlap is a prefix that ends where a block starts and that the
    // previous chunk ends with; take the longest such prefix.
    for (let at = body.lastIndexOf(BLOCK_SEP); at > 0; at = body.lastIndexOf(BLOCK_SEP, at - 1)) {
      if (previous.endsWith(body.slice(0, at))) {
        body = body.slice(at + BLOCK_SEP.length);
        overlapRemoved = true;
        break;
      }
    }
  }
  const blocks = body.split(/\n[ \t]*\n/).map((block) => block.trim()).filter(Boolean);
  const pick = (index: number): string[] => {
    const line = blocks[index].split('\n').find((candidate) => plainWords(candidate).length > 0) ?? '';
    const words = plainWords(line);
    // Unknown predecessor: the chunk may open with an overlap cut mid-word.
    return previous === null && !overlapRemoved && index === 0 && words.length > 1 ? words.slice(1) : words;
  };
  const index = blocks.findIndex((block) => !HEADING_RE.test(block) && !SKIPPED_BLOCK_RE.test(block));
  if (index >= 0) return pick(index);
  // Only headings (or tables/code): a heading line is still one block.
  const heading = blocks.findIndex((block) => HEADING_RE.test(block));
  return heading >= 0 ? pick(heading) : [];
}

const isWebLink = (value: string | null | undefined): value is string => typeof value === 'string' && /^https?:\/\//i.test(value);

/** The external link for a loaded passage: http(s) only; a website import
 * points at the cited text itself. */
export function passageLink(passage: PassageResponse, target: PassageChunk | undefined): string | null {
  if (!isWebLink(passage.source_url)) return null;
  if (passage.source_kind !== 'website' || !target) return passage.source_url;
  return withTextFragment(passage.source_url, target.text, previousChunkText(passage, target));
}

/** The text of the chunk right before `target` (`''` for the document's
 * first chunk, `null` when it is not part of the loaded passage). */
function previousChunkText(passage: PassageResponse, target: PassageChunk): string | null {
  if (target.chunk_index === 0) return '';
  const position = passage.chunks.indexOf(target);
  const before = position > 0 ? passage.chunks[position - 1] : undefined;
  if (before && target.chunk_index !== null && before.chunk_index === target.chunk_index - 1) return before.text;
  return null;
}

// --- provider ---------------------------------------------------------------

type OpenPassage = (source: Source) => void;
const PassageDialogContext = createContext<OpenPassage | null>(null);

/** Opens the passage dialog for a source, or `null` without a provider. */
export function usePassageDialog(): OpenPassage | null {
  return useContext(PassageDialogContext);
}

/** Holds the one open passage dialog; nested providers defer to the outermost. */
export function PassageDialogProvider({ children }: { children: ReactNode }) {
  const parent = useContext(PassageDialogContext);
  const [source, setSource] = useState<Source | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const open = useCallback<OpenPassage>((next) => {
    if (!canOpenPassage(next)) return;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setSource(next);
  }, []);

  const close = useCallback(() => {
    setSource(null);
    // Back to the marker/button that opened it, so keyboard and screen
    // reader users keep their place in the transcript.
    returnFocus.current?.focus();
    returnFocus.current = null;
  }, []);

  if (parent) return <>{children}</>;
  return (
    <PassageDialogContext.Provider value={open}>
      {children}
      {source ? <PassageDialog key={`${source.document_id}:${source.chunk_id}`} source={source} onClose={close} /> : null}
    </PassageDialogContext.Provider>
  );
}

/** „Textstelle öffnen“ on a source card / sources panel entry; renders
 * nothing when the source has no passage or no provider is mounted. */
export function OpenPassageButton({ source }: { source: Source }) {
  const { t } = useI18n();
  const open = usePassageDialog();
  if (!open || !canOpenPassage(source)) return null;
  return (
    <button
      type="button"
      onClick={(event) => {
        // A card's own click/hover handlers stay untouched.
        event.stopPropagation();
        open(source);
      }}
      className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--accent)] underline-offset-2 hover:underline"
    >
      <TextQuote className="h-3 w-3" aria-hidden="true" />
      {t('chat.passage.open')}
    </button>
  );
}

// --- dialog -----------------------------------------------------------------

type LoadState =
  | { status: 'loading' }
  | { status: 'ok'; passage: PassageResponse }
  | { status: 'not_found' }
  | { status: 'failed' };

async function loadPassage(source: Source): Promise<LoadState> {
  const query = new URLSearchParams({ document_id: source.document_id, chunk_id: String(source.chunk_id), context: String(CONTEXT_CHUNKS) });
  const response = await fetch(`/api/sources/passage?${query}`, { cache: 'no-store' }).catch(() => null);
  if (response?.status === 404) return { status: 'not_found' };
  if (!response?.ok) return { status: 'failed' };
  const passage = (await response.json().catch(() => null)) as PassageResponse | null;
  if (!passage || !Array.isArray(passage.chunks)) return { status: 'failed' };
  return { status: 'ok', passage };
}

function PassageImage({ src, alt }: ImgHTMLAttributes<HTMLImageElement>) {
  if (!src || typeof src !== 'string') return null;
  // eslint-disable-next-line @next/next/no-img-element -- proxied, per-passage remote image, not a static asset
  return <img src={toProxiedImageUrl(src)} alt={alt ?? ''} className="max-w-full rounded-lg" />;
}

const FOCUSABLE = 'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';

export function PassageDialog({ source, onClose }: { source: Source; onClose: () => void }) {
  const { t } = useI18n();
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const targetRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let active = true;
    void loadPassage(source).then((next) => {
      if (active) setState(next);
    });
    return () => {
      active = false;
    };
  }, [source]);

  useEffect(() => {
    closeRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    if (state.status === 'ok') targetRef.current?.scrollIntoView?.({ block: 'center' });
  }, [state]);

  // Keeps Tab inside the modal while it is open.
  function trapFocus(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Tab' || !dialogRef.current) return;
    const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const passage = state.status === 'ok' ? state.passage : null;
  const target = useMemo(
    () => passage?.chunks.find((chunk) => chunk.is_target) ?? passage?.chunks.find((chunk) => chunk.chunk_id === passage.target_chunk_id),
    [passage]
  );
  const title = passage?.title || passage?.original_filename || source.original_filename || t('chat.passage.title');
  const version = passage?.document_version ?? source.document_version;
  const pageStart = target?.page_start ?? null;
  const pageEnd = target?.page_end ?? null;
  const page =
    pageStart == null
      ? null
      : pageEnd == null || pageEnd === pageStart
        ? t('chat.passage.page', { page: pageStart })
        : t('chat.passage.pageRange', { start: pageStart, end: pageEnd });
  const headingPath = target?.heading_path ?? [];
  const link = passage ? passageLink(passage, target) : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="passage-dialog-title"
        onKeyDown={trapFocus}
        className="relative flex max-h-[90dvh] w-full max-w-2xl flex-col rounded-xl border border-[var(--line)] bg-[var(--surface)] shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-[var(--muted)]">{t('chat.passage.title')}</p>
            <h2 id="passage-dialog-title" className="truncate text-[17px] font-semibold">
              {title}
            </h2>
            <p className="mt-0.5 text-xs text-[var(--muted)]">
              {[version != null ? t('chat.passage.version', { version }) : null, page].filter(Boolean).join(' · ')}
            </p>
            {headingPath.length > 0 ? (
              <nav aria-label={t('chat.passage.headingLabel')} className="mt-1">
                <ol className="flex flex-wrap items-center gap-x-1 text-xs text-[var(--ink)]">
                  {headingPath.map((heading, index) => (
                    <li key={`${index}-${heading}`} className="inline-flex items-center gap-1">
                      {index > 0 ? <span aria-hidden="true" className="text-[var(--muted)]">›</span> : null}
                      {heading}
                    </li>
                  ))}
                </ol>
              </nav>
            ) : null}
          </div>
          <Button ref={closeRef} variant="ghost" size="sm" onClick={onClose} aria-label={t('chat.passage.close')}>
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-5 text-sm leading-relaxed">
          {state.status === 'loading' ? (
            <p role="status" className="text-[var(--muted)]">
              {t('chat.passage.loading')}
            </p>
          ) : state.status === 'not_found' ? (
            <p role="alert" className="rounded-lg bg-[var(--warn-bg)] px-3 py-2 text-[var(--warn)]">
              {t('chat.passage.notFound')}
            </p>
          ) : state.status === 'failed' ? (
            <p role="alert" className="rounded-lg bg-[var(--warn-bg)] px-3 py-2 text-[var(--warn)]">
              {t('chat.passage.loadFailed')}
            </p>
          ) : (
            state.passage.chunks.map((chunk) => {
              const isTarget = chunk === target;
              return (
                <section
                  key={chunk.chunk_id}
                  ref={isTarget ? targetRef : undefined}
                  aria-label={isTarget ? t('chat.passage.cited') : undefined}
                  data-target={isTarget}
                  className={isTarget ? 'passage-target prose-chat' : 'prose-chat text-[var(--muted)]'}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]} components={{ img: PassageImage }}>
                    {chunk.text}
                  </ReactMarkdown>
                </section>
              );
            })
          )}
        </div>

        {link ? (
          <div className="border-t border-[var(--line)] px-5 py-3">
            <a
              href={link}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--accent)] underline-offset-2 hover:underline"
            >
              {t('chat.passage.openOriginal')}
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          </div>
        ) : null}
      </div>
    </div>
  );
}
