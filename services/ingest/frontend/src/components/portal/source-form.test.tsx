// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import { SourceForm } from './source-form';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);
const space = { collection_id: 'area', name: 'Service', visibility: 'restricted', grants: [], can_manage: true };
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
  expect(screen.getByRole('link', { name: 'Zum Wissensbereich' }).getAttribute('href')).toBe('/knowledge/area');
  expect(screen.getByRole('link', { name: 'Verarbeitung ansehen' }).getAttribute('href')).toBe('/documents?stand=processing');
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
    if (path === '/api/v1/import/sources') return { items: [{ id: 'wiki', name: 'Firmenwiki', server_kind: 'server' }] };
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
  expect(JSON.parse(mutation?.[1]?.body as string).sync).toEqual({ enabled: true, interval_seconds: 86400 });
});

it('starts the Confluence setup right away when Confluence is chosen without a connection, then selects it', async () => {
  const normal = api.getMockImplementation()!;
  api.mockImplementation(async (path, options) => {
    if (path === '/api/v1/import/sources' && options?.method === 'POST') return { id: 'new-source', name: 'Mein Wiki', server_kind: '' };
    if (path === '/api/v1/import/sources/new-source/test') return { ok: true, detail: null, server_kind: 'server' };
    if (path === '/api/v1/import/sources') return { items: [] };
    return normal(path, options);
  });
  render(<SourceForm initialCollection="area" initialKind="confluence" />);
  await screen.findByRole('heading', { name: 'Confluence-Verbindung hinzufügen' });
  expect(screen.getByText(/noch keine Confluence-Verbindung/)).toBeTruthy();
  fireEvent.change(screen.getByRole('textbox', { name: 'Basis-URL' }), { target: { value: 'https://confluence.example.com' } });
  fireEvent.change(screen.getByPlaceholderText('Confluence-PAT'), { target: { value: 'pat-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Verbindung hinzufügen' }));
  await waitFor(() => expect((screen.getByRole('combobox', { name: 'Deine Confluence-Verbindung' }) as HTMLSelectElement).value).toBe('new-source'));
  expect(screen.queryByRole('heading', { name: 'Confluence-Verbindung hinzufügen' })).toBeNull();
});

it('keeps a failed connection test visible, retries it and blocks the import while the connection is untested', async () => {
  const normal = api.getMockImplementation()!;
  let ok = false;
  api.mockImplementation(async (path, options) => {
    if (path === '/api/v1/import/sources' && options?.method === 'POST') return { id: 'new-source', name: 'Mein Wiki', server_kind: '' };
    if (path === '/api/v1/import/sources/new-source/test') return ok ? { ok: true, detail: null, server_kind: 'server' } : { ok: false, detail: 'HTTP 401', server_kind: null };
    if (path === '/api/v1/import/sources') return { items: [] };
    return normal(path, options);
  });
  render(<SourceForm initialCollection="area" initialKind="confluence" />);
  await screen.findByRole('heading', { name: 'Confluence-Verbindung hinzufügen' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Basis-URL' }), { target: { value: 'https://confluence.example.com' } });
  fireEvent.change(screen.getByPlaceholderText('Confluence-PAT'), { target: { value: 'pat-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Verbindung hinzufügen' }));
  await screen.findByText(/Verbindung fehlgeschlagen: HTTP 401/);
  expect(api.mock.calls.filter(([path, o]) => path === '/api/v1/import/sources' && o?.method === 'POST')).toHaveLength(1);
  expect(screen.queryByRole('button', { name: 'Verbindung hinzufügen' })).toBeNull();
  // Keep the untested connection: the form flags it and refuses to start the import.
  fireEvent.click(screen.getByRole('button', { name: 'Ohne Prüfung speichern' }));
  await screen.findByText(/noch nicht erfolgreich geprüft/);
  expect((screen.getByRole('button', { name: 'Import starten' }) as HTMLButtonElement).disabled).toBe(true);
  ok = true;
  fireEvent.click(screen.getByRole('button', { name: 'Verbindung prüfen' }));
  await waitFor(() => expect(screen.queryByText(/noch nicht erfolgreich geprüft/)).toBeNull());
});

it('hands an already created connection to the form even when the dialog is simply closed', async () => {
  const normal = api.getMockImplementation()!;
  api.mockImplementation(async (path, options) => {
    if (path === '/api/v1/import/sources' && options?.method === 'POST') return { id: 'new-source', name: 'Mein Wiki', server_kind: '' };
    if (path === '/api/v1/import/sources/new-source/test') return { ok: false, detail: 'HTTP 401', server_kind: null };
    if (path === '/api/v1/import/sources') return { items: [] };
    return normal(path, options);
  });
  render(<SourceForm initialCollection="area" initialKind="confluence" />);
  await screen.findByRole('heading', { name: 'Confluence-Verbindung hinzufügen' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Basis-URL' }), { target: { value: 'https://confluence.example.com' } });
  fireEvent.change(screen.getByPlaceholderText('Confluence-PAT'), { target: { value: 'pat-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Verbindung hinzufügen' }));
  await screen.findByText(/Verbindung fehlgeschlagen: HTTP 401/);
  fireEvent.click(screen.getByRole('button', { name: 'Dialog schließen' }));
  await waitFor(() => expect((screen.getByRole('combobox', { name: 'Deine Confluence-Verbindung' }) as HTMLSelectElement).value).toBe('new-source'));
  expect(screen.queryByRole('heading', { name: 'Confluence-Verbindung hinzufügen' })).toBeNull();
});

it('imports a catalog entry: creates and tests a credential-less website connection, then starts a url-scoped run', async () => {
  const normal = api.getMockImplementation()!;
  const entry = { id: 'cat-1', title: 'VwVfG', description: 'Verwaltungsverfahrensgesetz', category: 'legal', publisher: 'BMJ', kind: 'website', start_url: 'https://www.gesetze-im-internet.de/vwvfg/', crawl_scope: 'prefix', include_linked_documents: false, max_pages: 150, interval_seconds: 604800, legal_source: true, host: 'www.gesetze-im-internet.de', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' };
  api.mockImplementation(async (path, options) => {
    if (path.startsWith('/api/v1/source-catalog')) return { items: [entry], can_manage: false };
    if (path === '/api/v1/import/sources' && options?.method === 'POST') return { id: 'web-1', name: 'www.gesetze-im-internet.de', kind: 'website', base_url: 'https://www.gesetze-im-internet.de', server_kind: '', auth_type: 'none' };
    if (path === '/api/v1/import/sources') return { items: [{ id: 'wiki', name: 'Firmenwiki', kind: 'confluence', server_kind: 'server' }] };
    if (path === '/api/v1/import/sources/web-1/test') return { ok: true, detail: 'Reachable (HTTP 200)', server_kind: 'website' };
    if (path === '/api/v1/import/runs') return { id: 'run-1', kind: 'website', status: 'pending', scope_type: 'url', scope_value: entry.start_url };
    return normal(path, options);
  });
  render(<SourceForm initialCollection="area" initialKind="catalog" initialCatalogEntryId="cat-1" />);
  await screen.findByRole('option', { name: 'Standard – schnell' });
  await screen.findByText(/wird beim Start automatisch eine Website-Verbindung/);
  // The entry's recommended scope and options are prefilled.
  expect(screen.getByRole('button', { name: 'Dieser Bereich (Unterpfad)' }).getAttribute('aria-pressed')).toBe('true');
  expect((screen.getByRole('checkbox', { name: /Rechtsquelle/ }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Nur diese Seite' }));
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich prüfe die Berechtigten/ }));
  fireEvent.submit(screen.getByRole('button', { name: 'Import starten' }).closest('form')!);
  await screen.findByRole('heading', { name: 'Website-Import gestartet' });
  const created = api.mock.calls.find(([path, init]) => path === '/api/v1/import/sources' && init?.method === 'POST');
  expect(JSON.parse(created?.[1]?.body as string)).toMatchObject({ kind: 'website', base_url: 'https://www.gesetze-im-internet.de', auth_type: 'none' });
  expect(api.mock.calls.some(([path]) => path === '/api/v1/import/sources/web-1/test')).toBe(true);
  const run = api.mock.calls.find(([path]) => path === '/api/v1/import/runs');
  expect(JSON.parse(run?.[1]?.body as string)).toEqual({
    source_id: 'web-1',
    scope: { type: 'url', value: entry.start_url },
    options: { collection_id: 'area', include_attachments: false, ocr_attachments: false, ocr_profile_id: 'ppocrv6_tiny_structurev3', crawl_scope: 'page', legal_source: true, catalog_entry_id: 'cat-1', max_pages: 150 },
    sync: { enabled: true, interval_seconds: 604800 },
  });
});

it('imports a website through an existing tested connection with the chosen scope and refresh', async () => {
  const normal = api.getMockImplementation()!;
  api.mockImplementation(async (path, options) => {
    if (path === '/api/v1/import/sources') return { items: [{ id: 'wiki', name: 'Firmenwiki', kind: 'confluence', server_kind: 'server' }, { id: 'intra', name: 'Intranet', kind: 'website', base_url: 'https://intranet.example.com', server_kind: 'website', auth_type: 'none' }] };
    if (path === '/api/v1/import/runs') return { id: 'run-2', kind: 'website', status: 'pending', scope_type: 'url', scope_value: 'https://intranet.example.com/handbuch/' };
    return normal(path, options);
  });
  render(<SourceForm initialCollection="area" initialKind="website" initialSourceId="intra" />);
  await screen.findByRole('option', { name: 'Standard – schnell' });
  const connection = await screen.findByRole('combobox', { name: 'Deine Website-Verbindung' }) as HTMLSelectElement;
  expect(connection.value).toBe('intra');
  expect(screen.queryByRole('option', { name: 'Firmenwiki' })).toBeNull();
  fireEvent.change(screen.getByRole('textbox', { name: 'Startadresse' }), { target: { value: 'https://intranet.example.com/handbuch/' } });
  fireEvent.click(screen.getByRole('button', { name: 'Gesamte Website' }));
  fireEvent.click(screen.getByRole('checkbox', { name: /Verlinkte Dokumente/ }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Aktualisierungsrhythmus' }), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich prüfe die Berechtigten/ }));
  fireEvent.submit(screen.getByRole('button', { name: 'Import starten' }).closest('form')!);
  await screen.findByRole('heading', { name: 'Website-Import gestartet' });
  const run = api.mock.calls.find(([path]) => path === '/api/v1/import/runs');
  expect(JSON.parse(run?.[1]?.body as string)).toEqual({
    source_id: 'intra',
    scope: { type: 'url', value: 'https://intranet.example.com/handbuch/' },
    options: { collection_id: 'area', include_attachments: true, ocr_attachments: true, ocr_profile_id: 'ppocrv6_tiny_structurev3', crawl_scope: 'host', legal_source: false, catalog_entry_id: null },
    sync: { enabled: false, interval_seconds: null },
  });
});

it('does not carry a Confluence connection over to the website form', async () => {
  sessionStorage.clear();
  const normal = api.getMockImplementation()!;
  api.mockImplementation(async (path, options) => {
    if (path === '/api/v1/import/sources') return { items: [{ id: 'wiki', name: 'Firmenwiki', kind: 'confluence', server_kind: '' }, { id: 'intra', name: 'Intranet', kind: 'website', base_url: 'https://intranet.example.com', server_kind: 'website', auth_type: 'none' }] };
    return normal(path, options);
  });
  render(<SourceForm initialCollection="area" initialKind="confluence" initialSourceId="wiki" />);
  await screen.findByText(/noch nicht erfolgreich geprüft/);
  fireEvent.click(screen.getByRole('radio', { name: /Website/ }));
  await screen.findByRole('option', { name: /Intranet/ });
  expect(screen.queryByText(/noch nicht erfolgreich geprüft/)).toBeNull();
  fireEvent.change(screen.getByRole('textbox', { name: 'Startadresse' }), { target: { value: 'https://intranet.example.com/a/' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich prüfe die Berechtigten/ }));
  expect((screen.getByRole('button', { name: 'Import starten' }) as HTMLButtonElement).disabled).toBe(true);
});

it('does not send a catalog entry\'s page limit with a plain website import', async () => {
  sessionStorage.clear();
  const normal = api.getMockImplementation()!;
  const entry = { id: 'cat-1', title: 'VwVfG', description: '', category: 'legal', publisher: 'BMJ', kind: 'website', start_url: 'https://www.gesetze-im-internet.de/vwvfg/', crawl_scope: 'prefix', include_linked_documents: false, max_pages: 150, interval_seconds: 604800, legal_source: true, host: 'www.gesetze-im-internet.de', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' };
  api.mockImplementation(async (path, options) => {
    if (path.startsWith('/api/v1/source-catalog')) return { items: [entry], can_manage: false };
    if (path === '/api/v1/import/sources') return { items: [{ id: 'intra', name: 'Intranet', kind: 'website', base_url: 'https://intranet.example.com', server_kind: 'website', auth_type: 'none' }] };
    if (path === '/api/v1/import/runs') return { id: 'run-3', kind: 'website', status: 'pending', scope_type: 'url', scope_value: 'https://intranet.example.com/a/' };
    return normal(path, options);
  });
  render(<SourceForm initialCollection="area" initialKind="catalog" initialCatalogEntryId="cat-1" />);
  await screen.findByRole('option', { name: 'Standard – schnell' });
  await screen.findByText(/Verbindung: Intranet|wird beim Start automatisch/);
  fireEvent.click(screen.getByRole('radio', { name: /Website/ }));
  fireEvent.change(await screen.findByRole('combobox', { name: 'Deine Website-Verbindung' }), { target: { value: 'intra' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Startadresse' }), { target: { value: 'https://intranet.example.com/a/' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /Ich prüfe die Berechtigten/ }));
  fireEvent.submit(screen.getByRole('button', { name: 'Import starten' }).closest('form')!);
  await screen.findByRole('heading', { name: 'Website-Import gestartet' });
  const run = api.mock.calls.find(([path]) => path === '/api/v1/import/runs');
  expect(JSON.parse(run?.[1]?.body as string).options).not.toHaveProperty('max_pages');
});
