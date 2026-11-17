'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { BellPlus, CheckCheck, RefreshCw, Search, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import {
  CHANGE_PAGE_SIZE, CHANGES_CHANGED_EVENT, changeHref, createSubscription, deleteSubscription, loadChanges, loadSubscriptions, markChangesRead, searchChanges,
  type ChangeNotification, type ChangeNotificationPage, type ChangeSearchItem, type ChangeSearchResult, type ChangeSummary, type TopicSubscription,
} from '@/lib/document-freshness';
import { portalError, type KnowledgeSpace } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import { EmptyState, Notice, PortalPage } from './shared';

/** Search windows offered for 'Was hat sich geändert?' (days; the backend allows 1..365). */
const SEARCH_DAYS = [7, 30, 90, 365];

function notifyChanged() {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(CHANGES_CHANGED_EVENT));
}

/**
 * 'Änderungen' (/aenderungen): what changed in the knowledge the caller may
 * read (lib/document-freshness.ts, backend app/api/document_freshness.py) --
 * an ad-hoc search „Was hat sich geändert?“ over recently released new
 * versions, the caller's topic subscriptions (keywords and/or a space) and
 * the feed of notifications they produced (mark read). The sidebar badge
 * counts unread notifications (CHANGES_CHANGED_EVENT refreshes it).
 */
export function ChangesPage() {
  const { t } = useI18n();
  const [spaces, setSpaces] = useState<KnowledgeSpace[]>([]);
  const [feedKey, setFeedKey] = useState(0);
  useEffect(() => {
    apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections').then(data => setSpaces(data.items ?? [])).catch(() => setSpaces([]));
  }, []);
  return <PortalPage title={t('portal.changes.title')} description={t('portal.changes.description')} eyebrow={t('portal.changes.eyebrow')}>
    <ChangeSearch spaces={spaces} />
    <div className="portal-changes-grid">
      <ChangeFeed key={feedKey} />
      <Subscriptions spaces={spaces} onChanged={() => setFeedKey(value => value + 1)} />
    </div>
  </PortalPage>;
}

function SummaryLines({ summary }: { summary: ChangeSummary }) {
  const { t } = useI18n();
  const stats = summary.stats;
  return <>
    {stats && <small>{t('portal.changes.stats', { added: stats.added_lines, removed: stats.removed_lines })}</small>}
    {summary.lines.length > 0 && <ul className="portal-change-lines">{summary.lines.map((line, index) => <li key={index}>{line}</li>)}</ul>}
  </>;
}

function ChangeTypeBadge({ type }: { type: string }) {
  const { t } = useI18n();
  return type === 'new'
    ? <span className="portal-badge portal-badge-success">{t('portal.changes.type.new')}</span>
    : <span className="portal-badge portal-badge-working">{t('portal.changes.type.updated')}</span>;
}

function ChangeSearch({ spaces }: { spaces: KnowledgeSpace[] }) {
  const { t, locale, formatDate } = useI18n();
  const [query, setQuery] = useState('');
  const [collectionId, setCollectionId] = useState('');
  const [days, setDays] = useState(30);
  const [result, setResult] = useState<ChangeSearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(0);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const current = ++requestId.current;
    setSearching(true); setError('');
    try {
      const found = await searchChanges({ q: query, collectionId: collectionId || undefined, days });
      if (current === requestId.current) setResult(found);
    } catch (err) {
      if (current === requestId.current) { setResult(null); setError(portalError(err, locale)); }
    } finally {
      if (current === requestId.current) setSearching(false);
    }
  }

  return <section className="portal-panel" aria-labelledby="change-search-title">
    <div className="portal-section-heading"><h2 id="change-search-title">{t('portal.changes.search.heading')}</h2></div>
    <form className="portal-change-search" role="search" onSubmit={submit}>
      <label className="portal-change-search-query">{t('portal.changes.search.label')}
        <input type="search" maxLength={200} value={query} placeholder={t('portal.changes.search.placeholder')} onChange={event => setQuery(event.target.value)} />
      </label>
      <label>{t('portal.changes.search.space')}
        <select value={collectionId} onChange={event => setCollectionId(event.target.value)}>
          <option value="">{t('portal.changes.allSpaces')}</option>
          {spaces.map(space => <option key={space.collection_id} value={space.collection_id}>{space.name || space.slug}</option>)}
        </select>
      </label>
      <label>{t('portal.changes.search.period')}
        <select value={days} onChange={event => setDays(Number(event.target.value))}>
          {SEARCH_DAYS.map(value => <option key={value} value={value}>{t('portal.changes.search.days', { count: value })}</option>)}
        </select>
      </label>
      <Button type="submit" disabled={searching}><Search size={16} aria-hidden="true" />{searching ? t('portal.changes.search.searching') : t('portal.changes.search.submit')}</Button>
    </form>
    {error && <div className="px-5 pb-5"><Notice error>{error}</Notice></div>}
    {result && <div className="portal-change-results" aria-live="polite">
      <p className="portal-field-hint">{result.terms.length
        ? t('portal.changes.search.resultTerms', { count: result.items.length, terms: result.terms.join(', ') })
        : t('portal.changes.search.resultAll', { count: result.items.length })}</p>
      {result.incomplete && <Notice>{t('portal.changes.search.incomplete')}</Notice>}
      {result.items.length ? <ul className="portal-change-list" aria-label={t('portal.changes.search.resultsAria')}>
        {result.items.map(item => <SearchResultRow key={item.job_id} item={item} formatDate={formatDate} />)}
      </ul> : <p className="portal-field-hint">{t('portal.changes.search.empty')}</p>}
    </div>}
  </section>;
}

function SearchResultRow({ item, formatDate }: { item: ChangeSearchItem; formatDate: (value: string) => string }) {
  const { t } = useI18n();
  return <li className="portal-change-item">
    <div className="portal-change-head">
      <Link href={changeHref(item)}>{item.title}</Link>
      <ChangeTypeBadge type={item.change_type} />
    </div>
    <small>{item.collection.name || item.collection.slug} · {t('portal.changes.version', { version: item.document_version })} · {t('portal.changes.releasedAt', { date: formatDate(item.released_at) })}</small>
    <SummaryLines summary={item.summary} />
  </li>;
}

function ChangeFeed() {
  const { t, locale, formatDate } = useI18n();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [feed, setFeed] = useState<ChangeNotificationPage | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);

  const load = useCallback(() => {
    const current = ++requestId.current;
    return loadChanges({ unread: unreadOnly, page })
      .then(result => {
        if (current !== requestId.current) return;
        // Past the end (read or deleted meanwhile): go to the last page that still has items.
        const lastPage = Math.max(1, Math.ceil(result.total / CHANGE_PAGE_SIZE));
        if (!result.items.length && page > lastPage) { setPage(lastPage); return; }
        setFeed(result); setError('');
      })
      .catch(err => { if (current === requestId.current) setError(portalError(err, locale)); });
  }, [unreadOnly, page, locale]);
  useEffect(() => { void load(); }, [load]);

  async function markRead(target: { ids: string[] } | { all: true }) {
    if (busy) return;
    setBusy(true); setError('');
    try {
      await markChangesRead(target);
      notifyChanged();
      if (unreadOnly) {
        // Read items leave this list and the pages shift (and 'all' reaches beyond this page): ask the server.
        await load();
        return;
      }
      const now = new Date().toISOString();
      const ids = 'ids' in target ? new Set(target.ids) : null;
      const isTarget = (item: ChangeNotification) => !item.read_at && (!ids || ids.has(item.id));
      setFeed(previous => previous && { ...previous, items: previous.items.map(item => (isTarget(item) ? { ...item, read_at: now } : item)) });
    } catch (err) {
      setError(portalError(err, locale));
    } finally {
      setBusy(false);
    }
  }

  const totalPages = feed ? Math.max(1, Math.ceil(feed.total / CHANGE_PAGE_SIZE)) : 1;
  const hasUnread = Boolean(feed?.items.some(item => !item.read_at));

  return <section className="portal-panel" aria-labelledby="change-feed-title">
    <div className="portal-section-heading">
      <h2 id="change-feed-title">{t('portal.changes.feed.heading')}{feed ? ` · ${feed.total}` : ''}</h2>
      <div className="flex flex-wrap items-center gap-2">
        <label className="portal-choice !my-0"><input type="checkbox" checked={unreadOnly} onChange={event => { setUnreadOnly(event.target.checked); setPage(1); }} />{t('portal.changes.feed.unreadOnly')}</label>
        <Button variant="outline" size="sm" disabled={busy || !hasUnread} onClick={() => void markRead({ all: true })}><CheckCheck size={15} aria-hidden="true" />{t('portal.changes.feed.markAllRead')}</Button>
        <Button variant="ghost" size="sm" onClick={() => void load()}><RefreshCw size={15} aria-hidden="true" />{t('common.refresh')}</Button>
      </div>
    </div>
    {error && <div className="px-5 pt-4"><Notice error action={() => void load()}>{error}</Notice></div>}
    {feed === null && !error ? <p role="status" className="portal-loading">{t('portal.changes.feed.loading')}</p>
      : feed?.items.length ? <>
        <ul className="portal-change-list portal-change-feed" aria-label={t('portal.changes.feed.heading')}>
          {feed.items.map(item => <li key={item.id} className={`portal-change-item ${item.read_at ? '' : 'portal-change-unread'}`}>
            <div className="portal-change-head">
              <Link href={changeHref(item)} onClick={() => { if (!item.read_at) void markRead({ ids: [item.id] }); }}>{item.title}</Link>
              <ChangeTypeBadge type={item.change_type} />
              {!item.read_at && <span className="portal-badge portal-badge-warning">{t('portal.changes.feed.unread')}</span>}
            </div>
            <small>
              {item.collection.name || item.collection.slug}
              {item.document_version ? ` · ${t('portal.changes.version', { version: item.document_version })}` : ''}
              {` · ${formatDate(item.created_at)}`}
              {item.subscription ? ` · ${t('portal.changes.feed.viaSubscription', { label: item.subscription.label })}` : ''}
            </small>
            {item.matched_terms.length > 0 && <small>{t('portal.changes.matchedTerms', { terms: item.matched_terms.join(', ') })}</small>}
            <SummaryLines summary={item.summary} />
            {!item.read_at && <div><Button variant="ghost" size="sm" disabled={busy} onClick={() => void markRead({ ids: [item.id] })}>{t('portal.changes.feed.markRead')}</Button></div>}
          </li>)}
        </ul>
        {totalPages > 1 && <nav className="portal-pagination" aria-label={t('portal.changes.feed.paginationAria')}>
          <span>{t('portal.changes.feed.pageOf', { page, totalPages })}</span>
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>{t('common.back')}</Button>
          <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>{t('common.next')}</Button>
        </nav>}
      </> : feed && <EmptyState title={unreadOnly ? t('portal.changes.feed.emptyUnreadTitle') : t('portal.changes.feed.emptyTitle')}>{t('portal.changes.feed.emptyBody')}</EmptyState>}
  </section>;
}

function Subscriptions({ spaces, onChanged }: { spaces: KnowledgeSpace[]; onChanged: () => void }) {
  const { t, locale } = useI18n();
  const [items, setItems] = useState<TopicSubscription[] | null>(null);
  const [query, setQuery] = useState('');
  const [collectionId, setCollectionId] = useState('');
  const [label, setLabel] = useState('');
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(() => loadSubscriptions()
    .then(result => { setItems(result.items); setError(''); })
    .catch(err => setError(portalError(err, locale))), [locale]);
  useEffect(() => { void load(); }, [load]);

  const canCreate = Boolean(query.trim() || collectionId);
  async function create(event: FormEvent) {
    event.preventDefault();
    if (!canCreate || saving) return;
    setSaving(true); setError(''); setNotice('');
    try {
      const created = await createSubscription({
        ...(query.trim() ? { query: query.trim() } : {}),
        ...(collectionId ? { collection_id: collectionId } : {}),
        ...(label.trim() ? { label: label.trim() } : {}),
      });
      setItems(previous => [created, ...(previous ?? []).filter(item => item.id !== created.id)]);
      setQuery(''); setCollectionId(''); setLabel('');
      setNotice(t('portal.changes.subscriptions.created', { label: created.label }));
    } catch (err) {
      setError(portalError(err, locale));
    } finally {
      setSaving(false);
    }
  }

  async function remove(subscription: TopicSubscription) {
    if (deletingId) return;
    setDeletingId(subscription.id); setError(''); setNotice('');
    try {
      await deleteSubscription(subscription.id);
      setItems(previous => (previous ?? []).filter(item => item.id !== subscription.id));
      setNotice(t('portal.changes.subscriptions.deleted', { label: subscription.label }));
      // Its notifications are deleted with it (CASCADE): reload the feed and the badge.
      onChanged();
      notifyChanged();
    } catch (err) {
      setError(portalError(err, locale));
    } finally {
      setDeletingId(null);
    }
  }

  return <section className="portal-panel" aria-labelledby="subscriptions-title">
    <div className="portal-section-heading"><h2 id="subscriptions-title">{t('portal.changes.subscriptions.heading')}</h2></div>
    <div className="portal-subscriptions">
      <p className="portal-field-hint !mt-0">{t('portal.changes.subscriptions.hint')}</p>
      {error && <Notice error>{error}</Notice>}
      {notice && <Notice>{notice}</Notice>}
      {items === null && !error ? <p role="status" className="portal-field-hint">{t('portal.changes.subscriptions.loading')}</p>
        : items?.length ? <ul className="portal-subscription-list" aria-label={t('portal.changes.subscriptions.heading')}>
          {items.map(item => <li key={item.id}>
            <span>
              <strong>{item.label}</strong>
              <small>
                {item.terms.length ? t('portal.changes.subscriptions.keywords', { terms: item.terms.join(', ') }) : t('portal.changes.subscriptions.allTopics')}
                {' · '}{item.collection ? item.collection.name || item.collection.slug : t('portal.changes.allSpaces')}
              </small>
            </span>
            <Button variant="ghost" size="icon" disabled={deletingId === item.id} onClick={() => void remove(item)} aria-label={t('portal.changes.subscriptions.deleteAria', { label: item.label })}><Trash2 size={16} aria-hidden="true" /></Button>
          </li>)}
        </ul> : items && <p className="portal-field-hint">{t('portal.changes.subscriptions.empty')}</p>}
      <form className="portal-subscription-form" onSubmit={create} aria-labelledby="subscription-new-title">
        <h3 id="subscription-new-title">{t('portal.changes.subscriptions.newHeading')}</h3>
        <label>{t('portal.changes.subscriptions.queryLabel')}
          <input maxLength={200} value={query} placeholder={t('portal.changes.subscriptions.queryPlaceholder')} onChange={event => setQuery(event.target.value)} />
        </label>
        <label>{t('portal.changes.subscriptions.spaceLabel')}
          <select value={collectionId} onChange={event => setCollectionId(event.target.value)}>
            <option value="">{t('portal.changes.allSpaces')}</option>
            {spaces.map(space => <option key={space.collection_id} value={space.collection_id}>{space.name || space.slug}</option>)}
          </select>
        </label>
        <label>{t('portal.changes.subscriptions.labelLabel')}
          <input maxLength={120} value={label} placeholder={t('portal.changes.subscriptions.labelPlaceholder')} onChange={event => setLabel(event.target.value)} />
        </label>
        {!canCreate && <p className="portal-field-hint !mt-0">{t('portal.changes.subscriptions.needsOne')}</p>}
        <Button type="submit" disabled={saving || !canCreate}><BellPlus size={16} aria-hidden="true" />{saving ? t('portal.changes.subscriptions.saving') : t('portal.changes.subscriptions.create')}</Button>
      </form>
    </div>
  </section>;
}
