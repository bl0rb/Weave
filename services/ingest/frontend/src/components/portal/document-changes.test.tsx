// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import type { DocumentDiff } from '@/lib/document-freshness';
import { DocumentChanges } from './document-changes';

const push = vi.fn();
const replace = vi.fn();
const router = { push, replace, refresh: vi.fn() };
vi.mock('next/navigation', () => ({ useRouter: () => router, usePathname: () => '/reviews/v3/changes' }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
vi.mock('@/components/back-link', () => ({ BackLink: ({ fallbackLabel, fallbackHref }: { fallbackLabel: string; fallbackHref: string }) => <a href={fallbackHref}>{fallbackLabel}</a> }));
const api = vi.mocked(apiJson);

const entry = (id: string, n: number) => ({ job_id: id, document_version: n, content_sha256: null, status: 'FINISHED', created_at: `2026-0${n}-01T00:00:00Z`, uploaded_by: null, is_current: n === 3 });
let versions: unknown = { items: [entry('v3', 3), entry('v2', 2), entry('v1', 1)] };
const diff: DocumentDiff = {
  base: { job_id: 'v2', document_version: 2, created_at: '2026-02-01T00:00:00Z' },
  target: { job_id: 'v3', document_version: 3, created_at: '2026-03-01T00:00:00Z' },
  stats: { added_lines: 2, removed_lines: 1, sections_added: 1, sections_removed: 0, sections_changed: 1 },
  summary: ['Abschnitt „Reisekosten › Tagegeld“ geändert: 1 Zeile ersetzt', 'Abschnitt „Anhang“ hinzugefügt'],
  sections: [
    { heading_path: ['Reisekosten', 'Tagegeld'], change: 'changed', added_lines: 1, removed_lines: 1, hunks: [
      { op: 'equal', base: ['Für Dienstreisen gilt:'], target: ['Für Dienstreisen gilt:'] },
      { op: 'replace', base: ['Das Tagegeld beträgt 24 €.'], target: ['Das Tagegeld beträgt 28 €.'] },
    ] },
    { heading_path: ['Anhang'], change: 'added', added_lines: 1, removed_lines: 0, hunks: [{ op: 'insert', base: [], target: ['Neue Tabelle'] }] },
  ],
};
let diffResponse: (path: string) => unknown;

beforeEach(() => {
  versions = { items: [entry('v3', 3), entry('v2', 2), entry('v1', 1)] };
  diffResponse = () => diff;
  push.mockReset();
  replace.mockReset();
  api.mockReset();
  api.mockImplementation(async path => ((path as string).endsWith('/versions') ? versions : diffResponse(path as string)));
});
afterEach(cleanup);

const diffCalls = () => api.mock.calls.map(([path]) => path as string).filter(path => path.includes('/diff'));

it('compares with the next older visible version and renders summary, stats and hunks', async () => {
  render(<DocumentChanges id="v3" />);
  expect(await screen.findByText('Abschnitt „Anhang“ hinzugefügt')).toBeTruthy();
  expect(diffCalls()).toEqual(['/api/v1/portal/documents/v3/diff?base=v2']);
  expect(screen.getByText('Was sich von Version 2 zu Version 3 geändert hat.')).toBeTruthy();
  expect(screen.getByText('+2 Zeilen')).toBeTruthy();
  expect(screen.getByText('−1 Zeile')).toBeTruthy();
  const section = screen.getByRole('region', { name: 'Reisekosten › Tagegeld' });
  expect(within(section).getByText('Geändert')).toBeTruthy();
  expect(section.querySelector('del')?.textContent).toBe('Entfernt: Das Tagegeld beträgt 24 €.');
  expect(section.querySelector('ins')?.textContent).toBe('Hinzugefügt: Das Tagegeld beträgt 28 €.');
  expect(within(section).getByText('Für Dienstreisen gilt:')).toBeTruthy();
  expect(within(screen.getByRole('region', { name: 'Anhang' })).getByText('Neuer Abschnitt')).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Zurück zum Dokument' }).getAttribute('href')).toBe('/reviews/v3');
});

it('uses an explicit base and navigates only when the comparison is submitted', async () => {
  render(<DocumentChanges id="v3" base="v1" />);
  await screen.findByText('Abschnitt „Anhang“ hinzugefügt');
  expect(diffCalls()).toEqual(['/api/v1/portal/documents/v3/diff?base=v1']);
  const baseSelect = screen.getByLabelText('Ältere Version') as HTMLSelectElement;
  const compare = screen.getByRole('button', { name: 'Vergleichen' }) as HTMLButtonElement;
  expect(baseSelect.value).toBe('v1');
  expect(compare.disabled).toBe(true); // the shown pair
  // The target itself is never offered as base.
  expect(Array.from(baseSelect.options).map(option => option.value)).toEqual(['v2', 'v1']);
  // Moving through the options (keyboard) does not navigate (WCAG 3.2.2).
  fireEvent.change(baseSelect, { target: { value: 'v2' } });
  expect(push).not.toHaveBeenCalled();
  fireEvent.click(compare);
  expect(push).toHaveBeenCalledWith('/reviews/v3/changes?base=v2');
  // A newer target keeps a still older base ...
  fireEvent.change(baseSelect, { target: { value: 'v1' } });
  fireEvent.change(screen.getByLabelText('Neuere Version'), { target: { value: 'v2' } });
  expect(push).toHaveBeenCalledTimes(1);
  expect(Array.from(baseSelect.options).map(option => option.value)).toEqual(['v1']);
  fireEvent.click(compare);
  expect(push).toHaveBeenLastCalledWith('/reviews/v2/changes?base=v1');
  // ... and the oldest version as target has no older version to start from.
  fireEvent.change(screen.getByLabelText('Neuere Version'), { target: { value: 'v1' } });
  expect(baseSelect.value).toBe('');
  expect(Array.from(baseSelect.options).map(option => option.value)).toEqual(['']);
  expect(compare.disabled).toBe(true);
});

it('offers only older FINISHED versions as base and skips failed ones by default', async () => {
  versions = { items: [entry('v3', 3), { ...entry('v2', 2), status: 'FAILED' }, entry('v1', 1)] };
  render(<DocumentChanges id="v3" />);
  await screen.findByText('Abschnitt „Anhang“ hinzugefügt');
  expect(diffCalls()).toEqual(['/api/v1/portal/documents/v3/diff?base=v1']);
  expect(Array.from((screen.getByLabelText('Ältere Version') as HTMLSelectElement).options).map(option => option.value)).toEqual(['v1']);
  expect(Array.from((screen.getByLabelText('Neuere Version') as HTMLSelectElement).options).map(option => option.value)).toEqual(['v3', 'v1']);
});

it('hides the selectors when only one version has markdown', async () => {
  versions = { items: [entry('v3', 3), { ...entry('v2', 2), status: 'PENDING' }] };
  render(<DocumentChanges id="v3" />);
  await waitFor(() => expect(api).toHaveBeenCalled());
  expect(screen.queryByLabelText('Ältere Version')).toBeNull();
});

it('swaps an inverted pair instead of rendering a reversed diff', async () => {
  render(<DocumentChanges id="v1" base="v3" />);
  await waitFor(() => expect(replace).toHaveBeenCalledWith('/reviews/v3/changes?base=v1'));
  // A reversed diff that arrived first is never shown.
  await waitFor(() => expect(diffCalls()).toHaveLength(1));
  expect(screen.queryByText('Abschnitt „Anhang“ hinzugefügt')).toBeNull();
});

it('says so when the document is the oldest visible version, without requesting a diff', async () => {
  render(<DocumentChanges id="v1" />);
  expect(await screen.findByText('Es gibt keine ältere Version, mit der du dieses Dokument vergleichen kannst.')).toBeTruthy();
  expect(diffCalls()).toEqual([]);
});

it('falls back to the backend default when versions are unavailable, and handles 409', async () => {
  api.mockImplementation(async path => {
    if ((path as string).endsWith('/versions')) throw new ApiError(401, 'password');
    throw new ApiError(409, 'Document has no previous version');
  });
  render(<DocumentChanges id="v3" />);
  expect(await screen.findByText('Es gibt keine ältere Version, mit der du dieses Dokument vergleichen kannst.')).toBeTruthy();
  expect(diffCalls()).toEqual(['/api/v1/portal/documents/v3/diff']);
  expect(screen.queryByLabelText('Ältere Version')).toBeNull();
});

it('shows a not-found error for an invisible or foreign base', async () => {
  diffResponse = () => { throw new ApiError(404, 'Document not found'); };
  render(<DocumentChanges id="v3" base="foreign" />);
  expect((await screen.findByRole('alert')).textContent).toContain('nicht verfügbar');
});

it('shows an empty state when both versions are identical', async () => {
  diffResponse = () => ({ ...diff, stats: { added_lines: 0, removed_lines: 0, sections_added: 0, sections_removed: 0, sections_changed: 0 }, summary: [], sections: [] });
  render(<DocumentChanges id="v3" />);
  expect(await screen.findByText('Keine Unterschiede')).toBeTruthy();
  expect(screen.getByText('Keine inhaltlichen Änderungen erkannt.')).toBeTruthy();
  await waitFor(() => expect(diffCalls()).toHaveLength(1));
});
