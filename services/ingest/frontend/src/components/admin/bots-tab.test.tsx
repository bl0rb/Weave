// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { apiFetch, apiJson } from '@/lib/api';
import { BotsTab } from './bots-tab';

vi.mock('@/lib/api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
}));

const json = vi.mocked(apiJson);
const fetcher = vi.mocked(apiFetch);
const savedBot = {
  id: 'service-assistent',
  name: 'Service-Assistent',
  description: 'Antwortet über n8n.',
  enabled: true,
  webhook_url: 'https://n8n.example.com/webhook/service',
  streaming: false,
  has_auth_token: false,
  timeout_seconds: 120,
  teams: ['Service'],
  collections: ['servicewissen'],
  require_sources: true,
  no_context_reply: 'Keine Belege.',
  created_at: '2026-09-04T08:00:00Z',
  updated_at: '2026-09-04T08:00:00Z',
};

beforeEach(() => {
  json.mockReset();
  fetcher.mockReset();
  json.mockImplementation(async (path, init) => {
    if (init?.method === 'POST') return savedBot;
    if (path === '/api/v1/auth/admin/bots') return { items: [] };
    if (path === '/api/v1/auth/admin/teams') return { items: [{ id: 'team-1', name: 'Service', created_at: '2026-09-04T08:00:00Z' }] };
    if (path === '/api/v1/collections') return { items: [{ collection_id: 'area-1', slug: 'servicewissen', name: 'Servicewissen', description: null, read_teams: ['Service'], can_manage: true }] };
    throw new Error(`unexpected request: ${path}`);
  });
  fetcher.mockResolvedValue({ ok: true } as Response);
});

afterEach(cleanup);

it('starts new bots as LLM bots and labels the dialog accordingly', async () => {
  render(<BotsTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Bot hinzufügen' }));

  expect(screen.getByRole('heading', { name: 'LLM-Bot hinzufügen' })).toBeTruthy();
  expect(screen.getByRole('combobox', { name: 'Bot-Typ' })).toHaveValue('llm');
  expect(screen.getByRole('textbox', { name: 'System-Prompt' })).toBeTruthy();
});

it('creates a scoped n8n bot and keeps pending delivery options disabled', async () => {
  render(<BotsTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Bot hinzufügen' }));

  fireEvent.change(screen.getByRole('combobox', { name: 'Bot-Typ' }), { target: { value: 'n8n' } });

  fireEvent.change(screen.getByRole('textbox', { name: /^Bot-ID/ }), { target: { value: 'service-assistent' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Anzeigename' }), { target: { value: 'Service-Assistent' } });
  fireEvent.change(screen.getByRole('textbox', { name: /^n8n-Webhook/ }), { target: { value: 'https://n8n.example.com/webhook/service' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Service' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'servicewissen' }));

  const streaming = screen.getByRole('switch', { name: /n8n-Streaming/ }) as HTMLButtonElement;
  const bearer = screen.getByLabelText(/^Bearer-Token/) as HTMLInputElement;
  expect(streaming.disabled).toBe(true);
  expect(bearer.disabled).toBe(true);

  fireEvent.click(screen.getByRole('button', { name: 'Bot speichern' }));
  await waitFor(() => expect(json.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
  const mutation = json.mock.calls.find(([, init]) => init?.method === 'POST');
  const body = JSON.parse(mutation?.[1]?.body as string);
  expect(body.id).toBe('service-assistent');
  expect(body.teams).toEqual(['Service']);
  expect(body.collections).toEqual(['servicewissen']);
  expect(body.streaming).toBe(false);
  expect(body.auth_token).toBeNull();
  expect(await screen.findByText(/Bot-Konfiguration gespeichert/)).toBeTruthy();
});

it('explains that bot scope narrows rather than grants document access', async () => {
  render(<BotsTab />);
  expect(await screen.findByText(/Schnittmenge aus Bot-Auswahl und Nutzerrechten/)).toBeTruthy();
  expect(screen.getByText(/Streaming und Bearer-Weitergabe werden erst/)).toBeTruthy();
});

it('deletes an existing bot only after confirmation', async () => {
  json.mockImplementation(async path => {
    if (path === '/api/v1/auth/admin/bots') return { items: [savedBot] };
    if (path === '/api/v1/auth/admin/teams') return { items: [] };
    if (path === '/api/v1/collections') return { items: [] };
    throw new Error(`unexpected request: ${path}`);
  });
  render(<BotsTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Service-Assistent löschen' }));
  fireEvent.click(screen.getByRole('button', { name: 'Bot löschen' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
    '/api/v1/auth/admin/bots/service-assistent',
    { method: 'DELETE' },
  ));
});
