// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiFetch, apiJson } from '@/lib/api';
import { ReviewDocument, ReviewInbox } from './reviews';
import { useVisiblePolling } from '@/lib/data-cache';

const auth = vi.hoisted(() => ({ user: { role: 'admin' as 'admin' | 'user' } }));
const push = vi.fn();
const refresh = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push, refresh }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiFetch: vi.fn(), apiJson: vi.fn() }));
vi.mock('@/lib/data-cache', () => ({ useVisiblePolling: vi.fn() }));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => auth }));
vi.mock('@/components/markdown/markdown-view', () => ({ MarkdownView: ({ markdown }: { markdown: string }) => <div>{markdown}</div> }));
const content = { id: 'doc', original_filename: 'Regelwerk.pdf', status: 'FINISHED', collection_id: 'area', collection_name: 'Service', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, can_reprocess: true, profile_id: 'ppocrv6_tiny_structurev3', release: null, review_decision: null, source: { kind: 'upload' as const, label: 'Hochgeladen', path: 'Kunden/Vertraege', url: null }, markdown: 'Geprüfter Text', markdown_sha256: 'a'.repeat(64),
  quality: { grade: 'A', score: 0.95, recommendation: 'allow', thresholds: { A: 0.9, B: 0.75 }, signals: { ocr_confidence: 0.92, confidence_sample_size: 1234, structure_quality: 0.8, noise_penalty: 0.05, text_quality: 0.95, field_validation: {} }, issues: [] },
  quality_missing_reason: null };
const capabilities = { profiles: [
  { value: 'ppocrv6_tiny_structurev3', label: 'Tiny', description: '', kind: 'ocr' },
  { value: 'ppocrv6_medium_structurev3', label: 'Medium', description: '', kind: 'ocr' },
  { value: 'vl:vision', label: 'VL: Dokumenten-KI', description: 'Vision model', kind: 'vl' },
  { value: 'openai_vision', label: 'Unconfigured vision', description: '', kind: 'vl' },
] };
const api = vi.mocked(apiJson);
const fetcher = vi.mocked(apiFetch);
const config = { publication_configured: true, team_name: 'Service', team_names: ['Service'] };
function mockDocument(overrides = {}) {
  api.mockImplementation(async path => path === '/api/v1/portal/config' ? config
    : path === '/api/v1/paddle/capabilities' ? capabilities : { ...content, ...overrides });
}
beforeEach(() => {
  api.mockReset();
  fetcher.mockReset();
  fetcher.mockResolvedValue({ ok: true, blob: async () => new Blob(['{}'], { type: 'application/json' }) } as Response);
  push.mockReset();
  refresh.mockReset();
  vi.mocked(useVisiblePolling).mockClear();
  mockDocument();
});
afterEach(cleanup);
it('requires explicit confirmation and submits exactly the preview hash', async () => {
  render(<ReviewDocument id="doc" />);
  const button = await screen.findByRole('button', { name: 'Geprüften Stand freigeben' });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole('checkbox', { name: /für die Berechtigten des Wissensbereichs/ })).toBeTruthy();
  expect(screen.queryByText(/Eine erfolgreiche Übergabe ist noch keine Bestätigung/)).toBeNull();
  expect(screen.getByText('Herkunft')).toBeTruthy();
  expect(screen.getByText('Hochgeladen: Kunden/Vertraege')).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox'));
  expect((button as HTMLButtonElement).disabled).toBe(false);
  api.mockResolvedValueOnce({ id: 'release', created_at: content.created_at, status: 'pending', error_message: null, released_by: 'anna' });
  fireEvent.click(button);
  await screen.findByText('Freigabe gespeichert');
  expect(screen.getByText('Freigegeben von anna')).toBeTruthy();
  const mutation = api.mock.calls.find(([path]) => path.endsWith('/release'));
  expect(JSON.parse(mutation?.[1]?.body as string)).toEqual({ markdown_sha256: content.markdown_sha256 });
});

it('deletes an unreleased document only after danger confirmation', async () => {
  render(<ReviewDocument id="doc" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Dokument löschen' }));
  expect(fetcher).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Dokument löschen' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/v1/jobs/doc', { method: 'DELETE' }));
  expect(push).toHaveBeenCalledWith('/reviews');
});
it('explains the quality grade with its signals and thresholds', async () => {
  render(<ReviewDocument id="doc" />);
  fireEvent.click(await screen.findByText('Warum Stufe A?'));
  expect(screen.getByText(/OCR-Konfidenz: 92 % \(Stichprobe: 1\.234 Werte\)/)).toBeTruthy();
  expect(screen.getByText(/Strukturqualität: 80 %/)).toBeTruthy();
  expect(screen.getByText(/Textqualität: 95 % \(Rauschen 5 %\)/)).toBeTruthy();
  expect(screen.getByText(/Schwellenwerte: A ab 90 %, B ab 75 %, sonst C\./)).toBeTruthy();
  expect(screen.getByText(/Gesamtwert: 95 %/)).toBeTruthy();
});

it('shows the missing-grade reason when no automatic quality check ran', async () => {
  mockDocument({ quality_grade: null, quality: null, quality_missing_reason: 'import_without_gate' });
  render(<ReviewDocument id="doc" />);
  fireEvent.click(await screen.findByText('Warum keine Bewertung?'));
  expect(screen.getByText(/Keine automatische Bewertung – Confluence-Importe/)).toBeTruthy();
});

it('permits explicitly confirmed grade C without rewriting its quality', async () => {
  mockDocument({ quality_grade: 'C', quality_recommendation: 'block', can_release: true });
  render(<ReviewDocument id="doc" />);
  const checkbox = await screen.findByRole('checkbox', { name: /trotz Qualitätsstufe C/ });
  const button = screen.getByRole('button', { name: 'Geprüften Stand freigeben' });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(checkbox);
  api.mockResolvedValueOnce({ id: 'release', created_at: content.created_at, status: 'pending' });
  fireEvent.click(button);
  await screen.findByText('Freigabe gespeichert');
  const mutation = api.mock.calls.find(([path]) => path.endsWith('/release'));
  expect(JSON.parse(mutation?.[1]?.body as string)).toEqual({ markdown_sha256: content.markdown_sha256, accept_quality_warning: true });
});

it('skips a document and shows it as übersprungen with an unskip button', async () => {
  render(<ReviewDocument id="doc" />);
  const skipButton = await screen.findByRole('button', { name: /Nicht freigeben \/ überspringen/ });
  api.mockResolvedValueOnce({ ...content, review_decision: 'skipped' });
  fireEvent.click(skipButton);
  await screen.findByRole('button', { name: 'Wieder zur Prüfung' });
  expect(screen.getByText('Übersprungen')).toBeTruthy();
  expect(api.mock.calls.some(([path]) => path === '/api/v1/portal/documents/doc/skip')).toBe(true);
});

it('does not offer approval to a reader', async () => {
  mockDocument({ can_release: false, can_reprocess: false });
  render(<ReviewDocument id="doc" />);
  const box = await screen.findByRole('checkbox');
  expect((box as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Geprüften Stand freigeben' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole('button', { name: 'Erneut prüfen' })).toBeNull();
});
it('requires another review after a stale-hash conflict', async () => {
  render(<ReviewDocument id="doc" />);
  await screen.findByRole('checkbox');
  fireEvent.click(screen.getByRole('checkbox'));
  api.mockRejectedValueOnce(new ApiError(409, 'stale hash'));
  fireEvent.click(screen.getByRole('button', { name: 'Geprüften Stand freigeben' }));
  await screen.findByRole('alert');
  await waitFor(() => expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false));
});

it.each(['ppocrv6_medium_structurev3', 'vl:vision'])('reprocesses the reviewed hash with %s and removes the old approval UI', async profileId => {
  render(<ReviewDocument id="doc" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Erneut prüfen' }));
  const select = await screen.findByRole('combobox', { name: 'Neues Verarbeitungsprofil' });
  expect(screen.getByRole('option', { name: 'Standard – schnell' })).toBeTruthy();
  expect(screen.getByRole('option', { name: 'Gründlich – komplexe Dokumente' })).toBeTruthy();
  expect(screen.getByRole('option', { name: 'Dokumenten-KI' })).toBeTruthy();
  expect(screen.queryByRole('option', { name: 'Unconfigured vision' })).toBeNull();
  expect((screen.getByRole('button', { name: 'Neu verarbeiten' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(select, { target: { value: profileId } });
  api.mockResolvedValueOnce({ job_id: 'doc', status: 'queued', profile_id: profileId });
  fireEvent.click(screen.getByRole('button', { name: 'Neu verarbeiten' }));
  const heading = await screen.findByRole('heading', { name: 'Erneute Verarbeitung gestartet' });
  await waitFor(() => expect(document.activeElement).toBe(heading));
  expect(screen.queryByText('Geprüfter Text')).toBeNull();
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(screen.getByRole('link', { name: 'Verarbeitung ansehen' }).getAttribute('href')).toBe('/processing');
  const calls = api.mock.calls.filter(([path]) => path.endsWith('/reprocess'));
  expect(calls).toHaveLength(1);
  expect(JSON.parse(calls[0][1]?.body as string)).toEqual({ profile_id: profileId, markdown_sha256: content.markdown_sha256 });
  expect(api.mock.calls.some(([path]) => path.endsWith('/release'))).toBe(false);
});

it('allows another profile for blocked quality without allowing approval', async () => {
  mockDocument({ can_release: false, quality_grade: 'C', quality_recommendation: 'block' });
  render(<ReviewDocument id="doc" />);
  await screen.findByRole('checkbox');
  expect((screen.getByRole('checkbox') as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Erneut prüfen' }));
  await screen.findByRole('combobox', { name: 'Neues Verarbeitungsprofil' });
  expect(screen.queryByRole('checkbox')).toBeNull();
});

it('clears the approval confirmation when the profile action is cancelled', async () => {
  render(<ReviewDocument id="doc" />);
  fireEvent.click(await screen.findByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Erneut prüfen' }));
  await screen.findByRole('combobox', { name: 'Neues Verarbeitungsprofil' });
  fireEvent.click(screen.getByRole('button', { name: 'Abbrechen' }));
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  expect(api.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

it('keeps issued releases protected even if stale capabilities say reprocessing is possible', async () => {
  mockDocument({ release: { id: 'released', created_at: content.created_at, status: 'sent', error_message: null, released_by: 'anna' } });
  render(<ReviewDocument id="doc" />);
  await screen.findByText('Freigabe gespeichert');
  expect(screen.queryByRole('button', { name: 'Erneut prüfen' })).toBeNull();
});

it('offers indexing diagnostics to admins for released documents', async () => {
  mockDocument({ release: { id: 'released', created_at: content.created_at, status: 'sent', error_message: null, released_by: 'anna' } });
  render(<ReviewDocument id="doc" />);
  const button = await screen.findByRole('button', { name: 'Indizierungsdiagnose herunterladen' });
  fireEvent.click(button);
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/v1/portal/documents/doc/indexing-diagnostics'));
});

it('does not offer indexing diagnostics to regular users', async () => {
  auth.user = { role: 'user' };
  mockDocument({ release: { id: 'released', created_at: content.created_at, status: 'sent', error_message: null, released_by: 'anna' } });
  render(<ReviewDocument id="doc" />);
  await screen.findByText('Freigabe gespeichert');
  expect(screen.queryByRole('button', { name: 'Indizierungsdiagnose herunterladen' })).toBeNull();
  auth.user = { role: 'admin' };
});

it('shows a reprocessing conflict and does not claim the job was started', async () => {
  render(<ReviewDocument id="doc" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Erneut prüfen' }));
  fireEvent.change(await screen.findByRole('combobox', { name: 'Neues Verarbeitungsprofil' }), { target: { value: 'ppocrv6_medium_structurev3' } });
  api.mockRejectedValueOnce(new ApiError(409, 'Preview changed'));
  fireEvent.click(screen.getByRole('button', { name: 'Neu verarbeiten' }));
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain('Bitte lade ihn erneut');
  expect(screen.queryByRole('heading', { name: 'Erneute Verarbeitung gestartet' })).toBeNull();
  expect(screen.queryByRole('checkbox')).toBeNull();
});

it('offers no invented profile when the server reports none', async () => {
  api.mockImplementation(async path => path === '/api/v1/portal/config' ? config
    : path === '/api/v1/paddle/capabilities' ? { profiles: [] } : content);
  render(<ReviewDocument id="doc" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Erneut prüfen' }));
  await screen.findByText(/kein passendes Profil verfügbar/);
  expect(screen.queryByRole('combobox')).toBeNull();
  expect((screen.getByRole('button', { name: 'Neu verarbeiten' }) as HTMLButtonElement).disabled).toBe(true);
});


it('updates a released preview from indexing to ready without reloading its markdown', async () => {
  const release = { id: 'release', created_at: content.created_at, status: 'sent', error_message: null };
  let ready = false;
  api.mockImplementation(async path => path === '/api/v1/portal/config' ? config
    : path.startsWith('/api/v1/portal/indexing-status')
      ? { items: [{ job_id: 'doc', release, indexing: { state: ready ? 'indexed' : 'pending', indexed_at: ready ? '2026-09-02T11:31:12Z' : null, chunk_count: ready ? 2 : 0 } }] }
      : { ...content, release });
  render(<ReviewDocument id="doc" />);
  await screen.findByText(/freigegebenen Inhalte werden für die KI-Suche aufbereitet/);
  ready = true;
  act(() => vi.mocked(useVisiblePolling).mock.calls.at(-1)?.[0]());
  await screen.findByText('Indizierung abgeschlossen');
  expect(screen.getByText('Durchsuchbare Textabschnitte')).toBeTruthy();
  expect(screen.getByText('Geprüfter Text')).toBeTruthy();
  expect(api.mock.calls.filter(([path]) => path === '/api/v1/portal/documents/doc')).toHaveLength(1);
});

it('filters the review inbox by quality grade and resets pagination', async () => {
  const item = { id: 'd1', original_filename: 'Doc.pdf', status: 'FINISHED', collection_id: 'c1', collection_name: 'Service', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, review_decision: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null } };
  api.mockImplementation(async path => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')
    ? { items: [item], total: 25 } : content);
  render(<ReviewInbox />);
  await screen.findByText('Doc.pdf');
  ['Alle', 'A', 'B', 'C', 'Ohne Bewertung'].forEach(label => expect(screen.getByRole('button', { name: label })).toBeTruthy());

  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
  await waitFor(() => {
    const call = api.mock.calls.filter(([path]) => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')).at(-1);
    expect(new URL(call?.[0] as string, 'http://localhost').searchParams.get('offset')).toBe('20');
  });

  fireEvent.click(screen.getByRole('button', { name: 'A' }));
  expect(screen.getByRole('button', { name: 'A' }).getAttribute('aria-pressed')).toBe('true');
  await waitFor(() => {
    const call = api.mock.calls.filter(([path]) => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')).at(-1);
    const params = new URL(call?.[0] as string, 'http://localhost').searchParams;
    expect(params.get('quality_grade')).toBe('A');
    expect(params.get('offset')).toBe('0');
  });
});

it('offers the three review-state filters and requests review_state', async () => {
  const item = { id: 'd1', original_filename: 'Doc.pdf', status: 'FINISHED', collection_id: 'c1', collection_name: 'Service', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, review_decision: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null } };
  api.mockImplementation(async path => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')
    ? { items: [item], total: 1 } : content);
  render(<ReviewInbox />);
  await screen.findByText('Doc.pdf');
  ['Zur Prüfung', 'Alle Dokumente', 'Übersprungen'].forEach(label => expect(screen.getByRole('button', { name: label })).toBeTruthy());

  fireEvent.click(screen.getByRole('button', { name: 'Übersprungen' }));
  await waitFor(() => {
    const call = api.mock.calls.filter(([path]) => typeof path === 'string' && path.startsWith('/api/v1/portal/documents')).at(-1);
    expect(new URL(call?.[0] as string, 'http://localhost').searchParams.get('review_state')).toBe('skipped');
  });
});

it('selects documents and releases them in bulk', async () => {
  const item = { id: 'd1', original_filename: 'Doc.pdf', status: 'FINISHED', collection_id: 'c1', collection_name: 'Service', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, review_decision: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null } };
  api.mockImplementation(async path => {
    if (typeof path === 'string' && path === '/api/v1/portal/documents/bulk') return { done: 1, errors: [] };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/documents')) return { items: [item], total: 1 };
    return content;
  });
  render(<ReviewInbox />);
  await screen.findByText('Doc.pdf');
  fireEvent.click(screen.getByRole('checkbox', { name: /Doc\.pdf auswählen/ }));
  expect(await screen.findByText('1 ausgewählt')).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich habe die Inhalte geprüft/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Freigeben' }));
  await waitFor(() => expect(api.mock.calls.some(([path]) => path === '/api/v1/portal/documents/bulk')).toBe(true));
  const call = api.mock.calls.find(([path]) => path === '/api/v1/portal/documents/bulk');
  expect(JSON.parse(call?.[1]?.body as string)).toEqual({ job_ids: ['d1'], action: 'release', accept_quality_warnings: false });
});

it('resets the bulk release confirmation after a batch so a new selection needs re-confirming', async () => {
  const item1 = { id: 'd1', original_filename: 'Doc1.pdf', status: 'FINISHED', collection_id: 'c1', collection_name: 'Service', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, review_decision: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null } };
  const item2 = { id: 'd2', original_filename: 'Doc2.pdf', status: 'FINISHED', collection_id: 'c1', collection_name: 'Service', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null, review_decision: null, source: { kind: 'upload', label: 'Hochgeladen', path: null, url: null } };
  api.mockImplementation(async path => {
    if (typeof path === 'string' && path === '/api/v1/portal/documents/bulk') return { done: 1, errors: [] };
    if (typeof path === 'string' && path.startsWith('/api/v1/portal/documents')) return { items: [item1, item2], total: 2 };
    return content;
  });
  render(<ReviewInbox />);
  await screen.findByText('Doc1.pdf');
  fireEvent.click(screen.getByRole('checkbox', { name: /Doc1\.pdf auswählen/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich habe die Inhalte geprüft/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Freigeben' }));
  await waitFor(() => expect(api.mock.calls.some(([path]) => path === '/api/v1/portal/documents/bulk')).toBe(true));
  await waitFor(() => expect(screen.queryByText('1 ausgewählt')).toBeNull());

  fireEvent.click(screen.getByRole('checkbox', { name: /Doc2\.pdf auswählen/ }));
  await screen.findByText('1 ausgewählt');
  const confirmCheckbox = screen.getByRole('checkbox', { name: /Ich habe die Inhalte geprüft/ }) as HTMLInputElement;
  expect(confirmCheckbox.checked).toBe(false);
  expect((screen.getByRole('button', { name: 'Freigeben' }) as HTMLButtonElement).disabled).toBe(true);
});
