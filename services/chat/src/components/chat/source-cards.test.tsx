// @vitest-environment jsdom
//
// Covers FINDING 2: `Source.collection` must reach the UI and render
// honestly, including the `null` case, instead of being silently omitted.
// See vitest.config.ts's own docstring for why this file opts into jsdom
// via a per-file pragma while the rest of the suite stays plain Node.
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { SourceCards } from '@/components/chat/source-cards';
import type { Source } from '@/types/weave-api';

function makeSource(overrides: Partial<Source>): Source {
  return {
    source: 'confluence',
    original_filename: 'Handbuch.pdf',
    page_start: 1,
    page_end: 1,
    document_version: 2,
    document_id: 'doc-1',
    chunk_id: 0,
    score: 0.87,
    collection: 'handbuch',
    ...overrides,
  };
}

describe('SourceCards', () => {
  afterEach(() => cleanup());

  it('shows the collection slug when one is set', () => {
    render(<SourceCards sources={[makeSource({ collection: 'handbuch' })]} />);
    expect(screen.getByText('Collection: handbuch')).toBeTruthy();
  });

  it('renders an explicit "ohne Collection" label for a null collection, never omitting the field', () => {
    render(<SourceCards sources={[makeSource({ chunk_id: 1, collection: null })]} />);
    expect(screen.getByText('Collection: ohne Collection')).toBeTruthy();
  });
});
