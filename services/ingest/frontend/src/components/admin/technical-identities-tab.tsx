'use client';

import { useState, type FormEvent } from 'react';
import { Check, ChevronDown, ChevronRight, Copy, KeyRound, Plus, RefreshCw, ShieldOff } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import type { KnowledgeSpace } from '@/lib/portal';
import {
  Badge,
  ConfirmDialog,
  ErrorNotice,
  Field,
  LoadingState,
  Modal,
  SectionCard,
  apiSend,
  errorMessage,
  inputClass,
  useAdminList,
} from './admin-shared';
import { useI18n } from '@/i18n/provider';
import type { MessageKey, MessageVars } from '@/i18n/messages';

type TechnicalIdentity = {
  id: string;
  name: string;
  description: string | null;
  allowed_collections: string[];
  enabled: boolean;
  token_prefix: string;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_by: string | null;
};

type TechnicalIdentityCreateResponse = TechnicalIdentity & { token: string };

type AuditEntry = { id: string; event: string; actor: string | null; details: Record<string, unknown>; created_at: string };

const BASE_PATH = '/api/v1/auth/admin/technical-identities';

/**
 * Kept as a module-level function (not nested in the component) so its
 * `Date.now()` read isn't flagged as an impure call during render by
 * react-hooks/purity -- `t` is threaded in explicitly instead of closing
 * over it.
 */
function statusOf(item: TechnicalIdentity, t: (key: MessageKey, vars?: MessageVars) => string): { label: string; tone: 'emerald' | 'slate' | 'amber' } {
  if (item.revoked_at) return { label: t('admin.identities.status.revoked'), tone: 'slate' };
  if (item.expires_at && new Date(item.expires_at).getTime() <= Date.now()) return { label: t('admin.identities.status.expired'), tone: 'amber' };
  if (!item.enabled) return { label: t('admin.identities.status.disabled'), tone: 'slate' };
  return { label: t('admin.identities.status.active'), tone: 'emerald' };
}

export function TechnicalIdentitiesTab() {
  const { t, formatDate } = useI18n();
  const identities = useAdminList<TechnicalIdentity>(BASE_PATH);
  const spaces = useAdminList<KnowledgeSpace>('/api/v1/collections');
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<TechnicalIdentity | null>(null);
  const [rotating, setRotating] = useState<TechnicalIdentity | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [issuedToken, setIssuedToken] = useState<TechnicalIdentityCreateResponse | null>(null);
  const [notice, setNotice] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);

  const loading = identities.loading || spaces.loading;
  const error = identities.error || spaces.error;

  function fmtDate(value: string | null): string {
    return value ? formatDate(value, { dateStyle: 'medium', timeStyle: 'short' }) : '–';
  }

  async function rotate(item: TechnicalIdentity) {
    setActionError(null);
    try {
      const rotated = await apiJson<TechnicalIdentityCreateResponse>(`${BASE_PATH}/${encodeURIComponent(item.id)}/rotate`, {
        method: 'POST',
      });
      setIssuedToken(rotated);
      setNotice('');
      await identities.reload();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  }

  return <div className="space-y-6">
    <SectionCard
      title={t('admin.identities.title')}
      description={t('admin.identities.description')}
      actions={<Button variant="outline" size="sm" onClick={() => { setCreating(true); setNotice(''); }}><Plus size={15} />{t('admin.identities.create')}</Button>}
    >
      <ErrorNotice message={error} />
      <ErrorNotice message={actionError} />
      {notice && <p role="status" className="mb-4 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</p>}
      {loading ? <LoadingState label={t('admin.identities.loading')} /> : identities.items.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <KeyRound className="h-9 w-9 text-slate-300" />
          <div><p className="font-medium text-slate-800">{t('admin.identities.emptyTitle')}</p><p className="mt-1 text-sm text-slate-500">{t('admin.identities.emptyBody')}</p></div>
          <Button variant="outline" size="sm" onClick={() => setCreating(true)}><Plus size={15} />{t('admin.identities.createFirst')}</Button>
        </div>
      ) : <ul className="divide-y divide-slate-100">
        {identities.items.map(item => {
          const status = statusOf(item, t);
          const isExpanded = expanded === item.id;
          return <li key={item.id} className="py-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <KeyRound size={18} className="text-emerald-700" />
                  <h3 className="font-semibold text-slate-950">{item.name}</h3>
                  <Badge tone={status.tone}>{status.label}</Badge>
                  <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">{item.token_prefix}…</code>
                </div>
                {item.description && <p className="mt-1 text-sm text-slate-600">{item.description}</p>}
                <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
                  <span>{t('admin.identities.knowledgeSpaces', { list: item.allowed_collections.length ? item.allowed_collections.join(', ') : t('admin.identities.none') })}</span>
                  <span>{t('admin.identities.lastUsed', { date: fmtDate(item.last_used_at) })}</span>
                  {item.expires_at && <span>{t('admin.identities.expiresAt', { date: fmtDate(item.expires_at) })}</span>}
                </div>
              </div>
              <div className="flex flex-wrap gap-1">
                <Button variant="ghost" size="sm" onClick={() => setRotating(item)} aria-label={t('admin.identities.rotateAria', { name: item.name })}><RefreshCw size={15} />{t('admin.identities.rotate')}</Button>
                {!item.revoked_at && <Button variant="ghost" size="sm" onClick={() => setRevoking(item)} aria-label={t('admin.identities.revokeAria', { name: item.name })}><ShieldOff size={15} />{t('admin.identities.revoke')}</Button>}
                <Button variant="ghost" size="sm" onClick={() => setExpanded(isExpanded ? null : item.id)} aria-label={isExpanded ? t('admin.identities.auditCollapseAria', { name: item.name }) : t('admin.identities.auditExpandAria', { name: item.name })}>
                  {isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}{t('admin.identities.audit')}
                </Button>
              </div>
            </div>
            {isExpanded && <AuditList identityId={item.id} />}
          </li>;
        })}
      </ul>}
    </SectionCard>

    {creating && <CreateDialog
      spaces={spaces.items}
      onClose={() => setCreating(false)}
      onCreated={async created => {
        setCreating(false);
        setIssuedToken(created);
        await identities.reload();
      }}
    />}

    {issuedToken && <TokenDisplay identity={issuedToken} onClose={() => { setIssuedToken(null); setNotice(t('admin.identities.tokenSavedNotice')); }} />}

    {revoking && <ConfirmDialog
      title={t('admin.identities.revokeTitle')}
      body={<p>{t('admin.identities.revokeBodyPrefix')} <strong className="text-slate-950">{revoking.name}</strong>{t('admin.identities.revokeBodySuffix')}</p>}
      confirmLabel={t('admin.identities.revokeTitle')}
      onClose={() => setRevoking(null)}
      onConfirm={async () => {
        await apiSend(`${BASE_PATH}/${encodeURIComponent(revoking.id)}/revoke`, { method: 'POST' });
        setRevoking(null);
        setNotice(t('admin.identities.revokedNotice'));
        await identities.reload();
      }}
    />}

    {rotating && <ConfirmDialog
      title={t('admin.identities.rotate')}
      body={<p>{t('admin.identities.rotateBodyPrefix')} <strong className="text-slate-950">{rotating.name}</strong>{t('admin.identities.rotateBodySuffix')}</p>}
      confirmLabel={t('admin.identities.rotate')}
      onClose={() => setRotating(null)}
      onConfirm={async () => {
        await rotate(rotating);
        setRotating(null);
      }}
    />}
  </div>;
}

function CreateDialog({ spaces, onClose, onCreated }: {
  spaces: KnowledgeSpace[];
  onClose: () => void;
  onCreated: (created: TechnicalIdentityCreateResponse) => Promise<void>;
}) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [collections, setCollections] = useState<string[]>([]);
  const [expiresInDays, setExpiresInDays] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canSave = Boolean(name.trim() && !saving);

  const toggle = (slug: string) => setCollections(current => current.includes(slug) ? current.filter(item => item !== slug) : [...current, slug]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    setSaving(true); setError(null);
    try {
      const created = await apiJson<TechnicalIdentityCreateResponse>(BASE_PATH, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          description: description || null,
          allowed_collections: collections,
          expires_in_days: expiresInDays.trim() ? Number(expiresInDays) : null,
        }),
      });
      await onCreated(created);
    } catch (err) { setError(errorMessage(err)); setSaving(false); }
  }

  return <Modal title={t('admin.identities.createModalTitle')} onClose={onClose}>
    <ErrorNotice message={error} />
    <form className="space-y-5" onSubmit={save}>
      <Field label={t('admin.identities.nameLabel')} hint={t('admin.identities.nameHint')}><input className={inputClass} required value={name} onChange={event => setName(event.target.value)} placeholder={t('admin.identities.namePlaceholder')} /></Field>
      <Field label={t('admin.identities.descriptionLabel')}><textarea className={inputClass} rows={2} value={description} onChange={event => setDescription(event.target.value)} /></Field>
      <Field label={t('admin.identities.expiryLabel')} hint={t('admin.identities.expiryHint')}><input className={inputClass} type="number" min={1} max={3650} value={expiresInDays} onChange={event => setExpiresInDays(event.target.value)} placeholder={t('admin.identities.expiryPlaceholder')} /></Field>
      <fieldset>
        <legend className="text-sm font-medium text-slate-700">{t('admin.identities.knowledgeSpacesLegend')}</legend>
        <p className="mt-1 text-xs text-slate-400">{t('admin.identities.knowledgeSpacesHint')}</p>
        {spaces.length ? <div className="mt-2 grid max-h-36 gap-2 overflow-y-auto rounded-xl border border-slate-200 p-3 sm:grid-cols-2">
          {spaces.map(space => <label key={space.slug} className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={collections.includes(space.slug)} onChange={() => toggle(space.slug)} />{space.slug}
          </label>)}
        </div> : <p className="mt-2 text-sm text-amber-700">{t('admin.identities.noSpacesAvailable')}</p>}
      </fieldset>
      <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-4">
        <Button type="button" variant="outline" onClick={onClose} disabled={saving}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={!canSave}>{saving ? t('admin.identities.creating') : t('admin.identities.save')}</Button>
      </div>
    </form>
  </Modal>;
}

function TokenDisplay({ identity, onClose }: { identity: TechnicalIdentityCreateResponse; onClose: () => void }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(identity.token);
      setCopied(true);
    } catch {
      // Clipboard API unavailable — the token stays visible/selectable.
    }
  }

  return <Modal title={t('admin.identities.tokenModalTitle', { name: identity.name })} onClose={onClose}>
    <div className="rounded-xl border border-emerald-300 bg-emerald-50 p-4">
      <p className="text-sm font-semibold text-emerald-900">{t('admin.identities.tokenWarning')}</p>
      <div className="mt-2 flex items-center gap-2">
        <code className="flex-1 overflow-x-auto rounded-lg border border-emerald-200 bg-white px-3 py-2 text-xs text-slate-950">{identity.token}</code>
        <Button type="button" size="sm" variant="outline" onClick={copy}>
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          {copied ? t('admin.identities.copied') : t('admin.identities.copy')}
        </Button>
      </div>
      <p className="mt-2 text-xs font-medium text-emerald-800">{t('admin.identities.afterCloseWarning', { prefix: identity.token_prefix })}</p>
    </div>
    <div className="mt-4 flex justify-end"><Button type="button" onClick={onClose}>{t('admin.identities.understood')}</Button></div>
  </Modal>;
}

function AuditList({ identityId }: { identityId: string }) {
  const { t, formatDate } = useI18n();
  const audit = useAdminList<AuditEntry>(`${BASE_PATH}/${encodeURIComponent(identityId)}/audit`);
  if (audit.loading) return <p className="mt-2 pl-6 text-xs text-slate-400">{t('admin.identities.auditLoading')}</p>;
  if (!audit.items.length) return <p className="mt-2 pl-6 text-xs text-slate-400">{t('admin.identities.auditEmpty')}</p>;
  return <ul className="mt-3 space-y-1 border-t border-slate-100 pl-6 pt-3 text-xs text-slate-500">
    {audit.items.map(entry => <li key={entry.id}>
      <span className="font-medium text-slate-700">{entry.event}</span>
      {entry.actor && <span> · {entry.actor}</span>}
      <span> · {formatDate(entry.created_at, { dateStyle: 'medium', timeStyle: 'short' })}</span>
    </li>)}
  </ul>;
}
