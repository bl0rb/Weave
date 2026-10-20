'use client';

import { useState, type FormEvent } from 'react';
import { Upload } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ApiError, apiFetch } from '@/lib/api';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { Badge, ErrorNotice, Field, Modal, Toggle, errorMessage, inputClass } from './admin-shared';

/** `BotImportReport` (services/ingest/backend/app/schemas/managed_bots.py). */
export type BotImportReport = {
  schema_version: number | null;
  bot_id: string | null;
  name: string | null;
  kind: string | null;
  conflict: 'managed' | 'runtime' | 'tombstoned' | null;
  action: 'create' | 'overwrite' | 'blocked';
  warnings: { code: string; detail: string }[];
  errors: string[];
  applied: boolean;
};

const IMPORT_PATH = '/api/v1/auth/admin/bots/import';

async function sendImport(file: File, dryRun: boolean, overwrite: boolean, botId: string): Promise<BotImportReport> {
  const body = new FormData();
  body.append('file', file, file.name);
  body.append('dry_run', dryRun ? 'true' : 'false');
  body.append('on_conflict', overwrite ? 'overwrite' : 'fail');
  if (botId.trim()) body.append('bot_id', botId.trim());
  const response = await apiFetch(IMPORT_PATH, { method: 'POST', body });
  const data = (await response.json().catch(() => null)) as (BotImportReport & { detail?: unknown }) | null;
  // 409/422 carry the report too; only a non-report body is a real failure.
  if (data && Array.isArray(data.errors) && typeof data.action === 'string') return data;
  throw new ApiError(response.status, typeof data?.detail === 'string' ? data.detail : `HTTP ${response.status}`);
}

/**
 * Two-step import of a bot document (contracts/bot-document.md): the
 * first request is a dry run whose report (action, warnings, errors) is
 * shown; "Importieren" repeats it with `dry_run=false`.
 */
export function BotImportDialog({ onClose, onImported }: { onClose: () => void; onImported: () => Promise<void> }) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [botId, setBotId] = useState('');
  const [report, setReport] = useState<BotImportReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const actionTone = { create: 'emerald', overwrite: 'amber', blocked: 'red' } as const;

  async function run(dryRun: boolean) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const next = await sendImport(file, dryRun, overwrite, botId);
      setReport(next);
      if (next.applied) await onImported();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function onCheck(event: FormEvent) {
    event.preventDefault();
    void run(true);
  }

  function warningLabel(code: string): string {
    const key = `admin.bots.import.warning.${code}` as MessageKey;
    const label = t(key);
    return label === key ? code : label;
  }

  return <Modal title={t('admin.bots.import.title')} onClose={onClose} footer={<>
    <Button type="button" variant="outline" onClick={onClose} disabled={busy}>{t('common.cancel')}</Button>
    <Button type="submit" form="bot-import-form" variant={report ? 'outline' : 'default'} disabled={!file || busy}>{t('admin.bots.import.check')}</Button>
    {report && report.action !== 'blocked' && !report.applied && (
      <Button type="button" onClick={() => void run(false)} disabled={busy}>{busy ? t('admin.bots.import.importing') : t('admin.bots.import.confirm')}</Button>
    )}
  </>}>
    <ErrorNotice message={error} />
    <form id="bot-import-form" onSubmit={onCheck} className="space-y-4">
      <Field label={t('admin.bots.import.file')} hint={t('admin.bots.import.fileHint')}>
        <input className={inputClass} type="file" accept=".yaml,.yml,.json,application/yaml,application/json" onChange={event => { setFile(event.target.files?.[0] ?? null); setReport(null); }} />
      </Field>
      <Field label={t('admin.bots.import.botId')} hint={t('admin.bots.import.botIdHint')}>
        <input className={inputClass} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" value={botId} onChange={event => { setBotId(event.target.value.toLowerCase()); setReport(null); }} placeholder={t('admin.bots.field.id.placeholder')} />
      </Field>
      <Toggle checked={overwrite} onChange={value => { setOverwrite(value); setReport(null); }} label={t('admin.bots.import.overwrite')} />
    </form>
    {report && <section aria-label={t('admin.bots.import.reportTitle')} className="mt-5 space-y-3 rounded-xl border border-slate-200 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={actionTone[report.action]}>{t(`admin.bots.import.action.${report.action}`)}</Badge>
        {report.applied && <Badge tone="emerald">{t('admin.bots.import.applied')}</Badge>}
        <span className="text-sm font-medium text-slate-800">{report.name ?? '—'}</span>
        {report.bot_id && <span className="text-xs text-slate-500">{report.bot_id}</span>}
        {report.kind && <Badge tone="slate">{report.kind}</Badge>}
      </div>
      {report.conflict && <p className="text-xs text-slate-600">{t(`admin.bots.import.conflict.${report.conflict}`)}</p>}
      {report.errors.length > 0 && <ul role="alert" className="list-disc space-y-1 pl-5 text-sm text-red-700">{report.errors.map((item, index) => <li key={index}>{item}</li>)}</ul>}
      {report.warnings.length > 0 && <ul className="space-y-1 text-sm text-amber-800">{report.warnings.map((item, index) => <li key={index}><span className="font-semibold">{warningLabel(item.code)}:</span> {item.detail}</li>)}</ul>}
      {report.errors.length === 0 && report.warnings.length === 0 && <p className="text-sm text-slate-600">{t('admin.bots.import.clean')}</p>}
    </section>}
    {!report && <p className="mt-4 flex items-center gap-2 text-xs text-slate-500"><Upload size={14} />{t('admin.bots.import.hint')}</p>}
  </Modal>;
}
