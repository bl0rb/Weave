// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import { NewKnowledgeSpace } from './new-knowledge-space';
import { KnowledgeDetail } from './knowledge';
import { PortalHome } from './home';

const navigation = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => navigation }));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => ({ user: { id: 'u-ada', username: 'Ada' } }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));
const api = vi.mocked(apiJson);
const area = {
  collection_id: 'area', slug: 'service', name: 'Servicewissen', description: 'Antworten für den Service',
  visibility: 'restricted', grants: [], created_by: null, responsible_team: null, role: 'owner', can_manage: true, can_upload: true,
};
const serviceConfig = { team_name: 'Service', team_names: ['Service'], teams: [{ id: 't-service', name: 'Service' }], publication_configured: true };

beforeEach(() => { api.mockReset(); navigation.replace.mockReset(); });
afterEach(cleanup);

/** Config first, then the directory (403 for a first-time creator unless `directory` is given), then the POST. */
function mockApi(config: object, { directory, created = area, createError }: { directory?: object; created?: object; createError?: Error } = {}) {
  api.mockImplementation(async (path, init) => {
    if (path === '/api/v1/portal/config') return config;
    if (path === '/api/v1/directory/teams') { if (directory) return directory; throw new ApiError(403, 'Only an admin or a collection owner may use the directory'); }
    if (path === '/api/v1/collections' && init?.method === 'POST') { if (createError) throw createError; return created; }
    throw new Error(`unexpected request: ${path}`);
  });
}
const postedBody = () => JSON.parse(api.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.body as string);
const step = (name: RegExp) => fireEvent.click(screen.getByRole('button', { name }));
async function fillBasics(name: string, purpose: string) {
  await screen.findByRole('textbox', { name: 'Name' });
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: name } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Details angeben' }), { target: { value: purpose } });
}

it('creates the area with purpose, responsible team and member team, then continues with it selected', async () => {
  mockApi(serviceConfig);
  render(<NewKnowledgeSpace />);
  await fillBasics('  Servicewissen  ', ' Antworten für den Service ');
  expect((screen.getByRole('combobox', { name: /Zuständige Gruppe/ }) as HTMLSelectElement).value).toBe('t-service');
  step(/Überprüfen/);
  fireEvent.click(screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }));
  // Straight on to the source form (files or Confluence are chosen there, in step 02).
  await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith('/sources/new?collection=area'));
  expect(postedBody()).toEqual({
    name: 'Servicewissen',
    description: 'Antworten für den Service',
    responsible_team_id: 't-service',
    visibility: 'restricted',
    grants: [{ team_id: 't-service', role: 'member' }],
  });
});

it('explains a name that is already taken and marks the required fields', async () => {
  mockApi(serviceConfig, { createError: new ApiError(409, 'A knowledge space with this name already exists') });
  const { container } = render(<NewKnowledgeSpace />);
  await fillBasics('Servicewissen', 'Wofür');
  expect(container.querySelectorAll('.portal-required')).toHaveLength(2);
  step(/Überprüfen/);
  fireEvent.click(screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }));
  expect(await screen.findByText(/Es gibt bereits einen Wissensbereich mit diesem Namen/)).toBeTruthy();
});

it('requires a purpose before the area can be created and links back to the step', async () => {
  mockApi(serviceConfig);
  render(<NewKnowledgeSpace />);
  await fillBasics('Bereich', '');
  step(/Überprüfen/);
  const save = screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }) as HTMLButtonElement;
  expect(save.disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Zweck' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Details angeben' }), { target: { value: 'Wofür' } });
  step(/Überprüfen/);
  expect((screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }) as HTMLButtonElement).disabled).toBe(false);
});

it('lets the member teams be narrowed and adds a newly chosen responsible team', async () => {
  mockApi({ ...serviceConfig, team_names: ['Service', 'Vertrieb'], teams: [{ id: 't-service', name: 'Service' }, { id: 't-sales', name: 'Vertrieb' }] });
  render(<NewKnowledgeSpace />);
  await fillBasics('Übergreifendes Wissen', 'Vertriebswissen');
  fireEvent.change(screen.getByRole('combobox', { name: /Zuständige Gruppe/ }), { target: { value: 't-sales' } });
  step(/Gruppen/);
  // Directory closed for a first-time creator: own teams only, without member counts.
  expect(screen.getByText(/deine eigenen Gruppen/)).toBeTruthy();
  expect((screen.getByRole('checkbox', { name: 'Vertrieb' }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Service' }));
  step(/Überprüfen/);
  fireEvent.click(screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }));
  await waitFor(() => expect(navigation.replace).toHaveBeenCalled());
  expect(postedBody().grants).toEqual([{ team_id: 't-sales', role: 'member' }]);
  expect(postedBody().responsible_team_id).toBe('t-sales');
});

it('shows the creator as fixed owner and adds people when the directory is open', async () => {
  mockApi(serviceConfig, { directory: { items: [{ id: 't-service', name: 'Service', member_count: 4 }] } });
  api.mockImplementationOnce(async () => serviceConfig);
  const base = api.getMockImplementation()!;
  api.mockImplementation(async (path, init) => typeof path === 'string' && path.startsWith('/api/v1/directory/users')
    ? { items: [{ id: 'u2', username: 'msmith', display_name: null, team: 'Recht' }] }
    : base(path, init));
  render(<NewKnowledgeSpace />);
  await fillBasics('Bereich', 'Wofür');
  step(/Personen/);
  expect(screen.getByText(/wirst automatisch Besitzer/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Ada entfernen' })).toBeNull();
  fireEvent.change(screen.getByPlaceholderText('Name oder Gruppe suchen …'), { target: { value: 'ms' } });
  fireEvent.click(await screen.findByRole('button', { name: /msmith/ }));
  step(/Überprüfen/);
  fireEvent.click(screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }));
  await waitFor(() => expect(navigation.replace).toHaveBeenCalled());
  expect(postedBody().grants).toEqual([{ user_id: 'u2', role: 'reader' }, { team_id: 't-service', role: 'member' }]);
});

it('only opens an area to everyone after an explicit confirmation', async () => {
  mockApi({ team_name: null, team_names: [], teams: [], publication_configured: true });
  render(<NewKnowledgeSpace />);
  await fillBasics('Bereich', 'Wofür');
  step(/Zugriff/);
  fireEvent.click(screen.getByRole('radio', { name: /^Öffentlich/ }));
  step(/Überprüfen/);
  expect((screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/noch nicht bestätigt/)).toBeTruthy();
  step(/^2\s*Zugriff/);
  fireEvent.click(screen.getByRole('checkbox'));
  step(/Überprüfen/);
  expect((screen.getByRole('button', { name: 'Anlegen und Quelle hinzufügen' }) as HTMLButtonElement).disabled).toBe(false);
});

it('keeps the Übersicht free of a "Wissensbereich anlegen" shortcut but offers "Quelle hinzufügen" as its one header action', async () => {
  api.mockImplementation(async path => path === '/api/v1/portal/stats'
    ? { spaces: [], review_waiting: 0, review_wait_median_hours: null, review_oldest_since: null, releases_per_week: [] }
    : { items: [], total: 0 });
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
  // The header only links to the space's documents, and only when it has some.
  expect([...container.querySelectorAll('header.portal-header a')].map(link => link.textContent)).toEqual(hasDocuments ? ['1 Dokument ansehen'] : []);
});

it('offers the collection ZIP under Wartung (single Markdown downloads live in the technical details)', async () => {
  api.mockImplementation(async path => path === '/api/v1/collections/area' ? area : {
    items: [{ id: 'doc', original_filename: 'Leitfaden.pdf', status: 'FINISHED', collection_id: 'area', collection_name: 'Servicewissen', created_at: '2026-09-01T12:00:00Z', quality_grade: 'A', quality_recommendation: 'allow', can_release: true, release: null }],
    total: 1,
  });
  render(<KnowledgeDetail id="area" />);
  expect(await screen.findByRole('button', { name: 'Alle als ZIP' })).toBeTruthy();
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
