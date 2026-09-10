// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api';
import { NewKnowledgeSpace } from './new-knowledge-space';
import { KnowledgeDetail } from './knowledge';
import { PortalHome } from './home';

const navigation = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => navigation }));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => ({ user: { username: 'Ada' } }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);
const area = { collection_id: 'area', slug: 'service', name: 'Servicewissen', description: '', read_teams: ['Service'], can_manage: true };

beforeEach(() => { api.mockReset(); navigation.replace.mockReset(); });
afterEach(cleanup);

it('creates the area for the own team and continues with that area selected', async () => {
  api.mockResolvedValueOnce({ team_name: 'Service', team_names: ['Service'], publication_configured: true });
  render(<NewKnowledgeSpace />);
  await screen.findByRole('checkbox', { name: 'Service' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: '  Servicewissen  ' } });
  api.mockResolvedValueOnce(area);
  fireEvent.click(screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }));
  await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith('/sources/new?collection=area'));
  expect(JSON.parse(api.mock.calls[1][1]?.body as string)).toEqual({ name: 'Servicewissen', description: '', read_teams: ['Service'] });
});

it('preselects all of the caller\'s own teams and allows narrowing the selection', async () => {
  api.mockResolvedValueOnce({ team_name: 'Service', team_names: ['Service', 'Vertrieb'], publication_configured: true });
  render(<NewKnowledgeSpace />);
  await screen.findByRole('checkbox', { name: 'Service' });
  expect((screen.getByRole('checkbox', { name: 'Service' }) as HTMLInputElement).checked).toBe(true);
  expect((screen.getByRole('checkbox', { name: 'Vertrieb' }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Vertrieb' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Übergreifendes Wissen' } });
  api.mockResolvedValueOnce(area);
  fireEvent.click(screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  expect(JSON.parse(api.mock.calls[1][1]?.body as string).read_teams).toEqual(['Service']);
});

it('does not accidentally open an area to everyone when a user has no team', async () => {
  api.mockResolvedValue({ team_name: null, team_names: [], publication_configured: true });
  render(<NewKnowledgeSpace />);
  await screen.findByText(/muss dir die Administration zuerst ein Team/);
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Bereich' } });
  const save = screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }) as HTMLButtonElement;
  expect(save.disabled).toBe(true);
  fireEvent.click(screen.getByRole('radio', { name: 'Alle angemeldeten Teams' }));
  expect(save.disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox'));
  expect(save.disabled).toBe(false);
});

it('starts creation directly from the journey and empty state, without a header upload action', async () => {
  api.mockResolvedValue({ items: [], total: 0 });
  const { container } = render(<PortalHome />);
  await screen.findByRole('link', { name: 'Wissensbereich anlegen' });
  expect(screen.queryByText('WISSEN VERBINDET')).toBeNull();
  const startLinks = screen.getAllByRole('link', { name: /Wissensbereich anlegen/ });
  expect(startLinks.length).toBe(2);
  startLinks.forEach(link => expect(link.getAttribute('href')).toBe('/knowledge/new'));
  expect(container.querySelector('header.portal-header a')).toBeNull();
});

it.each([false, true])('offers one context-specific upload action for an area (has documents: %s)', async hasDocuments => {
  api.mockImplementation(async path => path === '/api/v1/collections/area' ? area : {
    items: hasDocuments ? [{ id: 'doc', original_filename: 'Leitfaden.pdf', status: 'PENDING', collection_id: 'area', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: null, quality_recommendation: null, can_release: true, release: null }] : [],
    total: hasDocuments ? 1 : 0,
  });
  const { container } = render(<KnowledgeDetail id="area" />);
  await screen.findByRole('link', { name: 'Quelle hinzufügen' });
  expect(screen.getAllByRole('link', { name: 'Quelle hinzufügen' })).toHaveLength(1);
  expect(container.querySelector('header.portal-header a')).toBeNull();
});

it('offers a collection ZIP and an individual Markdown download for finished documents', async () => {
  api.mockImplementation(async path => path === '/api/v1/collections/area' ? area : {
    items: [{ id: 'doc', original_filename: 'Leitfaden.pdf', status: 'FINISHED', collection_id: 'area', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null }],
    total: 1,
  });
  render(<KnowledgeDetail id="area" />);
  expect(await screen.findByRole('button', { name: 'Alle als ZIP' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Leitfaden.pdf als Markdown herunterladen' })).toBeTruthy();
});
