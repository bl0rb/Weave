// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import { NewKnowledgeSpace } from './new-knowledge-space';
import { KnowledgeDetail } from './knowledge';
import { PortalHome } from './home';

const navigation = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => navigation }));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => ({ user: { username: 'Ada' } }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);
const area = {
  collection_id: 'area', slug: 'service', name: 'Servicewissen', description: 'Antworten für den Service',
  visibility: 'restricted', grants: [], created_by: null, responsible_team: null, role: 'owner', can_manage: true, can_upload: true,
};
const serviceConfig = { team_name: 'Service', team_names: ['Service'], teams: [{ id: 't-service', name: 'Service' }], publication_configured: true };

beforeEach(() => { api.mockReset(); navigation.replace.mockReset(); });
afterEach(cleanup);

it('creates the area with purpose, responsible team and member team, then continues with it selected', async () => {
  api.mockResolvedValueOnce(serviceConfig);
  render(<NewKnowledgeSpace />);
  await screen.findByRole('checkbox', { name: 'Service' });
  expect((screen.getByRole('combobox', { name: /Zuständige Gruppe/ }) as HTMLSelectElement).value).toBe('t-service');
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: '  Servicewissen  ' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Details angeben' }), { target: { value: ' Antworten für den Service ' } });
  api.mockResolvedValueOnce(area);
  fireEvent.click(screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }));
  // Straight on to the source form (files or Confluence are chosen there, in step 02).
  await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith('/sources/new?collection=area'));
  expect(JSON.parse(api.mock.calls[1][1]?.body as string)).toEqual({
    name: 'Servicewissen',
    description: 'Antworten für den Service',
    responsible_team_id: 't-service',
    visibility: 'restricted',
    grants: [{ team_id: 't-service', role: 'member' }],
  });
});

it('explains a name that is already taken and marks the required fields', async () => {
  api.mockResolvedValueOnce(serviceConfig);
  const { container } = render(<NewKnowledgeSpace />);
  await screen.findByRole('checkbox', { name: 'Service' });
  expect(container.querySelectorAll('.portal-required')).toHaveLength(2);
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Servicewissen' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Details angeben' }), { target: { value: 'Wofür' } });
  api.mockRejectedValueOnce(new ApiError(409, 'A knowledge space with this name already exists'));
  fireEvent.click(screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }));
  expect(await screen.findByText(/Es gibt bereits einen Wissensbereich mit diesem Namen/)).toBeTruthy();
});

it('requires a purpose before the area can be created', async () => {
  api.mockResolvedValueOnce(serviceConfig);
  render(<NewKnowledgeSpace />);
  await screen.findByRole('checkbox', { name: 'Service' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Bereich' } });
  const save = screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }) as HTMLButtonElement;
  expect(save.disabled).toBe(true);
  fireEvent.change(screen.getByRole('textbox', { name: 'Details angeben' }), { target: { value: 'Wofür' } });
  expect(save.disabled).toBe(false);
});

it('lets the member teams be narrowed and adds a newly chosen responsible team', async () => {
  api.mockResolvedValueOnce({ ...serviceConfig, team_names: ['Service', 'Vertrieb'], teams: [{ id: 't-service', name: 'Service' }, { id: 't-sales', name: 'Vertrieb' }] });
  render(<NewKnowledgeSpace />);
  await screen.findByRole('checkbox', { name: 'Service' });
  expect((screen.getByRole('checkbox', { name: 'Vertrieb' }) as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Service' }));
  fireEvent.change(screen.getByRole('combobox', { name: /Zuständige Gruppe/ }), { target: { value: 't-sales' } });
  expect((screen.getByRole('checkbox', { name: 'Vertrieb' }) as HTMLInputElement).checked).toBe(true);
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Übergreifendes Wissen' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Details angeben' }), { target: { value: 'Vertriebswissen' } });
  api.mockResolvedValueOnce(area);
  fireEvent.click(screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  const body = JSON.parse(api.mock.calls[1][1]?.body as string);
  expect(body.grants).toEqual([{ team_id: 't-sales', role: 'member' }]);
  expect(body.responsible_team_id).toBe('t-sales');
});

it('only opens an area to everyone after an explicit confirmation', async () => {
  api.mockResolvedValue({ team_name: null, team_names: [], teams: [], publication_configured: true });
  render(<NewKnowledgeSpace />);
  await screen.findByRole('radio', { name: 'Alle angemeldeten Nutzer' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Bereich' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Details angeben' }), { target: { value: 'Wofür' } });
  const save = screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }) as HTMLButtonElement;
  expect(save.disabled).toBe(false);
  fireEvent.click(screen.getByRole('radio', { name: 'Alle angemeldeten Nutzer' }));
  expect(save.disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox'));
  expect(save.disabled).toBe(false);
});

it('keeps the Übersicht free of a "Wissensbereich anlegen" shortcut but offers "Quelle hinzufügen" as its one header action', async () => {
  api.mockResolvedValue({ items: [], total: 0 });
  const { container } = render(<PortalHome />);
  await screen.findByRole('heading', { name: /^Guten (Morgen|Tag|Abend), Ada\.$/ });
  expect(screen.queryByRole('link', { name: /Wissensbereich anlegen/ })).toBeNull();
  expect(container.querySelectorAll('header.portal-header a')).toHaveLength(1);
  expect(screen.getByRole('link', { name: 'Quelle hinzufügen' }).getAttribute('href')).toBe('/sources/new');
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

it('uses the upload capability for entitled existing team members', async () => {
  const entitled = { ...area, can_manage: false, can_upload: true };
  api.mockImplementation(async path => path === '/api/v1/collections/area' ? entitled : {
    items: [{ id: 'doc', original_filename: 'Leitfaden.pdf', status: 'FINISHED', collection_id: 'area', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null }],
    total: 1,
  });
  render(<KnowledgeDetail id="area" />);
  expect(await screen.findByRole('link', { name: 'Quelle hinzufügen' })).toBeTruthy();
  // Several documents at once go through the selection + bulk bar, not a release-all button.
  expect(screen.queryByRole('button', { name: 'Dokumente freigeben' })).toBeNull();
});

it('keeps explicit readers from uploading or releasing a collection', async () => {
  api.mockImplementation(async path => path === '/api/v1/collections/area'
    ? { ...area, can_manage: false, can_upload: false }
    : { items: [], total: 1 });
  render(<KnowledgeDetail id="area" />);
  await screen.findByRole('heading', { name: 'Servicewissen' });
  expect(screen.queryByRole('link', { name: 'Quelle hinzufügen' })).toBeNull();
});
