// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiFetch, apiJson } from '@/lib/api';
import { ReviewDocument } from './reviews';
import { useVisiblePolling } from '@/lib/data-cache';

const push = vi.fn();
const refresh = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push, refresh }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiFetch: vi.fn(), apiJson: vi.fn() }));
vi.mock('@/lib/data-cache', () => ({ useVisiblePolling: vi.fn() }));
vi.mock('@/components/markdown/markdown-view', () => ({ MarkdownView: ({ markdown }: { markdown: string }) => <div>{markdown}</div> }));
const content = { id: 'doc', original_filename: 'Regelwerk.pdf', status: 'FINISHED', collection_id: 'area', collection_name: 'Service', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, can_reprocess: true, profile_id: 'ppocrv6_tiny_structurev3', release: null, markdown: 'Geprüfter Text', markdown_sha256: 'a'.repeat(64) };
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
  fetcher.mockResolvedValue({ ok: true } as Response);
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
  fireEvent.click(screen.getByRole('checkbox'));
  expect((button as HTMLButtonElement).disabled).toBe(false);
  api.mockResolvedValueOnce({ id: 'release', created_at: content.created_at, status: 'pending', error_message: null });
  fireEvent.click(button);
  await screen.findByText('Freigabe gespeichert');
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

it('does not offer approval to a reader', async () => {
  mockDocument({ can_release: false, can_reprocess: false });
  render(<ReviewDocument id="doc" />);
  const box = await screen.findByRole('checkbox');
  expect((box as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Geprüften Stand freigeben' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole('button', { name: 'Mit anderem Profil neu verarbeiten' })).toBeNull();
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
  fireEvent.click(await screen.findByRole('button', { name: 'Mit anderem Profil neu verarbeiten' }));
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
  fireEvent.click(screen.getByRole('button', { name: 'Mit anderem Profil neu verarbeiten' }));
  await screen.findByRole('combobox', { name: 'Neues Verarbeitungsprofil' });
  expect(screen.queryByRole('checkbox')).toBeNull();
});

it('clears the approval confirmation when the profile action is cancelled', async () => {
  render(<ReviewDocument id="doc" />);
  fireEvent.click(await screen.findByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Mit anderem Profil neu verarbeiten' }));
  await screen.findByRole('combobox', { name: 'Neues Verarbeitungsprofil' });
  fireEvent.click(screen.getByRole('button', { name: 'Abbrechen' }));
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  expect(api.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

it('keeps issued releases protected even if stale capabilities say reprocessing is possible', async () => {
  mockDocument({ release: { id: 'released', created_at: content.created_at, status: 'sent', error_message: null } });
  render(<ReviewDocument id="doc" />);
  await screen.findByText('Freigabe gespeichert');
  expect(screen.queryByRole('button', { name: 'Mit anderem Profil neu verarbeiten' })).toBeNull();
});

it('shows a reprocessing conflict and does not claim the job was started', async () => {
  render(<ReviewDocument id="doc" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Mit anderem Profil neu verarbeiten' }));
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
  fireEvent.click(await screen.findByRole('button', { name: 'Mit anderem Profil neu verarbeiten' }));
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
