// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiFetch, apiJson } from '@/lib/api';
import { BotImportDialog } from './bot-import-dialog';
import { BotsTab } from './bots-tab';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
}));

const json = vi.mocked(apiJson);
const fetcher = vi.mocked(apiFetch);

const listedBot = {
  id: 'legal-support', name: 'Legal Support', description: null, enabled: true, webhook_url: '', streaming: false,
  has_auth_token: false, timeout_seconds: 120, public: true, grants: [], collections: [], require_sources: true,
  no_context_reply: 'Keine Belege.', created_at: '2026-09-04T08:00:00Z', updated_at: '2026-09-04T08:00:00Z',
  source: 'runtime', editable: true,
};

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body, headers: new Headers() } as unknown as Response;
}

beforeEach(() => {
  json.mockReset();
  fetcher.mockReset();
  json.mockImplementation(async (path) => {
    if (path === '/api/v1/auth/admin/bots') return { items: [listedBot] };
    if (path === '/api/v1/auth/admin/teams') return { items: [] };
    if (path === '/api/v1/collections') return { items: [] };
    if (path === '/api/v1/auth/admin/chat-provider/endpoints') return { items: [] };
    throw new Error(`unexpected request: ${path}`);
  });
});

afterEach(cleanup);

it('exports a bot (also a Runtime YAML bot) from the row action', async () => {
  const blob = new Blob(['schema_version: 1\n'], { type: 'application/yaml' });
  fetcher.mockResolvedValue({
    ok: true, status: 200, blob: async () => blob,
    headers: new Headers({ 'content-disposition': 'attachment; filename="legal-support.bot.yaml"' }),
  } as unknown as Response);
  window.URL.createObjectURL = vi.fn(() => 'blob:export');
  window.URL.revokeObjectURL = vi.fn();
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

  render(<BotsTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Legal Support als Bot-Dokument exportieren' }));

  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/v1/auth/admin/bots/legal-support/export?format=yaml'));
  await waitFor(() => expect(click).toHaveBeenCalled());
  click.mockRestore();
});

it('shows the dry-run report and only then imports with dry_run=false', async () => {
  const dryRun = {
    schema_version: 1, bot_id: 'service-bot', name: 'Service', kind: 'n8n', conflict: null, action: 'create',
    warnings: [{ code: 'missing_secret', detail: 'n8n.auth_token enthält den Platzhalter ${WEAVE_SECRET}.' }],
    errors: [], applied: false,
  };
  fetcher.mockResolvedValueOnce(jsonResponse(dryRun)).mockResolvedValueOnce(jsonResponse({ ...dryRun, applied: true }));
  const onImported = vi.fn(async () => undefined);
  const onClose = vi.fn();

  render(<BotImportDialog onClose={onClose} onImported={onImported} />);
  const confirm = () => screen.queryByRole('button', { name: 'Importieren' });
  expect(confirm()).toBeNull();
  const file = new File(['schema_version: 1\n'], 'service-bot.bot.yaml', { type: 'application/yaml' });
  fireEvent.change(screen.getByLabelText(/Bot-Dokument \(YAML oder JSON\)/), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('switch', { name: 'Bestehenden Bot überschreiben' }));
  fireEvent.click(screen.getByRole('button', { name: 'Prüfen' }));

  expect(await screen.findByText('Neu anlegen')).toBeTruthy();
  expect(screen.getByText(/Secret fehlt/).textContent).toContain('Secret fehlt');
  expect(screen.getByText(/Platzhalter/)).toBeTruthy();
  const first = fetcher.mock.calls[0];
  expect(first[0]).toBe('/api/v1/auth/admin/bots/import');
  const firstBody = first[1]?.body as FormData;
  expect(firstBody.get('dry_run')).toBe('true');
  expect(firstBody.get('on_conflict')).toBe('overwrite');
  expect((firstBody.get('file') as File).name).toBe('service-bot.bot.yaml');
  expect(onImported).not.toHaveBeenCalled();

  fireEvent.click(confirm() as HTMLElement);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect((fetcher.mock.calls[1][1]?.body as FormData).get('dry_run')).toBe('false');
  await waitFor(() => expect(onImported).toHaveBeenCalled());
  expect(await screen.findByText('Übernommen')).toBeTruthy();
  expect(confirm()).toBeNull();
});

it('keeps a blocked report without an import button', async () => {
  fetcher.mockResolvedValueOnce(jsonResponse({
    schema_version: 1, bot_id: 'service-bot', name: 'Service', kind: 'n8n', conflict: 'managed', action: 'blocked',
    warnings: [], errors: ['Ein verwalteter Bot mit der ID \'service-bot\' existiert bereits.'], applied: false,
  }));
  render(<BotImportDialog onClose={vi.fn()} onImported={vi.fn(async () => undefined)} />);
  const file = new File(['schema_version: 1\n'], 'service-bot.bot.yaml', { type: 'application/yaml' });
  fireEvent.change(screen.getByLabelText(/Bot-Dokument \(YAML oder JSON\)/), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('button', { name: 'Prüfen' }));

  expect(await screen.findByText('Blockiert')).toBeTruthy();
  expect(screen.getByRole('alert').textContent).toContain('existiert bereits');
  expect(screen.queryByRole('button', { name: 'Importieren' })).toBeNull();
});
