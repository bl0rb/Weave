'use client';

import { useState } from 'react';
import { LoaderCircle } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { useAuth } from '@/lib/auth-context';
import { ErrorNotice, errorMessage, Field, inputClass, Modal } from '@/components/admin/admin-shared';
import type { ImportAuthType, ImportSource } from '@/lib/imports';
import { useImportSourceTest } from '@/lib/use-import-source-test';
import { useI18n } from '@/i18n/provider';

type WebsiteAuthType = Extract<ImportAuthType, 'none' | 'http_basic' | 'bearer'>;

/**
 * Website / intranet connection setup (POST /api/v1/import/sources with
 * kind 'website'). Public pages need no credentials; HTTP Basic and bearer
 * tokens are the supported schemes -- form logins, SSO/OAuth and Kerberos/
 * NTLM are listed as unsupported rather than silently failing later. Only
 * admins may release an internal (private-network) target, and only for
 * this connection's host. The new connection is tested right away, same as
 * the Confluence dialog.
 */
export function WebsiteSetupDialog({ onClose, onCreated, initialBaseUrl = '', initialName = '' }: {
  onClose: () => void; onCreated: (source: ImportSource) => void | Promise<void>; initialBaseUrl?: string; initialName?: string;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [name, setName] = useState(initialName || 'Website');
  const [baseUrl, setBaseUrl] = useState(initialBaseUrl);
  const [authType, setAuthType] = useState<WebsiteAuthType>('none');
  const [username, setUsername] = useState('');
  const [credential, setCredential] = useState('');
  const [allowPrivate, setAllowPrivate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ImportSource | null>(null);
  const { test, testingId, cooldownSecondsFor } = useImportSourceTest();
  const testing = testingId !== null;
  const cooldownSeconds = created ? cooldownSecondsFor(created.id) : 0;

  async function testCreated(source: ImportSource) {
    setError(null);
    const outcome = await test(source.id);
    if (outcome.kind === 'result' && outcome.result.ok) {
      await onCreated({ ...source, server_kind: outcome.result.server_kind ?? source.server_kind, last_validated_at: new Date().toISOString() });
      return;
    }
    if (outcome.kind === 'result') setError(`${t('portal.connections.confluence.testFailed')}${outcome.result.detail ? `: ${outcome.result.detail}` : ''}`);
    else setError(outcome.rateLimited ? errorMessage(outcome.error) : `${t('portal.connections.confluence.testFailed')}: ${errorMessage(outcome.error)}`);
  }

  const selectAuthType = (next: WebsiteAuthType) => {
    if (next !== authType) setCredential('');
    setAuthType(next);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const source = await apiJson<ImportSource>('/api/v1/import/sources', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(), kind: 'website', base_url: baseUrl.trim(), auth_type: authType,
          auth_username: authType === 'http_basic' ? username.trim() : '',
          credential: authType === 'none' ? '' : credential.trim(),
          allow_private_target: isAdmin && allowPrivate,
        }),
      });
      setCreated(source);
      setBusy(false);
      await testCreated(source);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  const authOption = (value: WebsiteAuthType, title: string, hint: string) => (
    <button type="button" aria-pressed={authType === value} onClick={() => selectAuthType(value)}
      className={`rounded-xl border p-3 text-left ${authType === value ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'}`}>
      <p className="text-sm font-semibold text-slate-950">{title}</p>
      <p className="mt-1 text-xs text-slate-600">{hint}</p>
    </button>
  );

  return (
    <Modal title={t('portal.connections.website.modalTitle')} onClose={created ? () => void onCreated(created) : onClose}>
      <form onSubmit={submit} className="space-y-4">
        <fieldset disabled={created !== null} className="min-w-0 space-y-4 border-0 p-0">
          <Field label={t('portal.connections.confluence.nameLabel')}>
            <input value={name} onChange={(e) => setName(e.target.value)} className={inputClass} required autoFocus placeholder="Intranet" />
          </Field>
          <Field label={t('portal.connections.confluence.baseUrlLabel')} hint={t('portal.connections.website.baseUrlHint')}>
            <input type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className={inputClass} required placeholder="https://intranet.example.com" />
          </Field>
          <div>
            <p className="text-sm font-medium text-slate-700">{t('portal.connections.confluence.authenticationLabel')}</p>
            <div className="mt-1 grid gap-3 sm:grid-cols-3">
              {authOption('none', t('portal.connections.website.authNoneTitle'), t('portal.connections.website.authNoneHint'))}
              {authOption('http_basic', t('portal.connections.website.authBasicTitle'), t('portal.connections.website.authBasicHint'))}
              {authOption('bearer', t('portal.connections.website.authBearerTitle'), t('portal.connections.website.authBearerHint'))}
            </div>
            <p className="mt-2 text-xs text-amber-800">{t('portal.connections.website.unsupported')}</p>
          </div>
          {authType === 'http_basic' && (
            <Field label={t('portal.connections.website.usernameLabel')}>
              <input value={username} onChange={(e) => setUsername(e.target.value)} className={inputClass} required autoComplete="off" />
            </Field>
          )}
          {authType !== 'none' && (
            <Field label={authType === 'http_basic' ? t('portal.connections.website.passwordLabel') : t('portal.connections.website.tokenLabel')} hint={t('portal.connections.confluence.credentialHint')}>
              <input value={credential} onChange={(e) => setCredential(e.target.value)} type="password" className={inputClass} required autoComplete="new-password" data-1p-ignore data-lpignore="true" />
            </Field>
          )}
          <label className="flex items-start gap-2 text-sm text-slate-700">
            <input type="checkbox" className="mt-1" checked={allowPrivate} disabled={!isAdmin} onChange={(e) => setAllowPrivate(e.target.checked)} />
            <span>{t('portal.connections.website.privateTargetLabel')}<span className="block text-xs text-slate-500">{isAdmin ? t('portal.connections.website.privateTargetHint') : t('portal.connections.website.privateTargetAdminOnly')}</span></span>
          </label>
        </fieldset>
        <ErrorNotice message={error} />
        {testing && (
          <p role="status" className="flex items-center gap-2 text-sm text-slate-600"><LoaderCircle className="h-4 w-4 animate-spin" />{t('portal.connections.confluence.testing')}</p>
        )}
        {created ? (
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={() => void onCreated(created)} disabled={testing}>{t('portal.connections.confluence.keepUntested')}</Button>
            <Button type="button" size="sm" onClick={() => void testCreated(created)} disabled={testing || cooldownSeconds > 0}>
              {testing && <LoaderCircle className="h-4 w-4 animate-spin" />}
              {cooldownSeconds > 0 ? t('portal.connections.confluence.testWithCooldown', { seconds: cooldownSeconds }) : t('portal.connections.confluence.test')}
            </Button>
          </div>
        ) : (
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={busy}>{t('portal.connections.confluence.cancel')}</Button>
            <Button type="submit" size="sm" disabled={busy}>{busy && <LoaderCircle className="h-4 w-4 animate-spin" />}{t('portal.connections.website.add')}</Button>
          </div>
        )}
      </form>
    </Modal>
  );
}
