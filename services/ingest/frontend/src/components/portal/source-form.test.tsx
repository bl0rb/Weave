// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import { SourceForm } from './source-form';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);
const space = { collection_id: 'area', name: 'Service', read_teams: ['Service'], can_manage: true };
beforeEach(() => {
  api.mockReset();
  api.mockImplementation(async path => path === '/api/v1/collections' ? { items: [space] }
    : path === '/api/v1/paddle/settings' ? { default_profile: 'ppocrv6_tiny' }
    : path === '/api/v1/paddle/capabilities' ? { profiles: [
      { value: 'ppocrv6_tiny_structurev3', label: 'Tiny', description: 'Tiny', kind: 'ocr' },
      { value: 'ppocrv6_medium_structurev3', label: 'Medium', description: 'Medium', kind: 'ocr' },
      { value: 'ppocrv6_small', label: 'Small', description: 'Hidden here', kind: 'ocr' },
      { value: 'vl:vision', label: 'VL: Vision Team', description: 'vision-model — vision-language connection', kind: 'vl' },
    ] }
    : path.endsWith('/upload') ? { job_id: 'job' } : {});
});
afterEach(cleanup);

async function upload() {
  const { container } = render(<SourceForm initialCollection="area" />);
  await screen.findByRole('button', { name: 'Hochladen und verarbeiten' });
  await screen.findByRole('option', { name: 'Standard – schnell' });
  const file = new File(['From: a@example.com\n\nTest'], 'nachricht.eml', { type: 'message/rfc822' });
  const picker = container.querySelector('input[type="file"]') as HTMLInputElement;
  expect(picker.accept).toContain('.eml');
  fireEvent.change(picker, { target: { files: [file] } });
  // jsdom cannot populate the native file-picker validity state from a FileList.
  fireEvent.submit(screen.getByRole('button', { name: 'Hochladen und verarbeiten' }).closest('form')!);
  return container;
}

it('replaces the upload form with next steps and keeps the selected area for more sources', async () => {
  const container = await upload();
  await screen.findByRole('heading', { name: 'Upload abgeschlossen' });
  expect(container.querySelector('input[type="file"]')).toBeNull();
  expect(screen.getByText(/einige Minuten dauern/)).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Verarbeitung ansehen' }).getAttribute('href')).toBe('/processing');
  const restart = api.mock.calls.find(([path]) => path === '/api/v1/jobs/job/restart');
  expect(JSON.parse(restart?.[1]?.body as string).profile_id).toBe('ppocrv6_tiny_structurev3');
  expect(api.mock.calls.some(([path]) => path.includes('/mail/'))).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Weitere Quellen hinzufügen' }));
  expect((screen.getByRole('combobox', { name: 'Wohin gehört die Quelle?' }) as HTMLSelectElement).value).toBe('area');
  expect(container.querySelector('input[type="file"]')).toBeTruthy();
  expect(screen.queryByRole('heading', { name: 'Upload abgeschlossen' })).toBeNull();
});

it('keeps a failed start actionable instead of reporting upload success', async () => {
  const normal = api.getMockImplementation()!;
  api.mockImplementation(async (path, options) => {
    if (path.endsWith('/restart')) throw new ApiError(503, 'worker unavailable');
    return normal(path, options);
  });
  await upload();
  await screen.findByRole('heading', { name: 'Die Verarbeitung konnte nicht gestartet werden' });
  expect(screen.getByRole('link', { name: 'Auftrag prüfen' }).getAttribute('href')).toBe('/jobs/job');
  expect(screen.queryByRole('heading', { name: 'Upload abgeschlossen' })).toBeNull();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Weitere Quellen hinzufügen' })).toBeTruthy());
});

it('offers the two structure presets and configured vision models, forwarding the selected model', async () => {
  const { container } = render(<SourceForm initialCollection="area" />);
  await screen.findByRole('option', { name: 'Vision Team' });
  expect(screen.getByRole('option', { name: 'Standard – schnell' })).toBeTruthy();
  expect(screen.getByRole('option', { name: 'Gründlich – komplexe Dokumente' })).toBeTruthy();
  expect(screen.queryByRole('option', { name: 'Small' })).toBeNull();
  fireEvent.change(screen.getByRole('combobox', { name: 'Wie sollen die Dokumente aufbereitet werden?' }), { target: { value: 'vl:vision' } });
  fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [new File(['pdf'], 'Datei.pdf', { type: 'application/pdf' })] } });
  fireEvent.submit(screen.getByRole('button', { name: 'Hochladen und verarbeiten' }).closest('form')!);
  await screen.findByRole('heading', { name: 'Upload abgeschlossen' });
  const restart = api.mock.calls.find(([path]) => path === '/api/v1/jobs/job/restart');
  expect(JSON.parse(restart?.[1]?.body as string).profile_id).toBe('vl:vision');
});

it('applies the selected profile to Confluence attachments, not to the page text', async () => {
  const normal = api.getMockImplementation()!;
  api.mockImplementation(async (path, options) => {
    if (path === '/api/v1/import/sources') return { items: [{ id: 'wiki', name: 'Firmenwiki' }] };
    if (path === '/api/v1/import/runs') return { id: 'run', status: 'pending' };
    return normal(path, options);
  });
  render(<SourceForm initialCollection="area" />);
  await screen.findByRole('option', { name: 'Gründlich – komplexe Dokumente' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Wie sollen die Dokumente aufbereitet werden?' }), { target: { value: 'ppocrv6_medium_structurev3' } });
  fireEvent.click(screen.getByRole('radio', { name: /Confluence verbinden/ }));
  await screen.findByRole('option', { name: 'Firmenwiki' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Deine Confluence-Verbindung' }), { target: { value: 'wiki' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Confluence-Seite' }), { target: { value: 'https://wiki.example/page' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich prüfe die Berechtigten/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Import starten' }));
  await screen.findByRole('heading', { name: 'Confluence-Import gestartet' });
  const mutation = api.mock.calls.find(([path]) => path === '/api/v1/import/runs');
  expect(JSON.parse(mutation?.[1]?.body as string).options).toEqual({ collection_id: 'area', include_attachments: true, ocr_attachments: true, ocr_profile_id: 'ppocrv6_medium_structurev3' });
});
