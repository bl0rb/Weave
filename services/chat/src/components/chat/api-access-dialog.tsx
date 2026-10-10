'use client';

import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Copy, KeyRound, Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useI18n } from '@/i18n/provider';

type ApiToken = { id: string; label: string; created_at: string; expires_at: string | null; last_used_at: string | null };
type CreatedToken = ApiToken & { token: string };

const EXPIRY_DAYS = ['30', '90', '365', 'never'] as const;

type TokenList = { items: ApiToken[]; forbidden: boolean; failed: boolean };

/** GET /api/tokens without touching state, so effects and handlers share it. */
async function readTokens(): Promise<TokenList> {
  const response = await fetch('/api/tokens').catch(() => null);
  if (response?.status === 403) return { items: [], forbidden: true, failed: false };
  if (!response?.ok) return { items: [], forbidden: false, failed: true };
  const body = await response.json().catch(() => null);
  return { items: (body?.items ?? []) as ApiToken[], forbidden: false, failed: false };
}

/**
 * "API-Zugang": self-service personal API tokens for scripts and
 * integrations (Weave-API's /v1/me/tokens via /api/tokens). A new token
 * is shown exactly once. Only a session signed in through Weave-Ingest may
 * manage tokens -- a token login gets 403 and an explanation instead.
 */
export function ApiAccessDialog({ onClose }: { onClose: () => void }) {
  const { t, locale } = useI18n();
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState('');
  const [label, setLabel] = useState('');
  const [expiry, setExpiry] = useState<(typeof EXPIRY_DAYS)[number]>('90');
  const [created, setCreated] = useState<CreatedToken | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const apply = useCallback((list: TokenList) => {
    setTokens(list.items);
    setForbidden(list.forbidden);
    if (list.failed) setError(t('chat.apiAccess.loadFailed'));
  }, [t]);
  const load = useCallback(() => readTokens().then(apply), [apply]);
  useEffect(() => {
    let active = true;
    readTokens().then(list => { if (active) apply(list); });
    return () => { active = false; };
  }, [apply]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) { if (event.key === 'Escape') onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!label.trim() || busy) return;
    setBusy(true); setError(''); setCopied(false);
    const response = await fetch('/api/tokens', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: label.trim(), expires_in_days: expiry === 'never' ? null : Number(expiry) }),
    }).catch(() => null);
    setBusy(false);
    if (!response?.ok) {
      setError(response?.status === 409 ? t('chat.apiAccess.limitReached') : t('chat.apiAccess.createFailed'));
      return;
    }
    setCreated(await response.json());
    setLabel('');
    await load();
  }

  async function revoke(token: ApiToken) {
    setError('');
    const response = await fetch(`/api/tokens/${encodeURIComponent(token.id)}`, { method: 'DELETE' }).catch(() => null);
    if (!response?.ok) { setError(t('chat.apiAccess.revokeFailed')); return; }
    if (created?.id === token.id) setCreated(null);
    await load();
  }

  async function copy() {
    if (!created) return;
    await navigator.clipboard?.writeText(created.token).catch(() => {});
    setCopied(true);
  }

  const date = (value: string | null) => value ? new Date(value).toLocaleDateString(locale === 'en' ? 'en-GB' : 'de-DE') : '—';

  return <div className="fixed inset-0 z-50 flex items-center justify-center p-0 sm:p-4">
    <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden="true" />
    <div role="dialog" aria-modal="true" aria-labelledby="api-access-title"
      className="relative flex max-h-[90dvh] w-full max-w-2xl flex-col overflow-y-auto rounded-xl border border-[var(--line)] bg-[var(--surface)] shadow-2xl">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
        <h2 id="api-access-title" className="flex items-center gap-2 text-[17px] font-semibold"><KeyRound className="h-4 w-4" aria-hidden="true" />{t('chat.apiAccess.title')}</h2>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label={t('chat.apiAccess.close')}><X className="h-4 w-4" aria-hidden="true" /></Button>
      </div>
      <div className="space-y-5 p-5 text-sm">
        <p className="text-[var(--muted)]">{t('chat.apiAccess.intro')}</p>
        {error && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-red-700">{error}</p>}
        {forbidden ? <p role="status" className="rounded-lg bg-amber-50 px-3 py-2 text-amber-800">{t('chat.apiAccess.sessionRequired')}</p> : <>
          {created && <div role="status" className="space-y-2 rounded-lg border border-[var(--accent)] bg-[var(--accent-soft)] p-3">
            <p className="font-semibold">{t('chat.apiAccess.createdOnce', { label: created.label })}</p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded bg-[var(--surface)] px-2 py-1 text-xs">{created.token}</code>
              <Button variant="outline" size="sm" onClick={copy}><Copy className="h-3.5 w-3.5" aria-hidden="true" />{copied ? t('chat.apiAccess.copied') : t('chat.apiAccess.copy')}</Button>
            </div>
          </div>}
          <form onSubmit={create} className="flex flex-wrap items-end gap-2">
            <label className="flex min-w-48 flex-1 flex-col gap-1 font-medium">{t('chat.apiAccess.label')}
              <input className="h-9 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 font-normal outline-none focus-visible:border-[var(--accent)] focus-visible:ring-[3px] focus-visible:ring-[var(--accent-soft)]" value={label} maxLength={100}
                onChange={event => setLabel(event.target.value)} placeholder={t('chat.apiAccess.labelPlaceholder')} />
            </label>
            <label className="flex flex-col gap-1 font-medium">{t('chat.apiAccess.expiry')}
              <select className="h-9 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-2 font-normal outline-none focus-visible:border-[var(--accent)] focus-visible:ring-[3px] focus-visible:ring-[var(--accent-soft)]" value={expiry} onChange={event => setExpiry(event.target.value as (typeof EXPIRY_DAYS)[number])}>
                {EXPIRY_DAYS.map(value => <option key={value} value={value}>{t(`chat.apiAccess.expiry.${value}`)}</option>)}
              </select>
            </label>
            <Button type="submit" disabled={!label.trim() || busy}>{t('chat.apiAccess.create')}</Button>
          </form>
          {tokens === null ? <p className="text-[var(--muted)]">{t('chat.apiAccess.loading')}</p> : tokens.length === 0
            ? <p className="text-[var(--muted)]">{t('chat.apiAccess.empty')}</p>
            : <ul className="divide-y divide-[var(--line)]" aria-label={t('chat.apiAccess.listLabel')}>
              {tokens.map(token => <li key={token.id} className="flex items-center justify-between gap-3 py-2">
                <span className="min-w-0">
                  <span className="block font-medium">{token.label}</span>
                  <span className="block text-xs text-[var(--muted)]">{t('chat.apiAccess.meta', { created: date(token.created_at), expires: token.expires_at ? date(token.expires_at) : t('chat.apiAccess.expiry.never'), used: date(token.last_used_at) })}</span>
                </span>
                <Button variant="ghost" size="sm" onClick={() => void revoke(token)} aria-label={t('chat.apiAccess.revokeAria', { label: token.label })}><Trash2 className="h-3.5 w-3.5" aria-hidden="true" /></Button>
              </li>)}
            </ul>}
        </>}
        <p className="text-xs text-[var(--muted)]">{t('chat.apiAccess.docsHint')}</p>
      </div>
    </div>
  </div>;
}
