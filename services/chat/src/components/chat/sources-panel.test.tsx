// @vitest-environment jsdom
//
// The sources panel's cards lead with the locator — the wiki page link for a
// Confluence source, else the page number(s) — with the rest kept secondary.
// See source-cards.test.tsx for why this file opts into jsdom per file.
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { SourcesPanel } from '@/components/chat/sources-panel';
import type { Source } from '@/types/weave-api';

function makeSource(overrides: Partial<Source>): Source {
  return {
    source: 'confluence',
    original_filename: 'Handbuch.pdf',
    page_start: 4,
    page_end: 4,
    document_version: 2,
    document_id: 'doc-1',
    chunk_id: 0,
    score: 0.87,
    score_kind: 'rerank',
    collection: 'handbuch',
    ...overrides,
  };
}

describe('SourcesPanel', () => {
  afterEach(() => cleanup());

  it('shows the page number of a document source and keeps the details in one muted line', () => {
    render(<SourcesPanel sources={[makeSource({})]} scopeLabel="Alle" />);
    expect(screen.getByText('S. 4')).toBeTruthy();
    expect(screen.getByText('v2 · Collection: handbuch · Relevanz 87 %')).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('shows the wiki page as an external link for a Confluence source', () => {
    render(<SourcesPanel sources={[makeSource({ page_start: null, page_end: null, source_kind: 'confluence', source_url: 'https://wiki.example/pages/42' })]} scopeLabel="Alle" />);
    const link = screen.getByRole('link', { name: 'Wiki-Seite öffnen' });
    expect(link.getAttribute('href')).toBe('https://wiki.example/pages/42');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.queryByText(/^S\. /)).toBeNull();
  });

  it('shows neither a locator nor a link when there is no page and no URL, and skips a missing version', () => {
    render(<SourcesPanel sources={[makeSource({ page_start: null, page_end: null, document_version: null, source_kind: 'upload', collection: null })]} scopeLabel="Alle" />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.queryByText(/^S\. /)).toBeNull();
    expect(screen.getByText('Collection: ohne Collection · Relevanz 87 %')).toBeTruthy();
  });
});
