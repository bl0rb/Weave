import type { Source } from '@/types/weave-api';

/** Colours available for knowledge spaces (globals.css's `--space-1` …). */
export const SPACE_COLOR_COUNT = 6;

export interface AnswerSpace {
  /** The knowledge space's slug; `null` for a pre-Collections legacy source. */
  collection: string | null;
  /** The `[N]` numbers the answer cites from this space. */
  refs: number[];
  /** The bot's notice for this space (e.g. "Nur intern"), if any. */
  notice: string | null;
  /** 1-based colour slot, `var(--space-<color>)`. */
  color: number;
}

/**
 * The knowledge spaces an answer draws on, in order of first appearance —
 * the same order (and so the same colours) as Weave-API's
 * `knowledge_spaces` answer metadata (app/services/notices.py).
 */
export function answerSpaces(sources: Source[] | null | undefined): AnswerSpace[] {
  const spaces = new Map<string | null, AnswerSpace>();
  for (const source of sources ?? []) {
    const key = source.collection ?? null;
    let space = spaces.get(key);
    if (!space) {
      space = { collection: key, refs: [], notice: null, color: (spaces.size % SPACE_COLOR_COUNT) + 1 };
      spaces.set(key, space);
    }
    if (typeof source.ref === 'number' && !space.refs.includes(source.ref)) space.refs.push(source.ref);
    const notice = source.notice?.trim();
    if (!space.notice && notice) space.notice = notice;
  }
  return [...spaces.values()];
}

/** The colour slot of a source's knowledge space within `spaces`. */
export function spaceColor(spaces: AnswerSpace[], source: Source): number | undefined {
  return spaces.find((space) => space.collection === (source.collection ?? null))?.color;
}

/**
 * The `[N]` markers an answer can resolve: only sources with an explicit
 * `ref` (the runtime sets it when it kept just the cited sources) — an agent
 * turn numbers facts, not sources, so its markers must not be mapped.
 */
export function citedSourcesByRef(sources: Source[] | null | undefined): Map<number, Source> {
  const byRef = new Map<number, Source>();
  for (const source of sources ?? []) {
    if (typeof source.ref === 'number' && !byRef.has(source.ref)) byRef.set(source.ref, source);
  }
  return byRef;
}

/** Weave-Runtime's own citation pattern (`_CITATION_RE` in app/services/chat.py). */
export const CITATION_RE = /\[(?:source\s*)?(\d+(?:\s*[,;]\s*(?:source\s*)?\d+)*)\]/gi;

export function citationNumbers(group: string): number[] {
  return (group.match(/\d+/g) ?? []).map(Number);
}
