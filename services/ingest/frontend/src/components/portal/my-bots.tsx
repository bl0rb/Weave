'use client';

import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Bot, Pencil } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Modal, inputClass } from '@/components/admin/admin-shared';
import { BotSharing } from '@/components/bot-sharing';
import { listOwnedBots, toGrantInputs, updateOwnedBot, type BotGrant, type OwnedBot } from '@/lib/bots';
import { loadDirectoryTeams, portalError, type KnowledgeSpace, type TeamRef } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import { EmptyState, Notice, PortalPage } from './shared';

/** "Meine Bots" (ADR 0008): owners maintain the content and the users of
 * their bots; the connection and the owners stay with the administrators. */
export function MyBots() {
  const { t, locale } = useI18n();
  const [bots, setBots] = useState<OwnedBot[] | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editing, setEditing] = useState<OwnedBot | null>(null);
  const load = useCallback(() => listOwnedBots()
    .then(data => { setBots(data.items); setError(''); })
    .catch(err => setError(portalError(err, locale))), [locale]);
  useEffect(() => { void load(); }, [load]);

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
        <div className="portal-space-actions"><Button variant="outline" size="sm" onClick={() => { setEditing(bot); setNotice(''); }} aria-label={t('portal.bots.editAria', { name: bot.name })}><Pencil size={14} aria-hidden="true" />{t('common.edit')}</Button></div>
      </article>)}
    </div> : !error && <EmptyState title={t('portal.bots.emptyTitle')}>{t('portal.bots.emptyBody')}</EmptyState>}
    {editing && <OwnedBotEditor bot={editing} onClose={() => setEditing(null)} onSaved={updated => {
      setBots(current => current?.map(bot => bot.id === updated.id ? updated : bot) ?? current);
      setEditing(null);
      setNotice(t('portal.bots.savedNotice'));
    }} />}
  </PortalPage>;
}

function OwnedBotEditor({ bot, onClose, onSaved }: { bot: OwnedBot; onClose: () => void; onSaved: (bot: OwnedBot) => void }) {
  const { t, locale } = useI18n();
  const [description, setDescription] = useState(bot.description ?? '');
  const [systemPrompt, setSystemPrompt] = useState(bot.system_prompt ?? '');
  const [collections, setCollections] = useState<string[]>(bot.collections);
  const [requireSources, setRequireSources] = useState(bot.require_sources);
  const [noContextReply, setNoContextReply] = useState(bot.no_context_reply);
  const [isPublic, setIsPublic] = useState(bot.public);
  const [grants, setGrants] = useState<BotGrant[]>(bot.grants);
  const [spaces, setSpaces] = useState<KnowledgeSpace[]>([]);
  const [teams, setTeams] = useState<TeamRef[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    apiJson<{ items: KnowledgeSpace[] }>('/api/v1/collections', { signal: controller.signal }).then(data => setSpaces(data.items)).catch(() => setSpaces([]));
    loadDirectoryTeams(controller.signal).then(data => setTeams(data.items)).catch(() => setTeams([]));
    return () => controller.abort();
  }, []);

  // Spaces an administrator attached stay listed even if the owner can't read them.
  const choices = [...spaces.map(space => ({ slug: space.slug, name: space.name })), ...bot.collections
    .filter(slug => !spaces.some(space => space.slug === slug)).map(slug => ({ slug, name: slug }))];
  const canSave = !saving && (bot.kind !== 'llm' || Boolean(systemPrompt.trim())) && (!requireSources || Boolean(noContextReply.trim()));

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    setSaving(true); setError('');
    try {
      onSaved(await updateOwnedBot(bot.id, {
        description,
        ...(bot.kind === 'llm' ? { system_prompt: systemPrompt } : {}),
        collections,
        require_sources: requireSources,
        no_context_reply: noContextReply,
        public: isPublic,
        grants: toGrantInputs(grants.filter(grant => grant.role === 'user')),
      }));
    } catch (err) { setError(portalError(err, locale)); setSaving(false); }
  }

  return <Modal size="lg" title={t('portal.bots.editTitle', { name: bot.name })} onClose={onClose}>
    {error && <Notice error>{error}</Notice>}
    <form className="space-y-4" onSubmit={save}>
      <label className="block text-sm font-medium text-[var(--ink-2)]">{t('common.description')}<textarea rows={2} maxLength={4000} className={inputClass} value={description} disabled={saving} onChange={event => setDescription(event.target.value)} /></label>
      {bot.kind === 'llm' && <label className="block text-sm font-medium text-[var(--ink-2)]">{t('portal.bots.systemPrompt')}<textarea required rows={5} maxLength={12000} className={inputClass} value={systemPrompt} disabled={saving} onChange={event => setSystemPrompt(event.target.value)} /></label>}
      <fieldset disabled={saving}>
        <legend className="text-sm font-medium text-[var(--ink-2)]">{t('portal.bots.spaces')}</legend>
        <p className="portal-field-hint">{t('portal.bots.spacesHint')}</p>
        <div className="mt-2 grid max-h-40 gap-2 overflow-y-auto sm:grid-cols-2">
          {choices.map(choice => <label key={choice.slug} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={collections.includes(choice.slug)}
            onChange={event => setCollections(current => event.target.checked ? [...current, choice.slug] : current.filter(slug => slug !== choice.slug))} />{choice.name}</label>)}
        </div>
      </fieldset>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={requireSources} disabled={saving} onChange={event => setRequireSources(event.target.checked)} />{t('portal.bots.requireSources')}</label>
      {requireSources && <label className="block text-sm font-medium text-[var(--ink-2)]">{t('portal.bots.noContextReply')}<textarea required rows={2} maxLength={2000} className={inputClass} value={noContextReply} disabled={saving} onChange={event => setNoContextReply(event.target.value)} /></label>}
      <BotSharing isPublic={isPublic} onPublicChange={setIsPublic} grants={grants} onGrantsChange={setGrants} teams={teams} canEditOwners={false} disabled={saving} />
      <div className="flex flex-wrap justify-end gap-2"><Button type="button" variant="outline" onClick={onClose} disabled={saving}>{t('common.cancel')}</Button><Button type="submit" disabled={!canSave}>{saving ? t('portal.access.dialog.saving') : t('common.save')}</Button></div>
    </form>
  </Modal>;
}
