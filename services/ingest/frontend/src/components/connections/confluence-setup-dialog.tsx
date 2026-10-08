'use client';

import { useState } from 'react';
import { LoaderCircle } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import { ErrorNotice, errorMessage, Field, inputClass, Modal } from '@/components/admin/admin-shared';
import type { ImportAuthType, ImportSource } from '@/lib/imports';
import { useI18n } from '@/i18n/provider';

/**
 * First-time Confluence setup: creates the signed-in user's own connection
 * (POST /api/v1/import/sources). Server / Data Center with a personal access
 * token is preselected -- the common on-premises case; Cloud stays one click
 * away. Create-only: credentials are write-only, so there is nothing to
 * prefill for an edit variant. Used by the connections tab, the new
 * knowledge space flow and the "add source" form.
 */
export function ConfluenceSetupDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (source: ImportSource) => void | Promise<void> }) {
  const { t } = useI18n();
  const [name, setName] = useState('Confluence');
  const [baseUrl, setBaseUrl] = useState('');
  const [authType, setAuthType] = useState<ImportAuthType>('pat_bearer');
  const [email, setEmail] = useState('');
  // Write-only secret: only ever holds what the user is typing right now.
  const [credential, setCredential] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Toggling the auth scheme clears the typed secret: the field is masked, so
  // a token entered for one scheme must not silently become the credential
  // of the other (mirrors imports/new/page.tsx's selectAuthType).
  const selectAuthType = (next: ImportAuthType) => {
    if (next !== authType) setCredential('');
    setAuthType(next);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedEmail = email.trim();
    if (authType === 'cloud_basic' && !trimmedEmail) {
      setError(t('portal.connections.confluence.cloudEmailRequired'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const source = await apiJson<ImportSource>('/api/v1/import/sources', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          base_url: baseUrl.trim(),
          auth_type: authType,
          auth_username: authType === 'cloud_basic' ? trimmedEmail : '',
          credential: credential.trim(),
        }),
      });
      await onCreated(source);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  const authOption = (value: ImportAuthType, title: string, hint: string) => (
    <button
      type="button"
      aria-pressed={authType === value}
      onClick={() => selectAuthType(value)}
      className={`rounded-xl border p-3 text-left ${authType === value ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'}`}
    >
      <p className="text-sm font-semibold text-slate-950">{title}</p>
      <p className="mt-1 text-xs text-slate-600">{hint}</p>
    </button>
  );

  return (
    <Modal title={t('portal.connections.confluence.modalTitle')} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label={t('portal.connections.confluence.nameLabel')}>
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputClass} required autoFocus placeholder="ACME Confluence" />
        </Field>
        <Field label={t('portal.connections.confluence.baseUrlLabel')}>
          <input
            type="url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            className={inputClass}
            required
            placeholder={authType === 'cloud_basic' ? 'https://acme.atlassian.net' : 'https://confluence.example.com'}
          />
        </Field>
        <div>
          <p className="text-sm font-medium text-slate-700">{t('portal.connections.confluence.authenticationLabel')}</p>
          <div className="mt-1 grid gap-3 sm:grid-cols-2">
            {authOption('pat_bearer', t('portal.connections.confluence.authServerTitle'), t('portal.connections.confluence.authServerHint'))}
            {authOption('cloud_basic', t('portal.connections.confluence.authCloudTitle'), t('portal.connections.confluence.authCloudHint'))}
          </div>
        </div>
        {authType === 'cloud_basic' && (
          <Field label={t('portal.connections.confluence.emailLabel')}>
            <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" className={inputClass} required placeholder="name@company.com" />
          </Field>
        )}
        <Field label={authType === 'cloud_basic' ? t('portal.connections.confluence.apiTokenLabel') : t('portal.connections.confluence.patLabel')} hint={t('portal.connections.confluence.credentialHint')}>
          <input
            value={credential}
            onChange={(e) => setCredential(e.target.value)}
            type="password"
            className={inputClass}
            required
            autoComplete="new-password"
            data-1p-ignore
            data-lpignore="true"
            placeholder={authType === 'cloud_basic' ? 'Atlassian-API-Token' : 'Confluence-PAT'}
          />
        </Field>
        <ErrorNotice message={error} />
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={busy}>
            {t('portal.connections.confluence.cancel')}
          </Button>
          <Button type="submit" size="sm" disabled={busy}>
            {busy && <LoaderCircle className="h-4 w-4 animate-spin" />}
            {t('portal.connections.confluence.add')}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
