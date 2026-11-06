'use client';

import { useCallback, useEffect, useState } from 'react';
import { Bot, Pencil } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { BotEditor, type ManagedBot } from '@/components/admin/bots-tab';
import { listOwnedBots, loadOwnedBotConfig, type OwnedBot } from '@/lib/bots';
import { loadDirectoryTeams, portalError, type KnowledgeSpace, type TeamRef } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import { EmptyState, Notice, PortalPage } from './shared';

/** "Meine Bots" (ADR 0008): owners maintain every setting of their bots in
 * the same editor the administrators use; the n8n connection and the
 * owners stay with the administrators (the server keeps them). */
export function MyBots() {
  const { t, locale } = useI18n();
  const [bots, setBots] = useState<OwnedBot[] | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editing, setEditing] = useState<ManagedBot | null>(null);
  const [spaces, setSpaces] = useState<KnowledgeSpace[]>([]);
  const [teams, setTeams] = useState<TeamRef[]>([]);
  const load = useCallback(() => listOwnedBots()
    .then(data => { setBots(data.items); setError(''); })
    .catch(err => setError(portalError(err, locale))), [locale]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const controller = new AbortController();
    apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections', { signal: controller.signal }).then(data => setSpaces(data.items)).catch(() => setSpaces([]));
    loadDirectoryTeams(controller.signal).then(data => setTeams(data.items)).catch(() => setTeams([]));
    return () => controller.abort();
  }, []);
  async function edit(bot: OwnedBot) {
    setNotice(''); setError('');
    try { setEditing(await loadOwnedBotConfig(bot.id)); } catch (err) { setError(portalError(err, locale)); }
  }
  // Spaces an administrator attached stay listed even if the owner can't read them.
  const editorSpaces = editing ? [...spaces, ...[...editing.collections, ...(editing.agent?.subagents ?? []).flatMap(subagent => subagent.collections)]
    .filter((slug, index, all) => all.indexOf(slug) === index && !spaces.some(space => space.slug === slug))
    .map(slug => ({ slug, name: slug, collection_id: slug } as KnowledgeSpace))] : spaces;

  const usersSummary = (bot: OwnedBot) => bot.public
    ? t('portal.bots.usableByAll')
    : bot.grants.filter(grant => grant.role === 'user').map(grant => grant.team_id ? t('portal.access.team', { team: grant.name }) : grant.name).join(', ')
      || t('portal.bots.usableByOwners');

  return <PortalPage title={t('portal.nav.bots')} description={t('portal.bots.description')}>
    {notice && <Notice>{notice}</Notice>}
    {error && <Notice error action={load}>{error}</Notice>}
    {bots === null && !error ? <Notice>{t('portal.bots.loading')}</Notice> : bots?.length ? <div className="portal-space-grid">
      {bots.map(bot => <article className="portal-panel portal-space-card" key={bot.id}>
        <div className="portal-space-top">
          <span className="portal-space-mark" aria-hidden="true"><Bot size={18} /></span>
          <div><h2>{bot.name}</h2><p>{bot.description || bot.id}</p></div>
        </div>
        <p className="text-xs text-[var(--muted)]">{t('portal.bots.spacesCount', { count: bot.collections.length })}{!bot.enabled && ` · ${t('portal.bots.disabled')}`}</p>
        <div className="portal-access-line"><span><small>{t('portal.bots.usableBy')}</small><strong>{usersSummary(bot)}</strong></span></div>
        <div className="portal-space-actions"><Button variant="outline" size="sm" onClick={() => void edit(bot)} aria-label={t('portal.bots.editAria', { name: bot.name })}><Pencil size={14} aria-hidden="true" />{t('common.edit')}</Button></div>
      </article>)}
    </div> : !error && <EmptyState title={t('portal.bots.emptyTitle')}>{t('portal.bots.emptyBody')}</EmptyState>}
    {editing && <BotEditor owner bot={editing} teams={teams} spaces={editorSpaces} onClose={() => setEditing(null)} onSaved={async () => {
      setEditing(null);
      setNotice(t('portal.bots.savedNotice'));
      await load();
    }} />}
  </PortalPage>;
}
