'use client';

import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { CheckCircle2, ClipboardCopy, FileDown, PlayCircle, XCircle } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ApiError, apiFetch, apiJson } from '@/lib/api';
import type { MessageKey } from '@/i18n/messages';
import { useI18n } from '@/i18n/provider';
import { Badge, ErrorNotice, Field, LoadingState, SectionCard, errorMessage, inputClass } from './admin-shared';

const BASE_PATH = '/api/v1/admin/config';

type EnvSetting = {
  name: string;
  group: string;
  value: unknown;
  default: unknown;
  secret: boolean;
  is_set: boolean;
  customized: boolean;
};

type ManualSection = {
  section: string;
  items: { id: string; values: Record<string, unknown> }[];
};

type ConfigOverview = {
  generated_at: string;
  env: EnvSetting[];
  manual: ManualSection[];
  checks: string[];
};

type CheckStep = { id: string; ok: boolean; detail: string; ms: number };
type CheckResult = { name: string; ok: boolean; steps: CheckStep[] };

const S3_FIELDS = [
  'S3_BUCKET',
  'S3_ENDPOINT_URL',
  'S3_REGION',
  'S3_ACCESS_KEY_ID',
  'S3_SECRET_ACCESS_KEY',
  'S3_KEY_PREFIX',
  'S3_ADDRESSING_STYLE',
  'S3_SSE',
  'S3_KMS_KEY_ID',
] as const;
type S3Field = (typeof S3_FIELDS)[number];
type S3Form = Record<S3Field, string>;

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '–';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

async function downloadExport(format: 'env' | 'json') {
  const response = await apiFetch(`${BASE_PATH}/export?format=${format}`);
  if (!response.ok) throw new ApiError(response.status, `Export failed (${response.status})`);
  const blob = await response.blob();
  const fileName =
    /filename="([^"]+)"/.exec(response.headers.get('content-disposition') ?? '')?.[1] ?? `weave-ingest.${format}`;
  const href = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = href;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(href);
}

export function ConfigTab() {
  const { t } = useI18n();
  const [overview, setOverview] = useState<ConfigOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setOverview(await apiJson<ConfigOverview>(BASE_PATH));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload]);

  if (!overview) {
    return error ? <ErrorNotice message={error} /> : <LoadingState label={t('admin.config.loading')} />;
  }

  return (
    <div className="space-y-4">
      <ChecksCard checks={overview.checks} />
      <S3TestCard env={overview.env} />
      <EnvSettingsCard env={overview.env} />
      <ManualSettingsCard sections={overview.manual} />
    </div>
  );
}

function StepList({ result }: { result: CheckResult }) {
  const { t } = useI18n();
  return (
    <ul className="mt-2 space-y-1 text-xs">
      {result.steps.map((step) => (
        <li key={step.id} className="flex items-start gap-2">
          {step.ok ? (
            <CheckCircle2 size={14} className="mt-0.5 flex-shrink-0 text-emerald-600" />
          ) : (
            <XCircle size={14} className="mt-0.5 flex-shrink-0 text-red-600" />
          )}
          <span className="font-medium text-slate-700">{t(`admin.config.step.${step.id}` as MessageKey)}</span>
          <span className={`min-w-0 break-all ${step.ok ? 'text-slate-500' : 'text-red-700'}`}>{step.detail}</span>
          <span className="ml-auto flex-shrink-0 text-slate-400">{step.ms} ms</span>
        </li>
      ))}
    </ul>
  );
}

function ResultBadge({ result }: { result: CheckResult | undefined }) {
  const { t } = useI18n();
  if (!result) return <Badge tone="slate">{t('admin.config.notTested')}</Badge>;
  return result.ok ? (
    <Badge tone="emerald">{t('admin.config.ok')}</Badge>
  ) : (
    <Badge tone="red">{t('admin.config.failed')}</Badge>
  );
}

function ChecksCard({ checks }: { checks: string[] }) {
  const { t } = useI18n();
  const [results, setResults] = useState<Record<string, CheckResult>>({});
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  async function run(name: string) {
    setRunning((current) => ({ ...current, [name]: true }));
    try {
      const result = await apiJson<CheckResult>(`${BASE_PATH}/checks/${name}`, { method: 'POST' });
      setResults((current) => ({ ...current, [name]: result }));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRunning((current) => ({ ...current, [name]: false }));
    }
  }

  return (
    <SectionCard
      title={t('admin.config.checksTitle')}
      description={t('admin.config.checksDescription')}
      actions={
        <Button size="sm" onClick={() => checks.forEach((name) => void run(name))}>
          <PlayCircle size={15} />
          {t('admin.config.runAll')}
        </Button>
      }
    >
      <ErrorNotice message={error} />
      <ul className="divide-y divide-slate-100">
        {checks.map((name) => (
          <li key={name} className="py-3">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-medium text-slate-950">{t(`admin.config.check.${name}` as MessageKey)}</span>
              <ResultBadge result={results[name]} />
              <Button className="ml-auto" variant="ghost" size="sm" disabled={running[name]} onClick={() => void run(name)}>
                <PlayCircle size={15} />
                {running[name] ? t('admin.config.testing') : t('admin.config.test')}
              </Button>
            </div>
            {results[name] && <StepList result={results[name]} />}
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function envSnippet(form: S3Form): string {
  const lines = ['OBJECT_STORAGE_BACKEND=s3'];
  for (const field of S3_FIELDS) {
    const value = form[field];
    if (field === 'S3_SECRET_ACCESS_KEY') {
      lines.push(value ? `${field}=${value}` : `# ${field}=`);
    } else if (value) {
      lines.push(`${field}=${value}`);
    }
  }
  return lines.join('\n');
}

function S3TestCard({ env }: { env: EnvSetting[] }) {
  const { t } = useI18n();
  const initial = useMemo(() => {
    const byName = new Map(env.map((item) => [item.name, item]));
    return Object.fromEntries(
      S3_FIELDS.map((field) => {
        const item = byName.get(field);
        return [field, item && !item.secret && typeof item.value === 'string' ? item.value : ''];
      }),
    ) as S3Form;
  }, [env]);
  const [form, setForm] = useState<S3Form>(initial);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const set = (field: S3Field) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [field]: event.target.value }));

  async function test(event: FormEvent) {
    event.preventDefault();
    setTesting(true);
    setError(null);
    try {
      const body = Object.fromEntries(S3_FIELDS.map((field) => [field.toLowerCase(), form[field]]));
      setResult(
        await apiJson<CheckResult>(`${BASE_PATH}/checks/s3`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }),
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setTesting(false);
    }
  }

  const snippet = envSnippet(form);

  async function copy() {
    await navigator.clipboard.writeText(snippet);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  const text = (field: S3Field, label: string, hint?: string, type = 'text') => (
    <Field label={label} hint={hint}>
      <input
        className={inputClass}
        type={type}
        value={form[field]}
        onChange={set(field)}
        autoComplete={type === 'password' ? 'new-password' : 'off'}
        placeholder={type === 'password' ? t('admin.config.s3.secretPlaceholder') : undefined}
      />
    </Field>
  );

  return (
    <SectionCard title={t('admin.config.s3.title')} description={t('admin.config.s3.description')}>
      <form className="space-y-4" onSubmit={test}>
        <ErrorNotice message={error} />
        <div className="grid gap-4 sm:grid-cols-2">
          {text('S3_BUCKET', 'S3_BUCKET')}
          {text('S3_ENDPOINT_URL', 'S3_ENDPOINT_URL', t('admin.config.s3.endpointHint'))}
          {text('S3_REGION', 'S3_REGION')}
          {text('S3_KEY_PREFIX', 'S3_KEY_PREFIX')}
          {text('S3_ACCESS_KEY_ID', 'S3_ACCESS_KEY_ID', t('admin.config.s3.accessKeyHint'))}
          {text('S3_SECRET_ACCESS_KEY', 'S3_SECRET_ACCESS_KEY', t('admin.config.s3.secretHint'), 'password')}
          <Field label="S3_ADDRESSING_STYLE">
            <select className={inputClass} value={form.S3_ADDRESSING_STYLE} onChange={set('S3_ADDRESSING_STYLE')}>
              <option value="auto">auto</option>
              <option value="path">path</option>
              <option value="virtual">virtual</option>
            </select>
          </Field>
          <Field label="S3_SSE">
            <select className={inputClass} value={form.S3_SSE} onChange={set('S3_SSE')}>
              <option value="">{t('admin.config.s3.sseNone')}</option>
              <option value="AES256">AES256</option>
              <option value="aws:kms">aws:kms</option>
            </select>
          </Field>
          {form.S3_SSE === 'aws:kms' && text('S3_KMS_KEY_ID', 'S3_KMS_KEY_ID')}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={testing}>
            <PlayCircle size={15} />
            {testing ? t('admin.config.testing') : t('admin.config.s3.test')}
          </Button>
          {result && <ResultBadge result={result} />}
        </div>
        {result && <StepList result={result} />}
      </form>

      <div className="mt-6">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-slate-800">{t('admin.config.s3.snippetTitle')}</h3>
          <Button variant="ghost" size="sm" onClick={() => void copy()}>
            <ClipboardCopy size={15} />
            {copied ? t('admin.config.copied') : t('admin.config.copy')}
          </Button>
        </div>
        <p className="mb-2 text-xs text-[var(--muted)]">{t('admin.config.s3.snippetHint')}</p>
        <pre className="overflow-x-auto rounded-lg bg-slate-950 px-4 py-3 text-xs text-slate-100">{snippet}</pre>
      </div>
    </SectionCard>
  );
}

function EnvSettingsCard({ env }: { env: EnvSetting[] }) {
  const { t } = useI18n();
  const [filter, setFilter] = useState('');
  const [customizedOnly, setCustomizedOnly] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const groups = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const visible = env.filter(
      (item) =>
        (!customizedOnly || item.customized) &&
        (!needle || item.name.toLowerCase().includes(needle) || formatValue(item.value).toLowerCase().includes(needle)),
    );
    const byGroup = new Map<string, EnvSetting[]>();
    for (const item of visible) byGroup.set(item.group, [...(byGroup.get(item.group) ?? []), item]);
    return [...byGroup.entries()];
  }, [env, filter, customizedOnly]);

  async function exportAs(format: 'env' | 'json') {
    try {
      await downloadExport(format);
      setExportError(null);
    } catch (err) {
      setExportError(errorMessage(err));
    }
  }

  return (
    <SectionCard
      title={t('admin.config.envTitle')}
      description={t('admin.config.envDescription')}
      actions={
        <>
          <Button variant="ghost" size="sm" onClick={() => void exportAs('env')}>
            <FileDown size={15} />
            {t('admin.config.exportEnv')}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void exportAs('json')}>
            <FileDown size={15} />
            {t('admin.config.exportJson')}
          </Button>
        </>
      }
    >
      <ErrorNotice message={exportError} />
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <input
          className={`${inputClass} mt-0 max-w-xs`}
          type="search"
          placeholder={t('admin.config.filterPlaceholder')}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={customizedOnly} onChange={(event) => setCustomizedOnly(event.target.checked)} />
          {t('admin.config.customizedOnly')}
        </label>
      </div>
      {groups.map(([group, items]) => (
        <div key={group} className="mb-5">
          <h3 className="mb-1 text-sm font-semibold text-slate-800">{t(`admin.config.group.${group}` as MessageKey)}</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <tbody className="divide-y divide-slate-100">
                {items.map((item) => (
                  <tr key={item.name}>
                    <td className="w-1/3 py-1.5 pr-3 font-mono text-slate-700">{item.name}</td>
                    <td className="py-1.5 pr-3 font-mono break-all text-slate-950">{formatValue(item.value)}</td>
                    <td className="py-1.5 text-right whitespace-nowrap">
                      {item.secret && <Badge tone="amber">{t('admin.config.secret')}</Badge>}{' '}
                      {item.customized ? (
                        <Badge tone="emerald">{t('admin.config.customized')}</Badge>
                      ) : (
                        <Badge tone="slate">{t('admin.config.default')}</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </SectionCard>
  );
}

function ManualSettingsCard({ sections }: { sections: ManualSection[] }) {
  const { t } = useI18n();
  return (
    <SectionCard title={t('admin.config.manualTitle')} description={t('admin.config.manualDescription')}>
      {sections.map((section) => (
        <div key={section.section} className="mb-5">
          <h3 className="mb-1 text-sm font-semibold text-slate-800">{t(`admin.config.manual.${section.section}` as MessageKey)}</h3>
          {section.items.length === 0 ? (
            <p className="text-xs text-slate-500">{t('admin.config.manualEmpty')}</p>
          ) : (
            section.items.map((item) => (
              <details key={item.id} className="rounded-lg border border-slate-100 px-3 py-2">
                <summary className="cursor-pointer font-mono text-xs font-medium text-slate-800">{item.id}</summary>
                <dl className="mt-2 grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-x-3 gap-y-1 text-xs">
                  {Object.entries(item.values).map(([key, value]) => (
                    <div key={key} className="contents">
                      <dt className="font-mono text-slate-600">{key}</dt>
                      <dd className="font-mono break-all text-slate-950">{formatValue(value)}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            ))
          )}
        </div>
      ))}
    </SectionCard>
  );
}
