// @vitest-environment jsdom
//
// „Textstelle öffnen“ (passage-dialog.tsx): which sources offer it, the
// dialog itself, and opening it from a `[N]` marker, a source card and the
// sources panel. See source-cards.test.tsx for why this file opts into
// jsdom per file.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { canOpenPassage, OpenPassageButton, PassageDialogProvider, passageLink, withTextFragment } from '@/components/chat/passage-dialog';
import { MessageBubble } from '@/components/chat/message-bubble';
import { SourceCards } from '@/components/chat/source-cards';
import { SourcesPanel } from '@/components/chat/sources-panel';
import type { UiMessage } from '@/lib/chat-types';
import type { PassageResponse, Source } from '@/types/weave-api';

const DOC = '1b4e28ba-2fa1-11d2-883f-0016d3cca427';

function makeSource(overrides: Partial<Source> = {}): Source {
  return {
    source: '', original_filename: 'reise.pdf', page_start: 4, page_end: 4, document_version: 3,
    document_id: DOC, chunk_id: 7, score: null, collection: 'hr', ref: 1, ...overrides,
  };
}

function makePassage(overrides: Partial<PassageResponse> = {}): PassageResponse {
  return {
    document_id: DOC, source_job_id: 'job-1', title: 'Reiserichtlinie', original_filename: 'reise.pdf', document_version: 3,
    source_kind: 'upload', source_url: null, collection: 'hr', target_chunk_id: 7,
    chunks: [
      { chunk_id: 6, chunk_index: 1, heading_path: ['Reisekosten'], page_start: 3, page_end: 3, text: 'Vorheriger Abschnitt.', is_target: false },
      { chunk_id: 7, chunk_index: 2, heading_path: ['Reisekosten', 'Tagegeld'], page_start: 4, page_end: 5, text: 'Das **Tagegeld** beträgt 28 Euro pro Tag.', is_target: true },
      { chunk_id: 8, chunk_index: 3, heading_path: ['Reisekosten', 'Übernachtung'], page_start: 5, page_end: 5, text: 'Nächster Abschnitt.', is_target: false },
    ],
    ...overrides,
  };
}

function stubPassage(response: () => Response) {
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => response());
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

describe('canOpenPassage', () => {
  it('accepts a document chunk and rejects knowledge-memory and non-document sources', () => {
    expect(canOpenPassage(makeSource())).toBe(true);
    expect(canOpenPassage(makeSource({ chunk_id: 0 }))).toBe(true);
    expect(canOpenPassage(makeSource({ memory_kind: 'document' }))).toBe(false);
    expect(canOpenPassage(makeSource({ document_id: 'collection:hr', memory_kind: 'collection' }))).toBe(false);
    expect(canOpenPassage(makeSource({ document_id: 'n8n-doc-1' }))).toBe(false);
    expect(canOpenPassage(makeSource({ document_id: '' }))).toBe(false);
    expect(canOpenPassage(makeSource({ chunk_id: -1 }))).toBe(false);
    expect(canOpenPassage(undefined)).toBe(false);
  });
});

describe('withTextFragment / passageLink', () => {
  it('appends the first eight words of the chunk as a text fragment, without markdown syntax', () => {
    expect(withTextFragment('https://example.org/reise', 'Das **Tagegeld** beträgt 28 Euro pro Tag im In-/Ausland und mehr'))
      .toBe('https://example.org/reise#:~:text=Das%20Tagegeld%20betr%C3%A4gt%2028%20Euro%20pro%20Tag%20im');
  });

  it('skips leading headings so the words stay inside one block', () => {
    expect(withTextFragment('https://example.org/r', '## Reisekosten\n\n### Erstattung\n\nDie Erstattung erfolgt monatlich.\n\nZweiter Absatz.'))
      .toBe('https://example.org/r#:~:text=Die%20Erstattung%20erfolgt%20monatlich.');
  });

  it('takes one line of a list or quote block, without markers, and skips tables, code and comments', () => {
    expect(withTextFragment('https://example.org/r', '- Bahn 2. Klasse\n- Flug nur mit Genehmigung')).toBe('https://example.org/r#:~:text=Bahn%202.%20Klasse');
    expect(withTextFragment('https://example.org/r', '> 1. Zitat **eins**\n> weiter')).toBe('https://example.org/r#:~:text=Zitat%20eins');
    expect(withTextFragment('https://example.org/r', '| A | B |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```\n\n<!-- x -->\n\nText danach ![Logo](l.png) hier'))
      .toBe('https://example.org/r#:~:text=Text%20danach%20hier');
  });

  it('falls back to the heading when the chunk has nothing else', () => {
    expect(withTextFragment('https://example.org/r', '## Reisekosten')).toBe('https://example.org/r#:~:text=Reisekosten');
  });

  it('drops an overlap prefix that the previous chunk ends with, even one cut mid-word or spanning blocks', () => {
    const previous = 'Fahrtkosten.\n\nDie Übernachtungskosten werden erstattet.\n\nBelege sind einzureichen.';
    expect(withTextFragment('https://example.org/r', 'ungskosten werden erstattet.\n\nBelege sind einzureichen.\n\nDas Tagegeld beträgt 28 Euro.', previous))
      .toBe('https://example.org/r#:~:text=Das%20Tagegeld%20betr%C3%A4gt%2028%20Euro.');
    // Not a suffix of the previous chunk: nothing is dropped.
    expect(withTextFragment('https://example.org/r', 'Neuer Absatz hier.\n\nMehr.', previous)).toBe('https://example.org/r#:~:text=Neuer%20Absatz%20hier.');
  });

  it('drops the possibly cut-off first word when the previous chunk is unknown', () => {
    expect(withTextFragment('https://example.org/r', 'ungskosten werden erstattet.\n\nMehr.', null)).toBe('https://example.org/r#:~:text=werden%20erstattet.');
    expect(withTextFragment('https://example.org/r', '## Titel\n\nGanzer Satz.', null)).toBe('https://example.org/r#:~:text=Ganzer%20Satz.');
    expect(withTextFragment('https://example.org/r', 'Allein', null)).toBe('https://example.org/r#:~:text=Allein');
  });

  it('escapes the fragment separator "-" and joins an existing fragment', () => {
    expect(withTextFragment('https://example.org/a#top', 'E-Mail an [IT](https://it.example)')).toBe('https://example.org/a#top:~:text=E%2DMail%20an%20IT');
  });

  it('leaves the URL alone when the chunk has no words', () => {
    expect(withTextFragment('https://example.org/', '  ** ')).toBe('https://example.org/');
  });

  it('links only http(s) and adds the fragment only for website sources', () => {
    const passage = makePassage();
    const target = passage.chunks[1];
    expect(passageLink({ ...passage, source_url: 'javascript:alert(1)' }, target)).toBeNull();
    expect(passageLink({ ...passage, source_url: null }, target)).toBeNull();
    expect(passageLink({ ...passage, source_kind: 'confluence', source_url: 'https://wiki.example/p/1' }, target)).toBe('https://wiki.example/p/1');
    expect(passageLink({ ...passage, source_kind: 'website', source_url: 'https://example.org/r' }, target)).toBe(
      'https://example.org/r#:~:text=Das%20Tagegeld%20betr%C3%A4gt%2028%20Euro%20pro%20Tag.',
    );
  });

  it('uses the neighbouring chunk to drop an overlap and treats a missing predecessor as unknown', () => {
    const website = { source_kind: 'website' as const, source_url: 'https://example.org/r' };
    const withOverlap = makePassage({
      ...website,
      chunks: [
        { chunk_id: 6, chunk_index: 1, heading_path: [], page_start: null, page_end: null, text: '## Reisekosten\n\nDie Übernachtungskosten werden erstattet.', is_target: false },
        { chunk_id: 7, chunk_index: 2, heading_path: [], page_start: null, page_end: null, text: 'ungskosten werden erstattet.\n\nDas Tagegeld beträgt 28 Euro.', is_target: true },
      ],
    });
    expect(passageLink(withOverlap, withOverlap.chunks[1])).toBe('https://example.org/r#:~:text=Das%20Tagegeld%20betr%C3%A4gt%2028%20Euro.');
    // Target first in the passage but not the document's first chunk.
    const noPredecessor = makePassage({ ...website, chunks: [withOverlap.chunks[1]] });
    expect(passageLink(noPredecessor, noPredecessor.chunks[0])).toBe('https://example.org/r#:~:text=werden%20erstattet.');
    // The document's first chunk has no overlap: every word counts.
    const first = makePassage({ ...website, chunks: [{ ...withOverlap.chunks[1], chunk_index: 0 }] });
    expect(passageLink(first, first.chunks[0])).toBe('https://example.org/r#:~:text=ungskosten%20werden%20erstattet.');
  });
});

describe('PassageDialog', () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  function renderButton(source: Source = makeSource()) {
    render(
      <PassageDialogProvider>
        <OpenPassageButton source={source} />
      </PassageDialogProvider>,
    );
    return screen.getByRole('button', { name: 'Textstelle öffnen' });
  }

  it('loads the passage and shows title, version, page, heading breadcrumb and the highlighted cited chunk', async () => {
    const fetchMock = stubPassage(() => json(makePassage()));
    fireEvent.click(renderButton());

    // While loading, the heading already names the source's file.
    expect(screen.getByRole('dialog', { name: 'reise.pdf' })).toBeTruthy();
    const cited = await screen.findByRole('region', { name: 'Zitierte Textstelle' });
    const dialog = screen.getByRole('dialog', { name: 'Reiserichtlinie' });
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(cited.textContent).toBe('Das Tagegeld beträgt 28 Euro pro Tag.');
    expect(cited.className).toContain('passage-target');
    // The scroll runs in a passive effect after the commit that findByRole saw.
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
    expect(dialog.textContent).toContain('Vorheriger Abschnitt.');
    expect(dialog.textContent).toContain('Nächster Abschnitt.');
    expect(screen.getByText('Version 3 · Seiten 4–5')).toBeTruthy();
    const breadcrumb = screen.getByRole('navigation', { name: 'Gliederung' });
    expect([...breadcrumb.querySelectorAll('li')].map((li) => li.textContent)).toEqual(['Reisekosten', '›Tagegeld']);
    // An upload has no original to link to.
    expect(screen.queryByRole('link')).toBeNull();
    expect(String(fetchMock.mock.calls[0][0])).toBe(`/api/sources/passage?document_id=${DOC}&chunk_id=7&context=1`);
  });

  it('links a website source to the cited text on the page', async () => {
    stubPassage(() => json(makePassage({ source_kind: 'website', source_url: 'https://example.org/r' })));
    fireEvent.click(renderButton());
    const link = await screen.findByRole('link', { name: 'Im Original öffnen' });
    expect(link.getAttribute('href')).toBe('https://example.org/r#:~:text=Das%20Tagegeld%20betr%C3%A4gt%2028%20Euro%20pro%20Tag.');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('explains a 404 as a passage that is no longer available', async () => {
    stubPassage(() => json({ kind: 'rejected' }, 404));
    fireEvent.click(renderButton());
    expect((await screen.findByRole('alert')).textContent).toBe(
      'Diese Textstelle ist nicht mehr verfügbar – das Dokument wurde vermutlich aktualisiert.',
    );
    // Falls back to the source's own file name for the heading.
    expect(screen.getByRole('dialog', { name: 'reise.pdf' })).toBeTruthy();
  });

  it('shows a generic error for other failures', async () => {
    stubPassage(() => json({ kind: 'gateway_unreachable' }, 502));
    fireEvent.click(renderButton());
    expect((await screen.findByRole('alert')).textContent).toBe('Die Textstelle konnte nicht geladen werden. Bitte versuche es erneut.');
  });

  it('focuses the close button, closes on Escape and returns focus to the opener', async () => {
    stubPassage(() => json(makePassage()));
    const opener = renderButton();
    opener.focus();
    fireEvent.click(opener);
    const close = screen.getByRole('button', { name: 'Textstelle schließen' });
    expect(document.activeElement).toBe(close);
    await screen.findByRole('region', { name: 'Zitierte Textstelle' });

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it('closes via the close button too', async () => {
    stubPassage(() => json(makePassage()));
    fireEvent.click(renderButton());
    fireEvent.click(screen.getByRole('button', { name: 'Textstelle schließen' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('offers no button for a knowledge-memory source or without a provider', () => {
    render(
      <PassageDialogProvider>
        <OpenPassageButton source={makeSource({ memory_kind: 'document' })} />
      </PassageDialogProvider>,
    );
    expect(screen.queryByRole('button', { name: 'Textstelle öffnen' })).toBeNull();
    cleanup();
    render(<OpenPassageButton source={makeSource()} />);
    expect(screen.queryByRole('button', { name: 'Textstelle öffnen' })).toBeNull();
  });
});

describe('opening a passage from the chat', () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  function answer(sources: Source[]): UiMessage {
    return {
      id: 'ui-1', role: 'assistant', content: 'Das Tagegeld beträgt 28 Euro [1].', streaming: false, sources, trace: null, error: null,
      viaFallback: false, slowResponse: false, progressLine: null, agentStatuses: [],
    };
  }

  it('opens the passage from a [N] citation marker', async () => {
    const fetchMock = stubPassage(() => json(makePassage()));
    render(
      <PassageDialogProvider>
        <MessageBubble message={answer([makeSource()])} />
      </PassageDialogProvider>,
    );

    const marker = screen.getByRole('button', { name: 'Textstelle zu Quelle 1 öffnen' });
    expect(marker.className).toBe('cite-marker');
    expect(marker.textContent).toBe('1');
    fireEvent.click(marker);

    await screen.findByRole('region', { name: 'Zitierte Textstelle' });
    expect(String(fetchMock.mock.calls[0][0])).toContain(`document_id=${DOC}&chunk_id=7`);
  });

  it('keeps the marker a plain badge when the bot hides its sources or the source has no passage', () => {
    const { unmount } = render(
      <PassageDialogProvider>
        <MessageBubble message={answer([makeSource()])} showSources={false} />
      </PassageDialogProvider>,
    );
    expect(screen.queryByRole('button', { name: 'Textstelle zu Quelle 1 öffnen' })).toBeNull();
    expect(document.querySelector('.cite-marker')?.tagName).toBe('SPAN');
    unmount();

    render(
      <PassageDialogProvider>
        <MessageBubble message={answer([makeSource({ memory_kind: 'document' })])} />
      </PassageDialogProvider>,
    );
    expect(screen.queryByRole('button', { name: 'Textstelle zu Quelle 1 öffnen' })).toBeNull();
  });

  it('opens the passage from an inline source card', async () => {
    stubPassage(() => json(makePassage()));
    render(
      <PassageDialogProvider>
        <SourceCards sources={[makeSource()]} messageId="ui-1" />
      </PassageDialogProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Belege (1)' }));
    fireEvent.click(screen.getByRole('button', { name: 'Textstelle öffnen' }));
    expect(await screen.findByRole('region', { name: 'Zitierte Textstelle' })).toBeTruthy();
  });

  it('opens the passage from the sources panel', async () => {
    stubPassage(() => json(makePassage()));
    render(
      <PassageDialogProvider>
        <SourcesPanel sources={[makeSource(), makeSource({ document_id: 'collection:hr', chunk_id: 0, memory_kind: 'collection' })]} />
      </PassageDialogProvider>,
    );
    const buttons = screen.getAllByRole('button', { name: 'Textstelle öffnen' });
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Reiserichtlinie' })).toBeTruthy());
  });
});
