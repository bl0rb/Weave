'use client';

import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { ArrowRight, BookOpenText, Pencil, Plus, Trash2 } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { apiSend, ConfirmDialog, Modal, inputClass } from '@/components/admin/admin-shared';
import { apiJson } from '@/lib/api';
import { formatRefreshInterval, refreshIntervalOptions, type CrawlScope } from '@/lib/imports';
import { CATALOG_CATEGORIES, loadSourceCatalog, type SourceCatalogCategory, type SourceCatalogEntry, type SourceCatalogEntryInput } from '@/lib/source-catalog';
import { jsonBody, portalError } from '@/lib/portal';
import type { MessageKey } from '@/i18n/messages';
import { useI18n } from '@/i18n/provider';
import { EmptyState, Notice, PortalPage, RequiredMark } from './shared';
import { KnowledgeTabs } from './page-tabs';

const SCOPES: CrawlScope[] = ['page', 'prefix', 'host'];

/**
 * Wissensbereiche › Quellenkatalog: the shared, curated list of reusable
 * sources. Everyone may browse and pick an entry (the "add source" form then
 * asks for scope and target space); admins maintain the entries. An entry is
 * a template only -- it grants no read rights on anything imported from it.
 */
export function SourceCatalog() {
  const { t, locale } = useI18n();
  const [entries, setEntries] = useState<SourceCatalogEntry[] | null>(null);
  const [canManage, setCanManage] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<'' | SourceCatalogCategory>('');
  const [editing, setEditing] = useState<SourceCatalogEntry | 'new' | null>(null);
  const [deleting, setDeleting] = useState<SourceCatalogEntry | null>(null);
  const load = useCallback(() => loadSourceCatalog()
    .then(data => { setEntries(data.items); setCanManage(data.can_manage); setError(''); })
    .catch(err => setError(portalError(err, locale))), [locale]);
  useEffect(() => { void load(); }, [load]);
  const visible = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase('de');
    return entries?.filter(entry => (!category || entry.category === category)
      && (!needle || `${entry.title} ${entry.description} ${entry.publisher} ${entry.start_url}`.toLocaleLowerCase('de').includes(needle)));
  }, [entries, search, category]);
  return <PortalPage eyebrow={null} title={t('portal.nav.sourceCatalog')} description={t('portal.catalog.description')}
    actions={canManage ? <Button onClick={() => { setEditing('new'); setNotice(''); }}><Plus size={16} aria-hidden="true" />{t('portal.catalog.add')}</Button> : undefined}>
    <KnowledgeTabs />
    {notice && <Notice>{notice}</Notice>}
    {error && <Notice error action={load}>{error}</Notice>}
    <p className="mb-5 text-sm text-[var(--muted)]">{t('portal.catalog.readRightsHint')} {!canManage && t('portal.catalog.manageHint')}</p>
    {entries === null && !error ? <Notice>{t('portal.catalog.loading')}</Notice> : entries?.length ? <>
      <div className="mb-5 flex flex-wrap items-end gap-4">
        <label className="portal-search">{t('portal.catalog.searchLabel')}<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder={t('portal.catalog.searchPlaceholder')} /></label>
        <div className="portal-filter-row" role="group" aria-label={t('portal.catalog.form.category')}>
          <button type="button" aria-pressed={category === ''} onClick={() => setCategory('')}>{t('portal.catalog.categoryAll')}</button>
          {CATALOG_CATEGORIES.map(value => <button key={value} type="button" aria-pressed={category === value} onClick={() => setCategory(value)}>{t(`portal.catalog.category.${value}` as MessageKey)}</button>)}
        </div>
      </div>
      <div className="portal-space-grid">
        {visible?.map(entry => <article key={entry.id} className="portal-panel portal-space-card">
          <div className="portal-space-top">
            <span className="portal-space-mark" aria-hidden="true"><BookOpenText size={18} /></span>
            <div><h2>{entry.title}</h2><p>{entry.description || entry.start_url}</p></div>
          </div>
          <p className="text-xs text-[var(--muted)]"><span className="portal-chip">{t(`portal.catalog.category.${entry.category}` as MessageKey)}</span>{entry.publisher && ` · ${t('portal.catalog.publisherLabel')}: ${entry.publisher}`}</p>
          <p className="break-all text-xs text-[var(--muted)]"><a className="portal-inline-link" href={entry.start_url} target="_blank" rel="noopener noreferrer">{entry.start_url}</a></p>
          <p className="text-xs text-[var(--muted)]">{t('portal.catalog.scopeLabel')}: {t(`portal.catalog.scope.${entry.crawl_scope}` as MessageKey)}{entry.max_pages ? ` · ${t('portal.catalog.maxPages', { count: entry.max_pages })}` : ''}{entry.include_linked_documents ? ` · ${t('portal.catalog.linkedDocs')}` : ''}{entry.interval_seconds ? ` · ${t('portal.catalog.intervalLabel')}: ${formatRefreshInterval(entry.interval_seconds, locale)}` : ''}</p>
          {entry.legal_source && <p className="text-xs font-semibold text-[var(--ink-2)]">{t('portal.catalog.legalSource')}</p>}
          <div className="portal-space-actions">
            <Link className={buttonVariants({ size: 'sm' })} href={`/sources/new?kind=catalog&catalog=${encodeURIComponent(entry.id)}`}>{t('portal.catalog.useEntry')}<ArrowRight size={15} aria-hidden="true" /></Link>
            {canManage && <div className="portal-space-management"><Button variant="ghost" size="sm" onClick={() => { setEditing(entry); setNotice(''); }} aria-label={t('portal.catalog.editAria', { title: entry.title })}><Pencil size={14} aria-hidden="true" />{t('portal.catalog.edit')}</Button><Button variant="ghost" size="sm" onClick={() => { setDeleting(entry); setNotice(''); }} aria-label={t('portal.catalog.deleteAria', { title: entry.title })}><Trash2 size={14} aria-hidden="true" />{t('portal.catalog.delete')}</Button></div>}
          </div>
        </article>)}
      </div>
      {visible?.length === 0 && <EmptyState title={t('portal.catalog.noMatchTitle')}>{t('portal.catalog.noMatchBody')}</EmptyState>}
    </> : !error && <EmptyState title={t('portal.catalog.emptyTitle')}>{t('portal.catalog.emptyBody')}</EmptyState>}
    {editing && <CatalogEntryEditor entry={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); setNotice(t('portal.catalog.savedNotice')); await load(); }} />}
    {deleting && <ConfirmDialog title={t('portal.catalog.deleteDialogTitle')} confirmLabel={t('portal.catalog.deleteConfirm')}
      body={<p><strong className="text-slate-950">{deleting.title}</strong> – {t('portal.catalog.deleteDialogBody')}</p>}
      onClose={() => setDeleting(null)}
      onConfirm={async () => { await apiSend(`/api/v1/source-catalog/${encodeURIComponent(deleting.id)}`, { method: 'DELETE' }); setDeleting(null); setNotice(t('portal.catalog.deletedNotice')); await load(); }} />}
  </PortalPage>;
}

function CatalogEntryEditor({ entry, onClose, onSaved }: { entry: SourceCatalogEntry | null; onClose: () => void; onSaved: () => Promise<void> }) {
  const { t, locale } = useI18n();
  const intervals = refreshIntervalOptions(locale);
  const [form, setForm] = useState<SourceCatalogEntryInput>({
    title: entry?.title ?? '', description: entry?.description ?? '', category: entry?.category ?? 'reference', publisher: entry?.publisher ?? '',
    start_url: entry?.start_url ?? '', crawl_scope: entry?.crawl_scope ?? 'prefix', include_linked_documents: entry?.include_linked_documents ?? false,
    max_pages: entry?.max_pages ?? null, interval_seconds: entry?.interval_seconds ?? 604800, legal_source: entry?.legal_source ?? false,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const set = <K extends keyof SourceCatalogEntryInput>(key: K, value: SourceCatalogEntryInput[K]) => setForm(current => ({ ...current, [key]: value }));
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form.title.trim() || !form.start_url.trim()) return;
    setSaving(true); setError('');
    try {
      if (entry) await apiJson(`/api/v1/source-catalog/${encodeURIComponent(entry.id)}`, { ...jsonBody(form), method: 'PATCH' });
      else await apiJson('/api/v1/source-catalog', jsonBody(form));
      await onSaved();
    } catch (err) { setError(portalError(err, locale)); setSaving(false); }
  }
  return <Modal title={entry ? t('portal.catalog.form.editTitle') : t('portal.catalog.form.createTitle')} onClose={onClose}>
    {error && <Notice error>{error}</Notice>}
    <form className="space-y-4" onSubmit={save}>
      <label className="block text-sm font-medium text-slate-700">{t('portal.catalog.form.title')}<RequiredMark /><input autoFocus required maxLength={255} className={inputClass} value={form.title} onChange={event => set('title', event.target.value)} /></label>
      <label className="block text-sm font-medium text-slate-700">{t('portal.catalog.form.description')}<textarea rows={3} maxLength={4000} className={inputClass} value={form.description} onChange={event => set('description', event.target.value)} /></label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm font-medium text-slate-700">{t('portal.catalog.form.category')}<select className={inputClass} value={form.category} onChange={event => set('category', event.target.value as SourceCatalogCategory)}>{CATALOG_CATEGORIES.map(value => <option key={value} value={value}>{t(`portal.catalog.category.${value}` as MessageKey)}</option>)}</select></label>
        <label className="block text-sm font-medium text-slate-700">{t('portal.catalog.form.publisher')}<input maxLength={255} className={inputClass} value={form.publisher} onChange={event => set('publisher', event.target.value)} /></label>
      </div>
      <label className="block text-sm font-medium text-slate-700">{t('portal.catalog.form.startUrl')}<RequiredMark /><input required type="url" maxLength={512} className={inputClass} value={form.start_url} onChange={event => set('start_url', event.target.value)} placeholder="https://www.gesetze-im-internet.de/vwvfg/" /></label>
      <p className="portal-field-hint">{t('portal.catalog.form.startUrlHint')}</p>
      <div className="grid gap-4 sm:grid-cols-3">
        <label className="block text-sm font-medium text-slate-700">{t('portal.catalog.form.crawlScope')}<select className={inputClass} value={form.crawl_scope} onChange={event => set('crawl_scope', event.target.value as CrawlScope)}>{SCOPES.map(value => <option key={value} value={value}>{t(`portal.catalog.scope.${value}` as MessageKey)}</option>)}</select></label>
        <label className="block text-sm font-medium text-slate-700">{t('portal.catalog.form.maxPages')}<input type="number" min={1} className={inputClass} value={form.max_pages ?? ''} onChange={event => set('max_pages', event.target.value ? Number(event.target.value) : null)} /></label>
        <label className="block text-sm font-medium text-slate-700">{t('portal.catalog.form.interval')}<select className={inputClass} value={form.interval_seconds ?? 0} onChange={event => set('interval_seconds', Number(event.target.value) || null)}><option value={0}>{t('portal.importSync.manualOnly')}</option>{intervals.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      </div>
      <label className="portal-choice"><input type="checkbox" checked={form.include_linked_documents} onChange={event => set('include_linked_documents', event.target.checked)} />{t('portal.catalog.form.includeLinkedDocuments')}</label>
      <label className="portal-choice"><input type="checkbox" checked={form.legal_source} onChange={event => set('legal_source', event.target.checked)} />{t('portal.catalog.form.legalSource')}</label>
      <div className="flex flex-wrap justify-end gap-2"><Button type="button" variant="outline" onClick={onClose} disabled={saving}>{t('common.cancel')}</Button><Button type="submit" disabled={saving || !form.title.trim() || !form.start_url.trim()}>{saving ? t('portal.catalog.form.saving') : t('common.save')}</Button></div>
    </form>
  </Modal>;
}
