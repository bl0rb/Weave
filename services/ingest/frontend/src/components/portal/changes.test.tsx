// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, apiFetch, apiJson } from '@/lib/api';
import { CHANGES_CHANGED_EVENT, type ChangeNotification, type TopicSubscription } from '@/lib/document-freshness';
import { ChangesPage } from './changes';

vi.mock('next/navigation', () => ({ usePathname: () => '/aenderungen', useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn(), apiFetch: vi.fn() }));
const api = vi.mocked(apiJson);
const fetcher = vi.mocked(apiFetch);

const travel = { id: 'c1', name: 'Reisen', slug: 'reisen' };
const spaces = { items: [{ collection_id: 'c1', slug: 'reisen', name: 'Reisen', can_upload: false, can_manage: false }, { collection_id: 'c2', slug: 'hr', name: 'Personal', can_upload: true, can_manage: false }] };
const stats = { added_lines: 3, removed_lines: 1, sections_added: 0, sections_removed: 0, sections_changed: 1 };
function notification(overrides: Partial<ChangeNotification> = {}): ChangeNotification {
  return {
    id: 'n1', subscription: { id: 's1', label: 'Reisekosten' }, job_id: 'v2', previous_job_id: 'v1', document_version: 2,
    collection: travel, change_type: 'updated', title: 'Reiserichtlinie.pdf',
    summary: { stats, lines: ['Abschnitt „Tagegeld“ geändert: 1 Zeile ersetzt'] }, matched_terms: ['reisekosten'],
    read_at: null, created_at: '2026-11-10T08:00:00Z', ...overrides,
  };
}
const subscription: TopicSubscription = { id: 's1', label: 'Reisekosten', query: 'Reisekosten', terms: ['reisekosten'], collection: travel, created_at: '2026-11-01T00:00:00Z' };

let feed: ChangeNotification[];
let subscriptions: TopicSubscription[];
let handler: (url: URL, init?: RequestInit) => unknown;

function defaultRoute(url: URL, init?: RequestInit): unknown {
  switch (url.pathname) {
    case '/api/v1/collections': return spaces;
    case '/api/v1/portal/changes': {
      const items = url.searchParams.get('unread') === 'true' ? feed.filter(item => !item.read_at) : feed;
      const page = Number(url.searchParams.get('page')), size = Number(url.searchParams.get('page_size'));
      return { items: items.slice((page - 1) * size, page * size), total: items.length, page, page_size: size };
    }
    case '/api/v1/portal/changes/read': {
      const body = JSON.parse(init!.body as string) as { ids?: string[]; all?: boolean };
      const targets = feed.filter(item => !item.read_at && (body.all || body.ids?.includes(item.id)));
      feed = feed.map(item => (targets.includes(item) ? { ...item, read_at: '2026-11-17T00:00:00Z' } : item));
      return { updated: targets.length };
    }
    case '/api/v1/portal/subscriptions':
      if (init?.method === 'POST') {
        const body = JSON.parse(init.body as string);
        return { id: 's-new', label: body.label ?? body.query ?? 'Personal', query: body.query ?? null, terms: body.query ? [body.query.toLowerCase()] : [], collection: body.collection_id ? { id: 'c2', name: 'Personal', slug: 'hr' } : null, created_at: '2026-11-17T00:00:00Z' };
      }
      return { items: subscriptions };
    case '/api/v1/portal/changes/search': return {
      terms: ['reiseregeln'],
      items: [{ job_id: 'v2', previous_job_id: 'v1', document_version: 2, collection: travel, change_type: 'updated', title: 'Reiserichtlinie.pdf', released_at: '2026-11-09T00:00:00Z', summary: { stats, lines: ['Abschnitt „Tagegeld“ geändert'] }, matched_terms: ['reiseregeln'] },
        { job_id: 'n1', previous_job_id: null, document_version: 1, collection: travel, change_type: 'new', title: 'Reise-FAQ.md', released_at: '2026-11-08T00:00:00Z', summary: { stats: null, lines: [] }, matched_terms: ['reiseregeln'] }],
    };
    default: throw new ApiError(404, 'not found');
  }
}

beforeEach(() => {
  feed = [notification(), notification({ id: 'n2', title: 'Reise-FAQ.md', job_id: 'f1', previous_job_id: null, change_type: 'new', summary: { stats: null, lines: [] }, read_at: '2026-11-11T00:00:00Z', subscription: null })];
  subscriptions = [subscription];
  handler = defaultRoute;
  api.mockReset();
  api.mockImplementation(async (path, init) => handler(new URL(path as string, 'http://localhost'), init));
  fetcher.mockReset();
  fetcher.mockResolvedValue({ ok: true, status: 204 } as Response);
});
afterEach(cleanup);

const calls = (pathname: string) => api.mock.calls.filter(([path]) => new URL(path as string, 'http://localhost').pathname === pathname);

it('shows the feed with unread state, links to the comparison and marks one notification read', async () => {
  const changed = vi.fn();
  window.addEventListener(CHANGES_CHANGED_EVENT, changed);
  render(<ChangesPage />);
  const list = await screen.findByRole('list', { name: 'Benachrichtigungen' });
  const [first, second] = within(list).getAllByRole('listitem').filter(item => item.parentElement === list);
  expect(within(first).getByRole('link', { name: 'Reiserichtlinie.pdf' }).getAttribute('href')).toBe('/reviews/v2/changes?base=v1');
  expect(within(first).getByText('Ungelesen')).toBeTruthy();
  expect(within(first).getByText('Aktualisiert')).toBeTruthy();
  expect(within(first).getByText(/über „Reisekosten“/)).toBeTruthy();
  expect(within(first).getByText('Abschnitt „Tagegeld“ geändert: 1 Zeile ersetzt')).toBeTruthy();
  expect(within(second).getByRole('link', { name: 'Reise-FAQ.md' }).getAttribute('href')).toBe('/reviews/f1');
  expect(within(second).queryByText('Ungelesen')).toBeNull();

  fireEvent.click(within(first).getByRole('button', { name: 'Als gelesen markieren' }));
  await waitFor(() => expect(within(first).queryByText('Ungelesen')).toBeNull());
  expect(JSON.parse(calls('/api/v1/portal/changes/read')[0][1]!.body as string)).toEqual({ ids: ['n1'] });
  expect(changed).toHaveBeenCalled();
  expect((screen.getByRole('button', { name: 'Alle als gelesen markieren' }) as HTMLButtonElement).disabled).toBe(true);
  window.removeEventListener(CHANGES_CHANGED_EVENT, changed);
});

it('filters unread notifications and marks all read', async () => {
  render(<ChangesPage />);
  await screen.findByRole('list', { name: 'Benachrichtigungen' });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Nur ungelesene' }));
  await waitFor(() => expect(calls('/api/v1/portal/changes').some(([path]) => (path as string).includes('unread=true'))).toBe(true));
  await waitFor(() => expect(screen.queryByRole('link', { name: 'Reise-FAQ.md' })).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Alle als gelesen markieren' }));
  expect(await screen.findByText('Keine ungelesenen Benachrichtigungen')).toBeTruthy();
  expect(JSON.parse(calls('/api/v1/portal/changes/read')[0][1]!.body as string)).toEqual({ all: true });
});

it('reloads the unread-only feed after marking read and leaves a page that became empty', async () => {
  feed = Array.from({ length: 30 }, (_, index) => notification({ id: `u${index}`, title: `Dokument ${index}.pdf`, job_id: `j${index}` }));
  render(<ChangesPage />);
  await screen.findByRole('link', { name: 'Dokument 0.pdf' });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Nur ungelesene' }));
  await waitFor(() => expect(calls('/api/v1/portal/changes').some(([path]) => (path as string).includes('unread=true'))).toBe(true));
  fireEvent.click(await screen.findByRole('button', { name: 'Weiter' }));
  await screen.findByRole('link', { name: 'Dokument 25.pdf' });
  expect(screen.getByText('Seite 2 von 2')).toBeTruthy();
  // Mark the five items of page 2 read one after another.
  for (let index = 25; index < 30; index += 1) {
    const item = screen.getByRole('link', { name: `Dokument ${index}.pdf` }).closest('li')!;
    fireEvent.click(within(item).getByRole('button', { name: 'Als gelesen markieren' }));
    await waitFor(() => expect(screen.queryByRole('link', { name: `Dokument ${index}.pdf` })).toBeNull());
  }
  // Page 2 is gone: back on page 1 with the 25 still unread notifications and the server's total.
  expect(await screen.findByRole('link', { name: 'Dokument 0.pdf' })).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'Benachrichtigungen · 25' })).toBeTruthy();
  expect(screen.queryByText('Keine ungelesenen Benachrichtigungen')).toBeNull();
  // 'Alle' reaches beyond the page: the count comes from the server, not from this page.
  fireEvent.click(screen.getByRole('button', { name: 'Alle als gelesen markieren' }));
  expect(await screen.findByText('Keine ungelesenen Benachrichtigungen')).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'Benachrichtigungen · 0' })).toBeTruthy();
});

it('marks a notification read when its link is opened', async () => {
  render(<ChangesPage />);
  fireEvent.click(await screen.findByRole('link', { name: 'Reiserichtlinie.pdf' }));
  await waitFor(() => expect(calls('/api/v1/portal/changes/read')).toHaveLength(1));
});

it('shows an empty feed and a load error with retry', async () => {
  feed = [];
  render(<ChangesPage />);
  expect(await screen.findByText('Noch keine Benachrichtigungen')).toBeTruthy();
  cleanup();
  handler = (url, init) => { if (url.pathname === '/api/v1/portal/changes') throw new ApiError(503, 'down'); return defaultRoute(url, init); };
  render(<ChangesPage />);
  expect(await screen.findByText('Der Dienst ist noch nicht bereit. Bitte versuche es später erneut oder wende dich an die Administration.')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Erneut versuchen' })).toBeTruthy();
});

it('searches „Was hat sich geändert?“ with keywords, space and period', async () => {
  render(<ChangesPage />);
  const search = screen.getByRole('search');
  await within(search).findByRole('option', { name: 'Personal' });
  fireEvent.change(within(search).getByLabelText('Thema oder Stichworte'), { target: { value: 'Reiseregeln' } });
  fireEvent.change(within(search).getByLabelText('Wissensbereich'), { target: { value: 'c1' } });
  fireEvent.change(within(search).getByLabelText('Zeitraum'), { target: { value: '90' } });
  fireEvent.click(within(search).getByRole('button', { name: 'Änderungen suchen' }));
  const results = await screen.findByRole('list', { name: 'Gefundene Änderungen' });
  const url = new URL(calls('/api/v1/portal/changes/search')[0][0] as string, 'http://localhost');
  expect(Object.fromEntries(url.searchParams)).toEqual({ days: '90', q: 'Reiseregeln', collection_id: 'c1' });
  expect(screen.getByText('2 geänderte Versionen zu „reiseregeln“')).toBeTruthy();
  expect(within(results).getByRole('link', { name: 'Reiserichtlinie.pdf' }).getAttribute('href')).toBe('/reviews/v2/changes?base=v1');
  expect(within(results).getByRole('link', { name: 'Reise-FAQ.md' }).getAttribute('href')).toBe('/reviews/n1');
  expect(within(results).getByText('+3 / −1 Zeilen')).toBeTruthy();
  expect(within(results).getByText('Neu')).toBeTruthy();
});

it('says when the search stopped early at the scan cap', async () => {
  handler = (url, init) => (url.pathname === '/api/v1/portal/changes/search' ? { ...(defaultRoute(url, init) as object), incomplete: true } : defaultRoute(url, init));
  render(<ChangesPage />);
  fireEvent.click(screen.getByRole('button', { name: 'Änderungen suchen' }));
  await screen.findByRole('list', { name: 'Gefundene Änderungen' });
  expect(screen.getByText(/Nicht alle Änderungen wurden durchsucht/)).toBeTruthy();
});

it('reports an empty search result and a search error', async () => {
  handler = (url, init) => (url.pathname === '/api/v1/portal/changes/search' ? { items: [], terms: [] } : defaultRoute(url, init));
  render(<ChangesPage />);
  fireEvent.click(screen.getByRole('button', { name: 'Änderungen suchen' }));
  expect(await screen.findByText('In diesem Zeitraum wurde dazu nichts geändert.')).toBeTruthy();
  expect(screen.getByText('0 geänderte Versionen')).toBeTruthy();
  handler = (url, init) => { if (url.pathname === '/api/v1/portal/changes/search') throw new ApiError(404, 'Collection not found'); return defaultRoute(url, init); };
  fireEvent.click(screen.getByRole('button', { name: 'Änderungen suchen' }));
  expect((await screen.findAllByRole('alert')).some(alert => alert.textContent?.includes('nicht verfügbar'))).toBe(true);
});

it('lists subscriptions, requires keywords or a space, creates and deletes one', async () => {
  render(<ChangesPage />);
  const list = await screen.findByRole('list', { name: 'Meine Themen' });
  expect(within(list).getByText('Reisekosten')).toBeTruthy();
  expect(within(list).getByText(/Stichworte: reisekosten · Reisen/)).toBeTruthy();

  const form = screen.getByRole('form', { name: 'Thema folgen' });
  const submit = within(form).getByRole('button', { name: 'Folgen' }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  expect(within(form).getByText('Gib Stichworte ein oder wähle einen Wissensbereich.')).toBeTruthy();
  await within(form).findByRole('option', { name: 'Personal' });
  fireEvent.change(within(form).getByLabelText('Wissensbereich (optional)'), { target: { value: 'c2' } });
  expect(submit.disabled).toBe(false);
  fireEvent.click(submit);
  expect(await screen.findByText('Du folgst jetzt „Personal“.')).toBeTruthy();
  const post = calls('/api/v1/portal/subscriptions').find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(post[1]!.body as string)).toEqual({ collection_id: 'c2' });
  expect(within(list).getAllByRole('listitem')).toHaveLength(2);

  const changesBefore = calls('/api/v1/portal/changes').length;
  fireEvent.click(within(list).getByRole('button', { name: 'Thema „Reisekosten“ nicht mehr folgen' }));
  expect(await screen.findByText('Du folgst „Reisekosten“ nicht mehr.')).toBeTruthy();
  expect(fetcher).toHaveBeenCalledWith('/api/v1/portal/subscriptions/s1', { method: 'DELETE' });
  expect(within(list).getAllByRole('listitem')).toHaveLength(1);
  // The feed reloads: the subscription's notifications are gone with it.
  await waitFor(() => expect(calls('/api/v1/portal/changes').length).toBeGreaterThan(changesBefore));
});

it('sends trimmed keywords and label, and shows a failed create', async () => {
  render(<ChangesPage />);
  const form = await screen.findByRole('form', { name: 'Thema folgen' });
  fireEvent.change(within(form).getByLabelText('Stichworte'), { target: { value: '  Tagegeld Ausland ' } });
  fireEvent.change(within(form).getByLabelText('Bezeichnung (optional)'), { target: { value: ' Reisen ins Ausland ' } });
  handler = (url, init) => {
    if (url.pathname === '/api/v1/portal/subscriptions' && init?.method === 'POST') throw new ApiError(422, 'Too many subscriptions');
    return defaultRoute(url, init);
  };
  fireEvent.click(within(form).getByRole('button', { name: 'Folgen' }));
  expect(await screen.findByText('Too many subscriptions')).toBeTruthy();
  const post = calls('/api/v1/portal/subscriptions').find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(post[1]!.body as string)).toEqual({ query: 'Tagegeld Ausland', label: 'Reisen ins Ausland' });
  // The input is kept for a retry.
  expect((within(form).getByLabelText('Stichworte') as HTMLInputElement).value).toBe('  Tagegeld Ausland ');
});

it('shows a failed delete and keeps the subscription', async () => {
  fetcher.mockResolvedValue({ ok: false, status: 404 } as Response);
  render(<ChangesPage />);
  const list = await screen.findByRole('list', { name: 'Meine Themen' });
  fireEvent.click(within(list).getByRole('button', { name: 'Thema „Reisekosten“ nicht mehr folgen' }));
  expect(await screen.findByText('Dieser Inhalt ist nicht verfügbar oder wurde noch nicht für dich freigegeben.')).toBeTruthy();
  expect(within(list).getAllByRole('listitem')).toHaveLength(1);
});

it('shows an empty subscription list', async () => {
  subscriptions = [];
  render(<ChangesPage />);
  expect(await screen.findByText('Du folgst noch keinem Thema.')).toBeTruthy();
});
