// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiJson } from '@/lib/api';
import { I18nProvider } from '@/i18n/provider';
import { REVIEW_TASKS_CHANGED_EVENT, type ReviewTask } from '@/lib/review-tasks';
import { ReviewTasks } from './review-tasks';

const auth = vi.hoisted(() => ({ user: { id: 'me', username: 'ada', role: 'user' as const } }));
vi.mock('@/lib/auth-context', () => ({ useAuth: () => auth }));
vi.mock('next/navigation', () => ({ usePathname: () => '/pruefaufgaben', useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock('@/lib/api', async importOriginal => ({ ...await importOriginal<typeof import('@/lib/api')>(), apiJson: vi.fn() }));

const api = vi.mocked(apiJson);

const space = { id: 'space-1', name: 'Personal', slug: 'personal' };
function task(overrides: Partial<ReviewTask> = {}): ReviewTask {
  return {
    id: 'gap-1', kind: 'knowledge_gap', status: 'open', title: 'Wie hoch ist das Tagegeld?', collection: space,
    document: null, related_document: null, occurrence_count: 3,
    first_seen_at: '2026-10-01T10:00:00Z', last_seen_at: '2026-10-05T10:00:00Z',
    assignee: null, resolved_at: null, resolution_note: null,
    details: { tokens: ['tagegeld'], questions: [{ text: 'Wie hoch ist das Tagegeld?', count: 2, created_at: '2026-10-05T10:00:00Z' }, { text: 'Tagegeld Ausland?', count: 1, created_at: '2026-10-01T10:00:00Z' }], rewritten_queries: ['tagegeld höhe'] },
    ...overrides,
  };
}
const feedbackTask = task({
  id: 'fb-1', kind: 'answer_feedback', title: 'Reiserichtlinie.pdf', occurrence_count: 2,
  document: { job_id: 'job-1', title: 'Reiserichtlinie.pdf', document_version: 3 },
  details: { counts: { wrong: 1, outdated: 1 }, samples: [
    { rating: 'wrong', comment: 'Der Betrag ist 28 €', question: 'Wie hoch ist das Tagegeld?', answer_excerpt: 'Das Tagegeld beträgt 24 €.', heading_path: ['Reisekosten', 'Tagegeld'], created_at: '2026-10-04T10:00:00Z' },
    { rating: 'outdated', comment: null, question: 'Gilt die Pauschale noch?', answer_excerpt: null, heading_path: null, created_at: '2026-10-03T10:00:00Z' },
  ] },
});
const reviewDueTask = task({
  id: 'due-1', kind: 'review_due', title: 'Prüfung fällig: Handbuch.pdf', occurrence_count: 1,
  document: { job_id: 'job-2', title: 'Handbuch.pdf', document_version: 1 },
  details: { review_due_at: '2026-11-01T00:00:00Z', overdue: true },
});
const contradictionTask = task({
  id: 'con-1', kind: 'contradiction', title: 'Möglicher Widerspruch', occurrence_count: 1,
  document: { job_id: 'job-a', title: 'Reiserichtlinie.pdf', document_version: 3 },
  related_document: { job_id: 'job-b', title: 'FAQ Reisen.md', document_version: 1 },
  details: { pairs: [{ excerpt: 'Das Tagegeld beträgt 28 €.', other_excerpt: 'Das Tagegeld beträgt 24 €.', heading_path: ['Reisekosten'], other_heading_path: ['FAQ', 'Tagegeld'], facts: ['28 €'], other_facts: ['24 €'], similarity: 0.92 }] },
});
const summary = {
  feedback: { helpful: 12, wrong: 4, outdated: 2 }, unanswered: 7, tasks: { open: 3, in_progress: 1 },
  documents: [{ job_id: 'job-1', title: 'Reiserichtlinie.pdf', helpful: 1, wrong: 3, outdated: 1 }],
};
const spaces = { items: [
  { collection_id: 'space-1', slug: 'personal', name: 'Personal', can_upload: true, can_manage: false },
  { collection_id: 'space-2', slug: 'lesen', name: 'Nur lesen', can_upload: false, can_manage: false },
] };

let tasks: ReviewTask[];
let total: number | undefined;
let patchResult: (id: string, body: Record<string, unknown>) => ReviewTask | Promise<ReviewTask>;

function route(path: string, init?: RequestInit): unknown {
  const url = new URL(path, 'http://localhost');
  if (url.pathname === '/api/v1/collections') return spaces;
  if (url.pathname === '/api/v1/portal/quality/summary') return summary;
  if (url.pathname === '/api/v1/portal/review-tasks') return { items: tasks, total: total ?? tasks.length, page: Number(url.searchParams.get('page')), page_size: 25 };
  const id = decodeURIComponent(url.pathname.split('/').at(-1) ?? '');
  if (init?.method === 'PATCH') return patchResult(id, JSON.parse(init.body as string));
  const found = tasks.find(item => item.id === id);
  if (!found) throw new ApiError(404, 'Review task not found');
  return found;
}
const listCalls = () => api.mock.calls.map(call => new URL(call[0] as string, 'http://localhost')).filter(url => url.pathname === '/api/v1/portal/review-tasks');
const patchCalls = () => api.mock.calls.filter(call => (call[1] as RequestInit | undefined)?.method === 'PATCH').map(call => ({ path: call[0] as string, body: JSON.parse((call[1] as RequestInit).body as string) }));

beforeEach(() => {
  tasks = [task(), feedbackTask, reviewDueTask, contradictionTask];
  total = undefined;
  patchResult = (id, body) => ({ ...tasks.find(item => item.id === id)!, ...body, assignee: body.assignee_id ? { id: 'me', username: 'ada' } : null } as ReviewTask);
  api.mockReset();
  api.mockImplementation(async (path, init) => route(path as string, init));
});
afterEach(cleanup);

const detailPanel = () => document.querySelector('section.portal-review-task-detail') as HTMLElement;
async function openTask(title: string) {
  fireEvent.click(await screen.findByRole('button', { name: title }));
  await waitFor(() => expect(detailPanel()).toBeTruthy());
}

describe('ReviewTasks', () => {
  it('shows the quality summary and the active task list', async () => {
    render(<ReviewTasks />);
    expect(await screen.findByRole('button', { name: 'Wie hoch ist das Tagegeld?' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Prüfaufgaben · 4' })).toBeTruthy();
    expect(screen.getByText('Hilfreich').nextSibling?.textContent).toBe('12');
    expect(screen.getByText('Unbeantwortete Fragen').nextSibling?.textContent).toBe('7');
    expect(screen.getByText('3 offen · 1 in Bearbeitung')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Reiserichtlinie.pdf' }).getAttribute('href')).toBe('/reviews/job-1');
    expect(screen.getByText('3 Hinweise')).toBeTruthy();
    const params = listCalls()[0].searchParams;
    expect(params.get('status')).toBe('active');
    expect(params.get('page')).toBe('1');
  });

  it('offers only spaces the caller maintains and filters by space, kind and status', async () => {
    total = 60;
    render(<ReviewTasks />);
    await screen.findByRole('button', { name: 'Wie hoch ist das Tagegeld?' });
    const spaceSelect = screen.getByRole('combobox', { name: 'Wissensbereich' });
    await waitFor(() => expect(within(spaceSelect).getAllByRole('option').map(option => option.textContent)).toEqual(['Alle Wissensbereiche', 'Personal']));

    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await waitFor(() => expect(listCalls().at(-1)?.searchParams.get('page')).toBe('2'));

    fireEvent.change(spaceSelect, { target: { value: 'space-1' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Art' }), { target: { value: 'contradiction' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), { target: { value: 'done' } });
    await waitFor(() => {
      const params = listCalls().at(-1)!.searchParams;
      expect(params.get('collection_id')).toBe('space-1');
      expect(params.get('kind')).toBe('contradiction');
      expect(params.get('status')).toBe('done');
      expect(params.get('page')).toBe('1');
    });
    await waitFor(() => expect(api.mock.calls.some(call => call[0] === '/api/v1/portal/quality/summary?collection_id=space-1')).toBe(true));
  });

  it('presets filters and opens a deep-linked task from the URL', async () => {
    render(<ReviewTasks initial={{ aufgabe: 'due-1', art: 'review_due', status: 'open', bereich: 'space-1' }} />);
    await waitFor(() => expect(detailPanel()).toBeTruthy());
    expect(await within(detailPanel()).findByText('Überfällig')).toBeTruthy();
    const params = listCalls()[0].searchParams;
    expect(params.get('kind')).toBe('review_due');
    expect(params.get('status')).toBe('open');
    expect(params.get('collection_id')).toBe('space-1');
  });

  it('ignores unknown filter values from the URL', async () => {
    render(<ReviewTasks initial={{ art: 'bogus', status: 'everything' }} />);
    await screen.findByRole('button', { name: 'Wie hoch ist das Tagegeld?' });
    const params = listCalls()[0].searchParams;
    expect(params.get('status')).toBe('active');
    expect(params.has('kind')).toBe(false);
  });

  it('shows the questions of a knowledge gap with counts', async () => {
    render(<ReviewTasks />);
    await openTask('Wie hoch ist das Tagegeld?');
    const panel = detailPanel();
    expect(within(panel).getByRole('heading', { level: 2, name: 'Wie hoch ist das Tagegeld?' })).toBeTruthy();
    expect(within(panel).getByText('Tagegeld Ausland?')).toBeTruthy();
    expect(within(panel).getByText(/2-mal gefragt/)).toBeTruthy();
    expect(within(panel).getByText(/einmal gefragt/)).toBeTruthy();
    expect(within(panel).getByText('tagegeld höhe')).toBeTruthy();
    expect(within(panel).getByRole('link', { name: /Quelle hinzufügen/ }).getAttribute('href')).toBe('/sources/new');
  });

  it('shows feedback counts, comments and the document', async () => {
    render(<ReviewTasks />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reiserichtlinie.pdf' }));
    await waitFor(() => expect(detailPanel()).toBeTruthy());
    const panel = detailPanel();
    expect(within(panel).getByText('1 × falsch')).toBeTruthy();
    expect(within(panel).getByText('1 × Prüfung angefordert')).toBeTruthy();
    expect(within(panel).getByText('Der Betrag ist 28 €')).toBeTruthy();
    expect(within(panel).getByText('Das Tagegeld beträgt 24 €.')).toBeTruthy();
    expect(within(panel).getByText(/Reisekosten › Tagegeld/)).toBeTruthy();
    expect(within(panel).getByText('Ohne Kommentar')).toBeTruthy();
    expect(within(panel).getByRole('link', { name: 'Reiserichtlinie.pdf' }).getAttribute('href')).toBe('/reviews/job-1');
  });

  it('shows the due date of a review task', async () => {
    render(<ReviewTasks />);
    fireEvent.click(await screen.findByRole('button', { name: 'Prüfung fällig: Handbuch.pdf' }));
    await waitFor(() => expect(detailPanel()).toBeTruthy());
    const panel = detailPanel();
    expect(within(panel).getByText(/Prüfung fällig am .*2026/)).toBeTruthy();
    expect(within(panel).getByText('Überfällig')).toBeTruthy();
    expect(within(panel).getByRole('link', { name: /Dokument prüfen/ }).getAttribute('href')).toBe('/reviews/job-2');
  });

  it('shows contradiction excerpts side by side with links to both documents', async () => {
    render(<ReviewTasks />);
    fireEvent.click(await screen.findByRole('button', { name: 'Möglicher Widerspruch' }));
    await waitFor(() => expect(detailPanel()).toBeTruthy());
    const panel = detailPanel();
    expect(within(panel).getByRole('link', { name: /Reiserichtlinie\.pdf/ }).getAttribute('href')).toBe('/reviews/job-a');
    expect(within(panel).getByRole('link', { name: /FAQ Reisen\.md/ }).getAttribute('href')).toBe('/reviews/job-b');
    const pair = within(panel).getByRole('group', { name: 'Textstellenpaar 1' });
    expect(within(pair).getByText('Das Tagegeld beträgt 28 €.')).toBeTruthy();
    expect(within(pair).getByText('Das Tagegeld beträgt 24 €.')).toBeTruthy();
    expect(within(pair).getByText('FAQ › Tagegeld')).toBeTruthy();
    expect(within(pair).getByText('Angaben: 24 €')).toBeTruthy();
  });

  it('takes a task over: in progress and assigned to the caller', async () => {
    const changed = vi.fn();
    window.addEventListener(REVIEW_TASKS_CHANGED_EVENT, changed);
    render(<ReviewTasks />);
    await openTask('Wie hoch ist das Tagegeld?');
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Übernehmen' }));
    await waitFor(() => expect(patchCalls()).toEqual([{ path: '/api/v1/portal/review-tasks/gap-1', body: { status: 'in_progress', assignee_id: 'me' } }]));
    // Still active: the row stays, now in progress; taking it over again is not offered.
    await waitFor(() => expect(within(detailPanel()).queryByRole('button', { name: 'Übernehmen' })).toBeNull());
    expect(within(detailPanel()).getAllByText('In Bearbeitung').length).toBeGreaterThan(0);
    expect(screen.getByText(/übernommen von ada/)).toBeTruthy();
    expect(changed).toHaveBeenCalledTimes(1);
    window.removeEventListener(REVIEW_TASKS_CHANGED_EVENT, changed);
  });

  it('closes a task as done with a note and drops it from the active list', async () => {
    render(<ReviewTasks />);
    await openTask('Wie hoch ist das Tagegeld?');
    fireEvent.change(within(detailPanel()).getByRole('textbox', { name: 'Notiz (optional)' }), { target: { value: '  Abschnitt ergänzt  ' } });
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Erledigt' }));
    await waitFor(() => expect(patchCalls()[0]).toEqual({ path: '/api/v1/portal/review-tasks/gap-1', body: { status: 'done', resolution_note: 'Abschnitt ergänzt' } }));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Prüfaufgaben · 3' })).toBeTruthy());
    expect(screen.queryByRole('button', { name: 'Wie hoch ist das Tagegeld?' })).toBeNull();
    // The panel stays on the closed task, without actions.
    expect(within(detailPanel()).getByText('Notiz:')).toBeTruthy();
    expect(within(detailPanel()).queryByRole('button', { name: 'Verwerfen' })).toBeNull();
  });

  it('dismisses a task without a note', async () => {
    render(<ReviewTasks />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reiserichtlinie.pdf' }));
    await waitFor(() => expect(detailPanel()).toBeTruthy());
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Verwerfen' }));
    await waitFor(() => expect(patchCalls()[0]).toEqual({ path: '/api/v1/portal/review-tasks/fb-1', body: { status: 'dismissed' } }));
  });

  it('shows the error of a failed action and keeps the task', async () => {
    patchResult = () => { throw new ApiError(409, 'Another active task already covers this topic'); };
    render(<ReviewTasks />);
    await openTask('Wie hoch ist das Tagegeld?');
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Erledigt' }));
    expect(await within(detailPanel()).findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Wie hoch ist das Tagegeld?' })).toBeTruthy();
  });

  it('shows "not available" for a task of a space the caller may not see', async () => {
    render(<ReviewTasks initial={{ aufgabe: 'foreign-task' }} />);
    await waitFor(() => expect(detailPanel()).toBeTruthy());
    expect((await within(detailPanel()).findByRole('alert')).textContent).toMatch(/nicht verfügbar/);
    expect(within(detailPanel()).queryByRole('button', { name: 'Erledigt' })).toBeNull();
  });

  it('closes the detail panel with the close button and Escape', async () => {
    render(<ReviewTasks />);
    await openTask('Wie hoch ist das Tagegeld?');
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Details schließen' }));
    expect(detailPanel()).toBeNull();
    await openTask('Wie hoch ist das Tagegeld?');
    fireEvent.keyDown(detailPanel(), { key: 'Escape' });
    expect(detailPanel()).toBeNull();
  });

  it('has no actions on closed tasks', async () => {
    tasks = [task({ status: 'done', resolved_at: '2026-10-06T10:00:00Z', resolution_note: 'Erledigt durch neues Kapitel' })];
    render(<ReviewTasks />);
    await openTask('Wie hoch ist das Tagegeld?');
    expect(within(detailPanel()).getByText('Erledigt durch neues Kapitel')).toBeTruthy();
    for (const name of ['Übernehmen', 'Erledigt', 'Verwerfen']) expect(within(detailPanel()).queryByRole('button', { name })).toBeNull();
  });

  it('explains an empty inbox, and an empty filter result differently', async () => {
    tasks = [];
    render(<ReviewTasks />);
    expect(await screen.findByText('Keine offenen Prüfaufgaben')).toBeTruthy();
    fireEvent.change(screen.getByRole('combobox', { name: 'Art' }), { target: { value: 'knowledge_gap' } });
    expect(await screen.findByText('Keine passenden Prüfaufgaben')).toBeTruthy();
  });

  it('shows a retryable error when the list cannot be loaded', async () => {
    api.mockImplementation(async (path, init) => {
      if ((path as string).startsWith('/api/v1/portal/review-tasks?')) throw new ApiError(503, 'down');
      return route(path as string, init);
    });
    render(<ReviewTasks />);
    expect(await screen.findByText(/Der Dienst ist noch nicht bereit/)).toBeTruthy();
    api.mockImplementation(async (path, init) => route(path as string, init));
    fireEvent.click(screen.getByRole('button', { name: /Erneut versuchen/ }));
    expect(await screen.findByRole('button', { name: 'Wie hoch ist das Tagegeld?' })).toBeTruthy();
  });

  it('keeps the pagination when the last task of a page is closed and goes back a page', async () => {
    const extra = task({ id: 'gap-26', title: 'Letzte Aufgabe' });
    api.mockImplementation(async (path, init) => {
      const url = new URL(path as string, 'http://localhost');
      if (url.pathname === '/api/v1/portal/review-tasks' && url.searchParams.get('page') === '2') {
        return { items: tasks.includes(extra) ? [extra] : [], total: total ?? 26, page: 2, page_size: 25 };
      }
      return route(path as string, init);
    });
    total = 26;
    render(<ReviewTasks />);
    await screen.findByRole('button', { name: 'Wie hoch ist das Tagegeld?' });
    tasks = [...tasks, extra];
    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await openTask('Letzte Aufgabe');
    total = 25;
    tasks = tasks.filter(item => item !== extra);
    patchResult = (id, body) => ({ ...extra, ...body } as ReviewTask);
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Erledigt' }));
    await waitFor(() => expect(listCalls().at(-1)?.searchParams.get('page')).toBe('1'));
    expect(await screen.findByRole('button', { name: 'Wie hoch ist das Tagegeld?' })).toBeTruthy();
    expect(screen.queryByText('Keine offenen Prüfaufgaben')).toBeNull();
  });

  it('steps back from a page that came back empty and keeps the pagination visible', async () => {
    // Others closed tasks meanwhile: page 3 comes back empty and only 30 tasks (2 pages) are left.
    api.mockImplementation(async (path, init) => {
      const url = new URL(path as string, 'http://localhost');
      if (url.pathname === '/api/v1/portal/review-tasks' && url.searchParams.get('page') === '3') return { items: [], total: 30, page: 3, page_size: 25 };
      return route(path as string, init);
    });
    total = 60;
    render(<ReviewTasks />);
    await screen.findByRole('button', { name: 'Wie hoch ist das Tagegeld?' });
    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await waitFor(() => expect(listCalls().at(-1)?.searchParams.get('page')).toBe('2'));
    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    await waitFor(() => expect(listCalls().map(url => url.searchParams.get('page'))).toEqual(['1', '2', '3', '2']));
    expect(await screen.findByRole('button', { name: 'Wie hoch ist das Tagegeld?' })).toBeTruthy();
    expect(screen.getByRole('navigation', { name: 'Seiten der Prüfaufgaben' })).toBeTruthy();
    expect(screen.getByText('Seite 2 von 3')).toBeTruthy();
  });

  it('returns focus to the task row when the detail panel closes', async () => {
    render(<ReviewTasks />);
    await openTask('Wie hoch ist das Tagegeld?');
    expect(document.activeElement?.id).toBe('review-task-detail-title');
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Details schließen' }));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Wie hoch ist das Tagegeld?' }));
    await openTask('Reiserichtlinie.pdf');
    fireEvent.keyDown(detailPanel(), { key: 'Escape' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Reiserichtlinie.pdf' }));
  });

  it('moves focus to the list heading when the closed task has left the list', async () => {
    render(<ReviewTasks />);
    await openTask('Wie hoch ist das Tagegeld?');
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Erledigt' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Wie hoch ist das Tagegeld?' })).toBeNull());
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Details schließen' }));
    expect(document.activeElement?.id).toBe('review-tasks-list-title');
  });

  it('ignores a deep-linked space the caller does not maintain', async () => {
    render(<ReviewTasks initial={{ bereich: 'space-2' }} />);
    await waitFor(() => expect(listCalls().at(-1)?.searchParams.has('collection_id')).toBe(false));
    expect((screen.getByRole('combobox', { name: 'Wissensbereich' }) as HTMLSelectElement).value).toBe('');
    expect(await screen.findByRole('button', { name: 'Wie hoch ist das Tagegeld?' })).toBeTruthy();
    await waitFor(() => expect(api.mock.calls.at(-1)?.[0]).not.toBe('/api/v1/portal/quality/summary?collection_id=space-2'));
  });

  it('keeps the summary of the selected space when an older summary response arrives late', async () => {
    let resolveSlow: (value: unknown) => void = () => {};
    api.mockImplementation(async (path, init) => {
      if (path === '/api/v1/portal/quality/summary?collection_id=space-1') return new Promise(resolve => { resolveSlow = resolve; });
      return route(path as string, init);
    });
    render(<ReviewTasks />);
    const spaceSelect = await screen.findByRole('combobox', { name: 'Wissensbereich' });
    await waitFor(() => expect(within(spaceSelect).getAllByRole('option')).toHaveLength(2));
    fireEvent.change(spaceSelect, { target: { value: 'space-1' } });
    await waitFor(() => expect(api.mock.calls.some(call => call[0] === '/api/v1/portal/quality/summary?collection_id=space-1')).toBe(true));
    fireEvent.change(spaceSelect, { target: { value: '' } });
    await waitFor(() => expect(screen.getByText('Hilfreich').nextSibling?.textContent).toBe('12'));
    resolveSlow({ ...summary, feedback: { helpful: 99, wrong: 0, outdated: 0 } });
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(screen.getByText('Hilfreich').nextSibling?.textContent).toBe('12');
  });

  it('keeps a typed note after taking a task over and on Escape inside the note', async () => {
    render(<ReviewTasks />);
    await openTask('Wie hoch ist das Tagegeld?');
    const noteField = () => within(detailPanel()).getByRole('textbox', { name: 'Notiz (optional)' }) as HTMLTextAreaElement;
    fireEvent.change(noteField(), { target: { value: 'Kapitel 3 ergänzen' } });
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Übernehmen' }));
    await waitFor(() => expect(within(detailPanel()).queryByRole('button', { name: 'Übernehmen' })).toBeNull());
    expect(noteField().value).toBe('Kapitel 3 ergänzen');
    fireEvent.keyDown(noteField(), { key: 'Escape' });
    expect(detailPanel()).toBeTruthy();
    fireEvent.click(within(detailPanel()).getByRole('button', { name: 'Erledigt' }));
    await waitFor(() => expect(patchCalls().at(-1)?.body).toEqual({ status: 'done', resolution_note: 'Kapitel 3 ergänzen' }));
  });

  it('renders in English', async () => {
    render(<I18nProvider initialLocale="en"><ReviewTasks /></I18nProvider>);
    expect(await screen.findByRole('heading', { name: 'Review tasks · 4' })).toBeTruthy();
    expect(screen.getByText('Unanswered questions')).toBeTruthy();
    expect(screen.getAllByText(/Possible contradiction/).length).toBeGreaterThan(0);
  });
});
