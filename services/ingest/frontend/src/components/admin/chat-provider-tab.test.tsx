// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import { ChatEndpointsSection, ChatProviderTab } from './chat-provider-tab';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);
const initial = { configured: false, enabled: false, base_url: '', model: '', has_api_key: false, timeout_seconds: 60, temperature: null, supports_tools: false, updated_at: null };

beforeEach(() => { api.mockReset(); api.mockResolvedValue(initial); });
afterEach(cleanup);

it('saves an OpenAI-compatible provider without echoing an existing secret', async () => {
  render(<ChatProviderTab />);
  await screen.findByText('Zentrale Chat-Generierung aktivieren');
  fireEvent.click(screen.getByRole('switch', { name: 'Zentrale Chat-Generierung aktivieren' }));
  fireEvent.change(screen.getByRole('textbox', { name: /^OpenAI-kompatibler Endpoint/ }), { target: { value: 'https://llm.example/v1' } });
  fireEvent.change(screen.getByRole('textbox', { name: /^Modell/ }), { target: { value: 'chat-model' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  const body = JSON.parse(api.mock.calls[1][1]?.body as string);
  expect(body).toMatchObject({ enabled: true, base_url: 'https://llm.example/v1', model: 'chat-model', api_key: null });
});

it('explains that n8n owns the models used inside its flows', async () => {
  render(<ChatProviderTab />);
  expect(await screen.findByText(/n8n-Flows verwalten ihr Modell weiterhin in n8n/)).toBeTruthy();
});

it('sends supports_tools when the tool-calling toggle is switched on', async () => {
  render(<ChatProviderTab />);
  await screen.findByText('Zentrale Chat-Generierung aktivieren');
  fireEvent.click(screen.getByRole('switch', { name: 'Modell unterstützt Tool-Aufrufe (nötig für den Agentenmodus)' }));
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  const body = JSON.parse(api.mock.calls[1][1]?.body as string);
  expect(body).toMatchObject({ supports_tools: true });
});

it('adds a named endpoint and saves it under its own path', async () => {
  api.mockImplementation(async (path, init) => (init?.method === 'PUT' ? { ...initial, id: 'tools-llm', configured: true } : { items: [] }));
  render(<ChatEndpointsSection />);
  fireEvent.change(await screen.findByRole('textbox', { name: /^Neue Endpunkt-ID/ }), { target: { value: 'tools-llm' } });
  fireEvent.click(screen.getByRole('button', { name: 'Endpunkt hinzufügen' }));
  fireEvent.change(screen.getByRole('textbox', { name: /^Anzeigename/ }), { target: { value: 'Tool-Modell' } });
  fireEvent.change(screen.getByRole('textbox', { name: /^OpenAI-kompatibler Endpoint/ }), { target: { value: 'https://tools.example/v1' } });
  fireEvent.change(screen.getByRole('textbox', { name: /^Modell/ }), { target: { value: 'tool-chat' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(api.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(true));
  const [path, init] = api.mock.calls.find(([, call]) => call?.method === 'PUT')!;
  expect(path).toBe('/api/v1/auth/admin/chat-provider/endpoints/tools-llm');
  expect(JSON.parse(init?.body as string)).toMatchObject({ name: 'Tool-Modell', enabled: true, model: 'tool-chat' });
});
