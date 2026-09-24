'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { ArrowRight, Pencil, Plus, RefreshCw } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { accessSummary } from '@/lib/access-summary';
import { jsonBody, portalError, type KnowledgeSpace } from '@/lib/portal';
import { AccessDialog } from '@/components/portal/access-dialog';
import { EmptyState, Notice } from '@/components/portal/shared';
import { useI18n } from '@/i18n/provider';

type ManagedCollection = Omit<KnowledgeSpace, 'can_manage'> & {
  owner: { id: string; username: string } | null;
  document_count: number;
  pending_count: number;
  running_count: number;
  review_count: number;
  failed_count: number;
  released_count: number;
};
type CollectionPage = { items: ManagedCollection[]; total: number };
const PAGE_SIZE = 20;

export function CollectionsTab() {
  const { t } = useI18n();
  const [page, setPage] = useState<CollectionPage | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [editing, setEditing] = useState<ManagedCollection | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ q: query, offset: String(offset), limit: String(PAGE_SIZE) });
    apiJson<CollectionPage>(`/api/v1/portal/admin/collections?${params}`, { signal: controller.signal })
      .then(data => {
        if (!controller.signal.aborted) { setPage(data); setError(''); }
      }).catch(err => {
        if (!controller.signal.aborted) setError(portalError(err));
      });
    return () => controller.abort();
  }, [query, offset, revision]);

  const reload = () => { setError(''); setRevision(value => value + 1); };
  function search(event: FormEvent) {
    event.preventDefault();
    setPage(null); setError(''); setOffset(0); setQuery(draft.trim()); setRevision(value => value + 1);
  }

  return <div className="portal-page !max-w-none !p-0">
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div><h2 className="text-[17px] font-semibold">{t('admin.collections.title')}</h2>
        <p className="mt-2 max-w-2xl text-sm text-slate-600">{t('admin.collections.description')}</p>
      </div>
      <Link href="/knowledge/new" className={buttonVariants({ variant: 'outline' })}><Plus size={16} />{t('admin.collections.create')}</Link>
    </div>
    {notice && <Notice>{notice}</Notice>}
    {editing && <CollectionEditor key={editing.collection_id} collection={editing} onCancel={() => setEditing(null)}
      onSaved={() => { setEditing(null); setNotice(t('admin.collections.saved')); reload(); }} />}
    <form onSubmit={search} className="mb-6 flex flex-wrap items-end gap-3">
      <label className="min-w-56 flex-1">{t('admin.collections.searchLabel')}
        <input type="search" value={draft} onChange={event => setDraft(event.target.value)} placeholder={t('admin.collections.searchPlaceholder')} />
      </label>
      <Button type="submit" variant="outline">{t('common.search')}</Button>
      <Button type="button" variant="ghost" onClick={reload}><RefreshCw size={15} />{t('common.refresh')}</Button>
    </form>
    {error && <Notice error action={reload}>{error}</Notice>}
    {!page && !error && <Notice>{t('admin.collections.loading')}</Notice>}
    {page && <section className="portal-panel" aria-label={t('admin.collections.manageAriaLabel')}>
      <div className="portal-section-heading"><h3 className="font-semibold">{query ? t('admin.collections.countFound', { count: page.total }) : t('admin.collections.count', { count: page.total })}</h3></div>
      {page.items.length ? <div className="portal-table-scroll"><table className="portal-table">
        <thead><tr><th scope="col">{t('admin.collections.column.name')}</th><th scope="col">{t('admin.collections.column.owner')}</th><th scope="col">{t('admin.collections.column.access')}</th><th scope="col">{t('admin.collections.column.status')}</th><th scope="col">{t('admin.collections.column.manage')}</th></tr></thead>
        <tbody>{page.items.map(collection => <tr key={collection.collection_id}>
          <td className="min-w-48"><Link href={`/knowledge/${encodeURIComponent(collection.collection_id)}`} className="font-semibold text-emerald-800 hover:underline">{collection.name}</Link>
            {collection.description && <p className="mt-1 line-clamp-2 text-xs text-slate-500">{collection.description}</p>}
          </td>
          <td>{collection.owner?.username || t('admin.collections.noOwner')}</td>
          <td className="min-w-36">{collection.read_teams.length ? collection.read_teams.join(', ') : t('admin.collections.allTeams')}</td>
          <td className="min-w-48"><strong className="block font-medium">{t('common.documents.count', { count: collection.document_count })}</strong>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {collection.pending_count > 0 && <span className="portal-badge portal-badge-neutral">{t('admin.collections.status.pending', { count: collection.pending_count })}</span>}
              {collection.running_count > 0 && <span className="portal-badge portal-badge-working">{t('admin.collections.status.running', { count: collection.running_count })}</span>}
              {collection.review_count > 0 && <span className="portal-badge portal-badge-warning">{t('admin.collections.status.review', { count: collection.review_count })}</span>}
              {collection.failed_count > 0 && <span className="portal-badge portal-badge-error">{t('admin.collections.status.failed', { count: collection.failed_count })}</span>}
              {collection.released_count > 0 && <span className="portal-badge portal-badge-success">{t('admin.collections.status.released', { count: collection.released_count })}</span>}
            </div>
          </td>
          <td><Button variant="outline" size="sm" onClick={() => { setEditing(collection); setNotice(''); }} aria-label={t('admin.collections.editTitle', { name: collection.name })}><Pencil size={14} />{t('common.edit')}</Button></td>
        </tr>)}</tbody>
      </table></div> : <EmptyState title={query ? t('admin.collections.empty.searchTitle') : t('admin.collections.empty.title')}>
        {query ? t('admin.collections.empty.searchBody') : t('admin.collections.empty.body')}
      </EmptyState>}
      {page.total > PAGE_SIZE && <nav className="portal-pagination" aria-label={t('admin.collections.paginationAriaLabel')}>
        <span>{t('admin.collections.paginationRange', { from: offset + 1, to: Math.min(offset + PAGE_SIZE, page.total), total: page.total })}</span>
        <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => { setPage(null); setOffset(value => Math.max(0, value - PAGE_SIZE)); }}>{t('common.back')}</Button>
        <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= page.total} onClick={() => { setPage(null); setOffset(value => value + PAGE_SIZE); }}>{t('admin.collections.next')}</Button>
      </nav>}
    </section>}
    <p className="portal-field-hint">{t('admin.collections.releasedNote')}</p>
  </div>;
}

function CollectionEditor({ collection, onCancel, onSaved }: {
  collection: ManagedCollection; onCancel: () => void; onSaved: () => void;
}) {
  const { t } = useI18n();
  const heading = useRef<HTMLHeadingElement>(null);
  const [original, setOriginal] = useState<KnowledgeSpace | null>(null);
  const [name, setName] = useState(collection.name);
  const [description, setDescription] = useState(collection.description || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [accessOpen, setAccessOpen] = useState(false);

  useEffect(() => {
    heading.current?.focus();
    const controller = new AbortController();
    apiJson<KnowledgeSpace>(`/api/v1/collections/${encodeURIComponent(collection.collection_id)}`, { signal: controller.signal })
      .then(current => {
        if (controller.signal.aborted) return;
        setOriginal(current); setName(current.name); setDescription(current.description || ''); setError('');
      }).catch(err => { if (!controller.signal.aborted) setError(portalError(err)); });
    return () => controller.abort();
  }, [collection.collection_id, revision]);

  const canSave = Boolean(original && name.trim() && !saving);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    setSaving(true); setError('');
    try {
      await apiJson(`/api/v1/collections/${encodeURIComponent(collection.collection_id)}`, {
        ...jsonBody({ name: name.trim(), description: description.trim() }), method: 'PATCH',
      });
      onSaved();
    } catch (err) { setError(portalError(err)); setSaving(false); }
  }

  return <section className="portal-panel portal-form-panel" aria-labelledby="edit-collection-title">
    <h3 id="edit-collection-title" ref={heading} tabIndex={-1} className="text-[17px] font-semibold">{t('admin.collections.editTitle', { name: collection.name })}</h3>
    <p className="portal-field-hint">{t('admin.collections.ownerLine', { name: collection.owner?.username || t('admin.collections.noOwner') })}</p>
    {error && <Notice error action={!original ? () => setRevision(value => value + 1) : undefined}>{error}</Notice>}
    {!original && !error && <Notice>{t('admin.collections.editorLoading')}</Notice>}
    <form className="portal-form" onSubmit={save}>
      <label>{t('admin.collections.field.name')}<input required maxLength={255} value={name} disabled={!original || saving} onChange={event => setName(event.target.value)} /></label>
      <label>{t('admin.collections.field.description')}<textarea rows={3} value={description} disabled={!original || saving} onChange={event => setDescription(event.target.value)} /></label>
      <div className="portal-access-line">
        <span><small>{t('admin.collections.column.access')}</small><strong>{original ? accessSummary(original) : '…'}</strong></span>
        <Button type="button" variant="outline" size="sm" disabled={!original} onClick={() => setAccessOpen(true)}>{t('admin.collections.changeAccess')}</Button>
      </div>
      <p className="portal-field-hint">{t('admin.collections.accessHint')}</p>
      <div className="portal-form-actions"><Button type="submit" disabled={!canSave}>{saving ? t('admin.collections.saving') : t('admin.collections.saveChanges')}</Button>
        <Button type="button" variant="ghost" disabled={saving} onClick={onCancel}>{t('common.cancel')}</Button>
        <Link className="portal-inline-link" href={`/knowledge/${encodeURIComponent(collection.collection_id)}`}>{t('admin.collections.viewContents')}<ArrowRight size={14} /></Link>
      </div>
    </form>
    {accessOpen && original && <AccessDialog collection={original} onClose={() => setAccessOpen(false)} onSaved={updated => { setOriginal(updated); setAccessOpen(false); }} />}
  </section>;
}
