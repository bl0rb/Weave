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
  expect((screen.getByRole('combobox', { name: 'Bot-Typ' }) as HTMLSelectElement).value).toBe('llm');
  expect(screen.getByRole('textbox', { name: 'System-Prompt' })).toBeTruthy();
});

it('creates a scoped n8n bot and keeps pending bearer delivery disabled', async () => {
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
  expect(streaming.disabled).toBe(false);
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

it('shows the agent-mode toggle only for LLM bots, off by default', async () => {
  render(<BotsTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Bot hinzufügen' }));

  const agentToggle = screen.getByRole('switch', { name: /Agentenmodus/ }) as HTMLButtonElement;
  expect(agentToggle.getAttribute('aria-checked')).toBe('false');
  expect(screen.queryByRole('button', { name: 'Subagent hinzufügen' })).toBeNull();

  fireEvent.change(screen.getByRole('combobox', { name: 'Bot-Typ' }), { target: { value: 'n8n' } });
  expect(screen.queryByRole('switch', { name: /Agentenmodus/ })).toBeNull();
});

it('blocks saving an enabled agent mode with no subagent, then allows it once one is added and filled in', async () => {
  render(<BotsTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Bot hinzufügen' }));

  fireEvent.change(screen.getByRole('textbox', { name: /^Bot-ID/ }), { target: { value: 'agent-bot' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Anzeigename' }), { target: { value: 'Agent Bot' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'System-Prompt' }), { target: { value: 'Antworte anhand der Recherche.' } });

  fireEvent.click(screen.getByRole('switch', { name: /Agentenmodus/ }));
  expect(screen.getByText(/Mindestens ein Subagent ist erforderlich/)).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Bot speichern' }) as HTMLButtonElement).disabled).toBe(true);

  fireEvent.click(screen.getByRole('button', { name: 'Subagent hinzufügen' }));
  expect(screen.getByText(/Der fachliche Auftrag darf nicht leer sein/)).toBeTruthy();
  expect(screen.getByText(/Mindestens ein Wissensbereich ist erforderlich/)).toBeTruthy();

  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'IT Support' } });
  expect((screen.getByRole('textbox', { name: /^ID \(Slug\)/ }) as HTMLInputElement).value).toBe('it-support');

  fireEvent.change(screen.getByRole('textbox', { name: 'Fachlicher Auftrag' }), { target: { value: 'Beantwortet IT-Fragen.' } });
  // Two "servicewissen" checkboxes exist -- one for the subagent's own
  // Collections picker (inside the agent section, first in DOM order) and
  // one for the bot-level "Wissensbereiche" scope further down.
  fireEvent.click(screen.getAllByRole('checkbox', { name: 'servicewissen' })[0]);

  expect(screen.queryByRole('alert')).toBeNull();
  expect((screen.getByRole('button', { name: 'Bot speichern' }) as HTMLButtonElement).disabled).toBe(false);

  fireEvent.click(screen.getByRole('button', { name: 'Bot speichern' }));
  await waitFor(() => expect(json.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
  const mutation = json.mock.calls.find(([, init]) => init?.method === 'POST');
  const body = JSON.parse(mutation?.[1]?.body as string);
  expect(body.agent).toEqual({
    enabled: true,
    subagents: [{
      id: 'it-support', name: 'IT Support', description: null, mission: 'Beantwortet IT-Fragen.',
      collections: ['servicewissen'], filters: {}, include_uncollected: false, model: null,
      limits: { max_searches: 3, max_results: 5, timeout_seconds: 60 },
    }],
    limits: { max_parallel: 3, max_followups: 1, budget_searches: 9, timeout_seconds: 120 },
  });
});

it('sends agent: null when the agent-mode toggle stays off', async () => {
  render(<BotsTab />);
  fireEvent.click(await screen.findByRole('button', { name: 'Bot hinzufügen' }));

  fireEvent.change(screen.getByRole('textbox', { name: /^Bot-ID/ }), { target: { value: 'plain-bot' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Anzeigename' }), { target: { value: 'Plain Bot' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'System-Prompt' }), { target: { value: 'Antworte normal.' } });

  fireEvent.click(screen.getByRole('button', { name: 'Bot speichern' }));
  await waitFor(() => expect(json.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
  const mutation = json.mock.calls.find(([, init]) => init?.method === 'POST');
  const body = JSON.parse(mutation?.[1]?.body as string);
  expect(body.agent).toBeNull();
});

it('explains that bot scope narrows rather than grants document access', async () => {
  render(<BotsTab />);
  expect(await screen.findByText(/Schnittmenge aus Bot-Auswahl und Nutzerrechten/)).toBeTruthy();
  expect(screen.getByText(/Die Bearer-Weitergabe wird erst/)).toBeTruthy();
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

it('allows deleting a bundled Runtime bot after confirmation', async () => {
  json.mockImplementation(async path => {
    if (path === '/api/v1/auth/admin/bots') return { items: [{ ...savedBot, source: 'runtime', editable: true }] };
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
