'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { ArrowRight, Pencil, Plus, RefreshCw } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button, buttonVariants } from '@/components/ui/button';
import { jsonBody, portalError, type KnowledgeSpace } from '@/lib/portal';
import { EmptyState, Notice } from '@/components/portal/shared';

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
type Team = { id: string; name: string };
const PAGE_SIZE = 20;

export function CollectionsTab() {
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
      <div><h2 className="text-xl font-semibold">Alle Wissensbereiche</h2>
        <p className="mt-2 max-w-2xl text-sm text-slate-600">Verwalte Wissensbereiche und Berechtigte über alle Eigentümer hinweg. Der Bearbeitungsstand zeigt, wo Arbeit wartet.</p>
      </div>
      <Link href="/knowledge/new" className={buttonVariants({ variant: 'outline' })}><Plus size={16} />Wissensbereich anlegen</Link>
    </div>
    {notice && <Notice>{notice}</Notice>}
    {editing && <CollectionEditor key={editing.collection_id} collection={editing} onCancel={() => setEditing(null)}
      onSaved={() => { setEditing(null); setNotice('Wissensbereich gespeichert.'); reload(); }} />}
    <form onSubmit={search} className="mb-6 flex flex-wrap items-end gap-3">
      <label className="min-w-56 flex-1">Wissensbereiche finden
        <input type="search" value={draft} onChange={event => setDraft(event.target.value)} placeholder="Name oder Beschreibung" />
      </label>
      <Button type="submit" variant="outline">Suchen</Button>
      <Button type="button" variant="ghost" onClick={reload}><RefreshCw size={15} />Aktualisieren</Button>
    </form>
    {error && <Notice error action={reload}>{error}</Notice>}
    {!page && !error && <Notice>Wissensbereiche werden geladen …</Notice>}
    {page && <section className="portal-panel" aria-label="Verwaltung aller Wissensbereiche">
      <div className="portal-section-heading"><h3 className="font-semibold">{page.total} Wissensbereiche{query ? ' gefunden' : ''}</h3></div>
      {page.items.length ? <div className="portal-table-scroll"><table className="portal-table">
        <thead><tr><th scope="col">Wissensbereich</th><th scope="col">Eigentümer</th><th scope="col">Berechtigte</th><th scope="col">Bearbeitungsstand</th><th scope="col">Verwaltung</th></tr></thead>
        <tbody>{page.items.map(collection => <tr key={collection.collection_id}>
          <td className="min-w-48"><Link href={`/knowledge/${encodeURIComponent(collection.collection_id)}`} className="font-semibold text-emerald-800 hover:underline">{collection.name}</Link>
            {collection.description && <p className="mt-1 line-clamp-2 text-xs text-slate-500">{collection.description}</p>}
          </td>
          <td>{collection.owner?.username || 'Kein Eigentümer'}</td>
          <td className="min-w-36">{collection.read_teams.length ? collection.read_teams.join(', ') : 'Alle angemeldeten Teams'}</td>
          <td className="min-w-48"><strong className="block font-medium">{collection.document_count} Dokumente</strong>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {collection.pending_count > 0 && <span className="portal-badge portal-badge-neutral">{collection.pending_count} warten</span>}
              {collection.running_count > 0 && <span className="portal-badge portal-badge-working">{collection.running_count} in Verarbeitung</span>}
              {collection.review_count > 0 && <span className="portal-badge portal-badge-warning">{collection.review_count} zur Prüfung</span>}
              {collection.failed_count > 0 && <span className="portal-badge portal-badge-error">{collection.failed_count} fehlgeschlagen</span>}
              {collection.released_count > 0 && <span className="portal-badge portal-badge-success">{collection.released_count} freigegeben</span>}
            </div>
          </td>
          <td><Button variant="outline" size="sm" onClick={() => { setEditing(collection); setNotice(''); }} aria-label={`${collection.name} bearbeiten`}><Pencil size={14} />Bearbeiten</Button></td>
        </tr>)}</tbody>
      </table></div> : <EmptyState title={query ? 'Kein passender Wissensbereich' : 'Noch keine Wissensbereiche'}>
        {query ? 'Versuche einen anderen Suchbegriff.' : 'Nutzer können im Portal ihren ersten Wissensbereich anlegen.'}
      </EmptyState>}
      {page.total > PAGE_SIZE && <nav className="portal-pagination" aria-label="Wissensbereiche blättern">
        <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} von {page.total}</span>
        <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => { setPage(null); setOffset(value => Math.max(0, value - PAGE_SIZE)); }}>Zurück</Button>
        <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= page.total} onClick={() => { setPage(null); setOffset(value => value + PAGE_SIZE); }}>Weiter</Button>
      </nav>}
    </section>}
    <p className="portal-field-hint">„Freigegeben“ bedeutet, dass ein geprüfter Stand zur Übergabe bereitsteht. Ob die Indexierung abgeschlossen ist, lässt sich daraus nicht ableiten.</p>
  </div>;
}

function CollectionEditor({ collection, onCancel, onSaved }: {
  collection: ManagedCollection; onCancel: () => void; onSaved: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [original, setOriginal] = useState<KnowledgeSpace | null>(null);
  const [teams, setTeams] = useState<Team[]>([]);
  const [name, setName] = useState(collection.name);
  const [description, setDescription] = useState(collection.description || '');
  const [selected, setSelected] = useState<string[]>(collection.read_teams);
  const [shareAll, setShareAll] = useState(collection.read_teams.length === 0);
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    heading.current?.focus();
    const controller = new AbortController();
    Promise.all([
      apiJson<KnowledgeSpace>(`/api/v1/collections/${encodeURIComponent(collection.collection_id)}`, { signal: controller.signal }),
      apiJson<{ items: Team[] }>('/api/v1/auth/admin/teams', { signal: controller.signal }),
    ]).then(([current, available]) => {
      if (controller.signal.aborted) return;
      setOriginal(current); setName(current.name); setDescription(current.description || '');
      setSelected(current.read_teams); setShareAll(current.read_teams.length === 0); setTeams(available.items); setError('');
    }).catch(err => { if (!controller.signal.aborted) setError(portalError(err)); });
    return () => controller.abort();
  }, [collection.collection_id, revision]);

  const teamNames = [...new Set([...teams.map(team => team.name), ...(original?.read_teams || [])])].sort((a, b) => a.localeCompare(b, 'de'));
  const widensToEveryone = shareAll && Boolean(original?.read_teams.length);
  const canSave = Boolean(original && name.trim() && !saving && (shareAll ? !widensToEveryone || confirmed : selected.length > 0));

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    setSaving(true); setError('');
    try {
      await apiJson(`/api/v1/collections/${encodeURIComponent(collection.collection_id)}`, {
        ...jsonBody({ name: name.trim(), description: description.trim(), read_teams: shareAll ? [] : selected }), method: 'PATCH',
      });
      onSaved();
    } catch (err) { setError(portalError(err)); setSaving(false); }
  }

  return <section className="portal-panel portal-form-panel" aria-labelledby="edit-collection-title">
    <h3 id="edit-collection-title" ref={heading} tabIndex={-1} className="text-lg font-semibold">{collection.name} bearbeiten</h3>
    <p className="portal-field-hint">Eigentümer: {collection.owner?.username || 'Kein Eigentümer'}</p>
    {error && <Notice error action={!original ? () => setRevision(value => value + 1) : undefined}>{error}</Notice>}
    {!original && !error && <Notice>Einstellungen werden geladen …</Notice>}
    <form className="portal-form" onSubmit={save}>
      <label>Name<input required maxLength={255} value={name} disabled={!original || saving} onChange={event => setName(event.target.value)} /></label>
      <label>Beschreibung<textarea rows={3} value={description} disabled={!original || saving} onChange={event => setDescription(event.target.value)} /></label>
      <fieldset disabled={!original || saving}>
        <legend>Berechtigte</legend>
        <label className="portal-choice"><input type="radio" name="admin-readers" checked={!shareAll} onChange={() => { setShareAll(false); setConfirmed(false); }} />Ausgewählte Teams</label>
        {!shareAll && <div className="ml-6">
          {teamNames.map(team => <label className="portal-choice" key={team}><input type="checkbox" checked={selected.includes(team)}
            onChange={event => setSelected(current => event.target.checked ? [...current, team] : current.filter(value => value !== team))} />
            {team}{!teams.some(item => item.name === team) && <span className="text-xs text-slate-500">Bestehender Eintrag</span>}
          </label>)}
          {!selected.length && <p className="portal-field-hint">Wähle mindestens ein Team. Ohne Auswahl wird nicht gespeichert.</p>}
          {!teamNames.length && <p className="portal-field-hint">Lege zuerst unter „Teams“ ein Team an.</p>}
        </div>}
        <label className="portal-choice"><input type="radio" name="admin-readers" checked={shareAll} onChange={() => setShareAll(true)} />Alle angemeldeten Teams</label>
        {widensToEveryone && <label className="portal-choice portal-access-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />Ich bestätige, dass freigegebene Inhalte allen angemeldeten Teams zur Verfügung stehen dürfen.</label>}
      </fieldset>
      <p className="portal-field-hint">Leserechte gelten für Chat, Bots und Suche. Die Administration und die Eigentümer behalten die Verwaltung. Änderungen werden an die Suche weitergegeben.</p>
      <div className="portal-form-actions"><Button type="submit" disabled={!canSave}>{saving ? 'Wird gespeichert …' : 'Änderungen speichern'}</Button>
        <Button type="button" variant="ghost" disabled={saving} onClick={onCancel}>Abbrechen</Button>
        <Link className="portal-inline-link" href={`/knowledge/${encodeURIComponent(collection.collection_id)}`}>Inhalte ansehen<ArrowRight size={14} /></Link>
      </div>
    </form>
  </section>;
}
