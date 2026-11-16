'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { LoaderCircle, Trash2, Upload } from 'lucide-react';

import { useBranding } from '@/components/branding-provider';
import { Button } from '@/components/ui/button';
import { BrandingAssetImage, WeaveIngestMark } from '@/components/weave-ingest-logo';
import { useI18n } from '@/i18n/provider';
import { apiJson } from '@/lib/api';
import { resolveApiBaseUrl } from '@/lib/api-base';
import {
  BRANDING_COLOR_KEYS,
  DEFAULT_PRIMARY_COLOR,
  normalizeHexColor,
  parseBranding,
  type BrandingAssetKind,
  type BrandingColors,
} from '@/lib/branding';
import { ErrorNotice, LoadingState, SectionCard, errorMessage, inputClass } from './admin-shared';

type BrandingAssetInfo = { content_type: string; sha256: string; size_bytes: number; url: string };

/** `BrandingAdmin` (services/ingest/backend/app/schemas/branding.py). */
export type BrandingAdmin = {
  primary_color: string | null;
  accent_color: string | null;
  palette: unknown;
  logo: BrandingAssetInfo | null;
  busy: BrandingAssetInfo | null;
  updated_at: string | null;
  managed: boolean;
};

const PATH = '/api/v1/auth/admin/branding';
const MAX_ASSET_BYTES = 1024 * 1024;
const ACCEPT = '.svg,.gif,.png,image/svg+xml,image/gif,image/png';
const TYPE_LABEL: Record<string, string> = { 'image/svg+xml': 'SVG', 'image/gif': 'GIF', 'image/png': 'PNG' };

/**
 * Administration › Betrieb › Erscheinungsbild: primary and accent colour
 * (hex), the logo and the busy animation for portal and chat. Every
 * successful change is handed to the BrandingProvider, so the portal
 * restyles at once.
 */
export function BrandingTab() {
  const { t } = useI18n();
  const { refresh } = useBranding();
  const [config, setConfig] = useState<BrandingAdmin | null>(null);
  const [primary, setPrimary] = useState('');
  const [accent, setAccent] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiJson<BrandingAdmin>(PATH)
      .then(value => {
        if (cancelled) return;
        setConfig(value);
        setPrimary(value.primary_color ?? '');
        setAccent(value.accent_color ?? '');
      })
      .catch(err => { if (!cancelled) setError(errorMessage(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  function adopt(next: BrandingAdmin) {
    setConfig(next);
    void refresh(next);
  }

  const invalid = t('admin.branding.colors.invalid');
  const primaryError = primary.trim() && !normalizeHexColor(primary) ? invalid : null;
  const accentError = accent.trim() && !normalizeHexColor(accent) ? invalid : null;

  async function save() {
    if (primaryError || accentError) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const next = await apiJson<BrandingAdmin>(PATH, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ primary_color: normalizeHexColor(primary), accent_color: normalizeHexColor(accent) }),
      });
      setPrimary(next.primary_color ?? '');
      setAccent(next.accent_color ?? '');
      setMessage(t('admin.branding.saved'));
      adopt(next);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  if (loading || !config) {
    return (
      <SectionCard title={t('admin.branding.colors.title')} description={t('admin.branding.colors.description')}>
        <ErrorNotice message={error} />
        {loading && <LoadingState label={t('admin.branding.loading')} />}
      </SectionCard>
    );
  }

  const branding = parseBranding(config, resolveApiBaseUrl());
  const primaryFallback = DEFAULT_PRIMARY_COLOR;
  const accentFallback = normalizeHexColor(primary) ?? primaryFallback;
  return (
    <div className="space-y-6">
      <SectionCard title={t('admin.branding.colors.title')} description={t('admin.branding.colors.description')}>
        <ErrorNotice message={error} />
        <form
          className="space-y-6"
          noValidate
          onSubmit={event => { event.preventDefault(); void save(); }}
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <ColorField label={t('admin.branding.colors.primary')} hint={t('admin.branding.colors.primaryHint')} value={primary} fallback={primaryFallback} error={primaryError} onChange={setPrimary} />
            <ColorField label={t('admin.branding.colors.accent')} hint={t('admin.branding.colors.accentHint')} value={accent} fallback={accentFallback} error={accentError} onChange={setAccent} />
          </div>
          <div className="flex flex-wrap items-center gap-3 border-t border-[var(--line)] pt-4">
            <Button type="submit" disabled={saving || Boolean(primaryError || accentError)}>
              {saving && <LoaderCircle className="h-4 w-4 animate-spin" />}
              {saving ? t('admin.branding.saving') : t('common.save')}
            </Button>
            <span aria-live="polite" className="text-sm text-[var(--muted)]">{message}</span>
          </div>
        </form>
        <PalettePreview palette={branding?.palette ?? null} />
      </SectionCard>

      <SectionCard title={t('admin.branding.assets.title')} description={t('admin.branding.assets.description')}>
        <div className="grid gap-6 lg:grid-cols-2">
          <AssetEditor kind="logo" label={t('admin.branding.assets.logo')} hint={t('admin.branding.assets.logoHint')} asset={config.logo} url={branding?.logo?.url ?? null} onChanged={adopt} />
          <AssetEditor kind="busy" label={t('admin.branding.assets.busy')} hint={t('admin.branding.assets.busyHint')} asset={config.busy} url={branding?.busy?.url ?? null} onChanged={adopt} />
        </div>
      </SectionCard>
    </div>
  );
}

/** Hex text field plus a synced colour swatch; '' means "Standard" (null). */
function ColorField({
  label,
  hint,
  value,
  fallback,
  error,
  onChange,
}: {
  label: string;
  hint: string;
  value: string;
  /** The effective colour while the field is empty or invalid (shown by the swatch). */
  fallback: string;
  error: string | null;
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  const id = useId();
  const messageId = `${id}-message`;
  return (
    <div className="text-[13px] font-semibold text-[var(--ink-2)]">
      <label htmlFor={id}>{label}</label>
      <div className="mt-1.5 flex items-center gap-2">
        <input
          type="color"
          aria-label={t('admin.branding.colors.picker', { label })}
          value={normalizeHexColor(value) ?? fallback}
          onChange={event => onChange(event.target.value.toLowerCase())}
          className="h-10 w-12 flex-shrink-0 cursor-pointer rounded-lg border border-[var(--line-2)] bg-[var(--surface)] p-1"
        />
        <input
          id={id}
          className="ui-control min-w-0 flex-1 font-mono"
          value={value}
          placeholder={t('admin.branding.colors.placeholder')}
          onChange={event => onChange(event.target.value)}
          spellCheck={false}
          autoComplete="off"
          maxLength={7}
          aria-invalid={error ? true : undefined}
          aria-describedby={messageId}
        />
        <Button type="button" variant="outline" onClick={() => onChange('')} disabled={value === ''} aria-label={t('admin.branding.colors.resetLabel', { label })}>
          {t('admin.branding.colors.reset')}
        </Button>
      </div>
      <span id={messageId} className={`mt-1.5 block text-xs font-normal ${error ? 'text-red-600' : 'text-[var(--muted)]'}`}>
        {error ?? hint}
      </span>
    </div>
  );
}

/** The saved palette (validated hex only) for light and dark, with a sample button, link and badge. */
function PalettePreview({ palette }: { palette: { light: BrandingColors; dark: BrandingColors } | null }) {
  const { t } = useI18n();
  return (
    <section className="mt-6 border-t border-[var(--line)] pt-4" aria-label={t('admin.branding.palette.title')}>
      <h3 className="text-sm font-semibold text-[var(--ink)]">{t('admin.branding.palette.title')}</h3>
      {!palette ? (
        <p className="mt-2 text-sm text-[var(--muted)]">{t('admin.branding.palette.default')}</p>
      ) : (
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          {(['light', 'dark'] as const).map(mode => {
            const colors = palette[mode];
            const surface = mode === 'light' ? { background: '#ffffff', color: '#14221d' } : { background: '#1f2024', color: '#eceef1' };
            return (
              <div key={mode} className="rounded-xl border border-[var(--line-2)] p-4" style={surface} data-testid={`branding-palette-${mode}`}>
                <p className="text-xs font-semibold uppercase tracking-wide">{t(`admin.branding.palette.${mode}`)}</p>
                <ul className="mt-3 grid grid-cols-5 gap-2">
                  {BRANDING_COLOR_KEYS.map(key => (
                    <li key={key} className="min-w-0 text-[11px] leading-tight">
                      <span className="block h-8 rounded-md border border-black/10" style={{ background: colors[key] }} />
                      <span className="mt-1 block">{t(`admin.branding.palette.${key}`)}</span>
                      <span className="block font-mono opacity-70">{colors[key]}</span>
                    </li>
                  ))}
                </ul>
                <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
                  <span className="inline-flex h-9 items-center rounded-lg px-3 font-semibold" style={{ background: colors.accent, color: colors.on_accent }}>
                    {t('admin.branding.palette.sampleButton')}
                  </span>
                  <span className="font-semibold underline underline-offset-4" style={{ color: colors.accent }}>
                    {t('admin.branding.palette.sampleLink')}
                  </span>
                  <span className="inline-flex h-6 items-center rounded-full px-2.5 text-xs font-semibold" style={{ background: colors.accent_soft, color: colors.accent_ink }}>
                    {t('admin.branding.palette.sampleChip')}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

/** Preview, upload (multipart, field `file`) and removal of one asset. */
function AssetEditor({
  kind,
  label,
  hint,
  asset,
  url,
  onChanged,
}: {
  kind: BrandingAssetKind;
  label: string;
  hint: string;
  asset: BrandingAssetInfo | null;
  /** Validated asset URL on the API origin, null when unset or invalid. */
  url: string | null;
  onChanged: (next: BrandingAdmin) => void;
}) {
  const { t } = useI18n();
  const inputId = useId();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [pending, setPending] = useState<'upload' | 'remove' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const path = `${PATH}/assets/${kind}`;

  async function run(action: 'upload' | 'remove', request: () => Promise<BrandingAdmin>, done: string) {
    setPending(action);
    setError(null);
    setMessage(null);
    try {
      const next = await request();
      setMessage(done);
      onChanged(next);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(null);
    }
  }

  function upload() {
    if (!file) return;
    if (file.size > MAX_ASSET_BYTES) {
      setMessage(null);
      setError(t('admin.branding.assets.tooLarge'));
      return;
    }
    void run('upload', async () => {
      const body = new FormData();
      body.append('file', file, file.name);
      const next = await apiJson<BrandingAdmin>(path, { method: 'PUT', body });
      setFile(null);
      if (fileInput.current) fileInput.current.value = '';
      return next;
    }, t('admin.branding.assets.uploaded'));
  }

  function remove() {
    void run('remove', () => apiJson<BrandingAdmin>(path, { method: 'DELETE' }), t('admin.branding.assets.removed'));
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold text-[var(--ink)]">{label}</h3>
        <p className="mt-0.5 text-xs text-[var(--muted)]">{hint}</p>
      </div>
      <div className="flex items-center gap-4 rounded-xl border border-[var(--line)] bg-[var(--surface-2)] p-4">
        <div className="flex h-16 w-16 flex-shrink-0 items-center justify-center">
          {url ? (
            <BrandingAssetImage src={url} className="h-16 w-16" />
          ) : (
            <WeaveIngestMark className="h-14 w-14" animation={kind === 'busy' ? 'loop' : undefined} />
          )}
        </div>
        <p className="text-sm text-[var(--ink-2)]">
          {url && asset
            ? t('admin.branding.assets.custom', { type: TYPE_LABEL[asset.content_type] ?? asset.content_type, size: Math.max(1, Math.round(asset.size_bytes / 1024)) })
            : t('admin.branding.assets.builtIn')}
        </p>
      </div>
      <ErrorNotice message={error} />
      <div className="text-[13px] font-semibold text-[var(--ink-2)]">
        <label htmlFor={inputId}>{t('admin.branding.assets.file', { label })}</label>
        <input
          id={inputId}
          ref={fileInput}
          className={inputClass}
          type="file"
          accept={ACCEPT}
          onChange={event => { setFile(event.target.files?.[0] ?? null); setError(null); setMessage(null); }}
        />
        <span className="mt-1.5 block text-xs font-normal text-[var(--muted)]">{t('admin.branding.assets.fileHint')}</span>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" onClick={upload} disabled={!file || pending !== null} aria-label={t('admin.branding.assets.uploadLabel', { label })}>
          {pending === 'upload' ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          {pending === 'upload' ? t('admin.branding.assets.uploading') : t('admin.branding.assets.upload')}
        </Button>
        {asset && (
          <Button type="button" variant="outline" onClick={remove} disabled={pending !== null} aria-label={t('admin.branding.assets.removeLabel', { label })}>
            {pending === 'remove' ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
            {t('admin.branding.assets.remove')}
          </Button>
        )}
        <span aria-live="polite" className="text-sm text-[var(--muted)]">{message}</span>
      </div>
    </div>
  );
}
