// @vitest-environment jsdom
//
// Covers FINDING 2: `Source.collection` must reach the UI and render
// honestly, including the `null` case, instead of being silently omitted.
// See vitest.config.ts's own docstring for why this file opts into jsdom
// via a per-file pragma while the rest of the suite stays plain Node.
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
    score_kind: 'rerank',
    collection: 'handbuch',
    ...overrides,
  };
}

describe('SourceCards', () => {
  afterEach(() => cleanup());

  it('shows the collection slug when one is set', () => {
    render(<SourceCards sources={[makeSource({ collection: 'handbuch' })]} />);
    expect(screen.queryByText('Collection: handbuch')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Belege (1)' }));
    expect(screen.getByText('Collection: handbuch')).toBeTruthy();
  });

  it('renders an explicit "ohne Collection" label for a null collection, never omitting the field', () => {
    render(<SourceCards sources={[makeSource({ chunk_id: 1, collection: null })]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Belege (1)' }));
    expect(screen.getByText('Collection: ohne Collection')).toBeTruthy();
  });

  it('renders a deduplicated image strip proxied through the portal-artifacts route', () => {
    const imageUrl = 'http://ingest.example/api/v1/portal/releases/rel-1/artifacts/diagram.png';
    const { container } = render(
      <SourceCards
        sources={[
          makeSource({ chunk_id: 1, images: [imageUrl] }),
          makeSource({ chunk_id: 2, images: [imageUrl] }),
        ]}
      />
    );
    expect(screen.getByText('Bilder aus den Quellen')).toBeTruthy();
    const images = container.querySelectorAll('img');
    expect(images).toHaveLength(1);
    expect(images[0].getAttribute('src')).toBe('/api/portal-artifacts/rel-1/diagram.png');
  });

  it('links a wiki source to its exact page and names the uploader of an upload', () => {
    render(<SourceCards sources={[
      makeSource({ chunk_id: 1, source_kind: 'confluence', source_url: 'https://wiki.example/pages/42' }),
      makeSource({ chunk_id: 2, source: '3f2a.pdf', source_kind: 'upload', uploaded_by: 'ada', uploaded_at: '2026-09-28T10:00:00+00:00' }),
      makeSource({ chunk_id: 3, source_kind: 'upload', source_url: 'javascript:alert(1)' }),
    ]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Belege (3)' }));
    const link = screen.getByRole('link', { name: 'Wiki-Seite öffnen' });
    expect(link.getAttribute('href')).toBe('https://wiki.example/pages/42');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    expect(screen.getByText('Hochgeladen von ada am 28.9.2026')).toBeTruthy();
    // Never a link for anything but http(s); the internal file name is not shown either.
    expect(screen.getAllByRole('link')).toHaveLength(1);
    expect(screen.queryByText('Quelle: 3f2a.pdf')).toBeNull();
  });

  it('renders no image strip when no source carries images', () => {
    render(<SourceCards sources={[makeSource({})]} />);
    expect(screen.queryByText('Bilder aus den Quellen')).toBeNull();
  });

  it('shows a rerank score as a rounded percentage', () => {
    render(<SourceCards sources={[makeSource({ score: 0.87, score_kind: 'rerank' })]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Belege (1)' }));
    expect(screen.getByText(/Relevanz 87 %/)).toBeTruthy();
  });

  it('hides the score for an rrf (non-reranked) source', () => {
    render(<SourceCards sources={[makeSource({ score: 0.02, score_kind: 'rrf' })]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Belege (1)' }));
    expect(screen.queryByText(/Relevanz/)).toBeNull();
  });
});
