'use client';

import { useState } from 'react';
import { Sparkles } from 'lucide-react';
import { ApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { requestDocumentAnalysis, portalError, type DocumentAnalysis } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';
import type { MessageKey } from '@/i18n/messages';
import { Notice } from './shared';

const severityTone = { info: 'portal-badge-neutral', warning: 'portal-badge-warning', critical: 'portal-badge-error' } as const;

/** On-demand AI recommendation (costs a model call): never requested automatically. */
export function DocumentAnalysisPanel({ id, configured }: { id: string; configured: boolean }) {
  const { t, locale } = useI18n();
  const [result, setResult] = useState<DocumentAnalysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  async function run(refresh: boolean) {
    if (loading || !configured) return;
    setLoading(true); setError('');
    try { setResult(await requestDocumentAnalysis(id, locale === 'en' ? 'en' : 'de', refresh)); }
    catch (err) {
      const detail = err instanceof ApiError ? err.detail : '';
      setError(err instanceof ApiError && err.status === 409 && detail.includes('analysis_not_configured') ? t('portal.reviews.analysis.errorNotConfigured')
        : err instanceof ApiError && err.status === 502 ? t('portal.reviews.analysis.errorFailed') : portalError(err, locale));
    } finally { setLoading(false); }
  }
  const when = result && new Intl.DateTimeFormat(locale === 'en' ? 'en-GB' : 'de-DE', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(result.generated_at));
  return <section className="grid gap-2" aria-labelledby="analysis-title">
    <h3 id="analysis-title" className="text-[14px] font-semibold">{t('portal.reviews.analysis.heading')}</h3>
    <Button className="w-full" variant="outline" disabled={!configured || loading} onClick={() => void run(Boolean(result))}><Sparkles size={16} />{loading ? t('portal.reviews.analysis.loading') : result ? t('portal.reviews.analysis.refresh') : t('portal.reviews.analysis.request')}</Button>
    {!configured ? <p className="portal-field-hint">{t('portal.reviews.analysis.notConfigured')}</p> : !result && <p className="portal-field-hint">{t('portal.reviews.analysis.hint')}</p>}
    <div aria-live="polite" className="grid gap-2">
      {error && <Notice error>{error}</Notice>}
      {result && <>
        <p>{result.summary}</p>
        <ul className="grid gap-2">{result.recommendations.map((item, index) => <li key={index} className="rounded border border-[var(--line)] p-2 text-[13px]">
          <span className={`portal-badge ${severityTone[item.severity] ?? ''}`}>{t(`portal.reviews.analysis.severity.${item.severity}` as MessageKey)}</span>{' '}
          <strong>{t(`portal.reviews.analysis.action.${item.action}` as MessageKey)}: {item.title}</strong>
          <span className="block">{item.detail}</span>
        </li>)}</ul>
        <span className="portal-field-hint">{result.cached ? t('portal.reviews.analysis.cached', { date: when! }) : t('portal.reviews.analysis.generated', { date: when! })}</span>
      </>}
    </div>
  </section>;
}
