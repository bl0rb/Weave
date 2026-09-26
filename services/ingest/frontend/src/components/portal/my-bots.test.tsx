// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import { MyBots } from './my-bots';

vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);

const bot = {
  id: 'wissens-bot', kind: 'llm', name: 'Wissens-Bot', description: 'Hilft beim Service.', enabled: true,
  system_prompt: 'Hilf.', retrieval_enabled: true, collections: ['admin-bereich'], require_sources: true,
  no_context_reply: 'Keine Belege.', public: false, updated_at: '2026-09-26T08:00:00Z',
  grants: [
    { user_id: 'u-ada', team_id: null, role: 'owner', name: 'ada' },
    { user_id: null, team_id: 't-service', role: 'user', name: 'Service' },
  ],
};

beforeEach(() => {
  api.mockReset();
  api.mockImplementation(async (path, init) => {
    if (init?.method === 'PATCH') return { ...bot, ...JSON.parse(init.body as string), grants: bot.grants };
    if (path === '/api/v1/bots') return { items: [bot] };
    if (path === '/api/v1/collections') return { items: [{ collection_id: 'c1', slug: 'servicewissen', name: 'Servicewissen', description: 'x', visibility: 'public', grants: [] }] };
    if (path === '/api/v1/directory/teams') return { items: [{ id: 't-service', name: 'Service', member_count: 3 }, { id: 't-legal', name: 'Recht', member_count: 2 }] };
    throw new Error(`unexpected request: ${path}`);
  });
});
afterEach(cleanup);

it('lists owned bots with who may use them', async () => {
  render(<MyBots />);
  expect(await screen.findByRole('heading', { name: 'Wissens-Bot' })).toBeTruthy();
  expect(screen.getByText('Team Service')).toBeTruthy();
});

it('saves content and users but keeps the owners fixed', async () => {
  render(<MyBots />);
  fireEvent.click(await screen.findByRole('button', { name: 'Wissens-Bot bearbeiten' }));
  await screen.findByRole('checkbox', { name: 'Servicewissen' });
  // Owners are shown, but not removable by an owner.
  expect(screen.queryByRole('button', { name: 'ada entfernen' })).toBeNull();
  expect(screen.getByRole('checkbox', { name: 'admin-bereich' })).toBeTruthy();

  fireEvent.click(screen.getByRole('checkbox', { name: 'Servicewissen' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Recht' }));
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));

  await waitFor(() => expect(api.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true));
  const [path, init] = api.mock.calls.find(([, options]) => options?.method === 'PATCH')!;
  expect(path).toBe('/api/v1/bots/wissens-bot');
  const body = JSON.parse(init?.body as string);
  expect(body.collections).toEqual(['admin-bereich', 'servicewissen']);
  expect(body.grants).toEqual([{ team_id: 't-service', role: 'user' }, { team_id: 't-legal', role: 'user' }]);
  expect(body.system_prompt).toBe('Hilf.');
  expect(await screen.findByText('Bot gespeichert.')).toBeTruthy();
});
