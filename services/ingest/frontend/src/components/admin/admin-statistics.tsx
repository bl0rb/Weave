'use client';

import type { ReactNode } from 'react';

import { useAdminJson } from '@/components/admin/admin-page-shared';
import type { MessageKey } from '@/i18n/messages';
import { useI18n } from '@/i18n/provider';

// Shape of GET /api/v1/admin/statistics (AdminStatisticsResponse in
// services/ingest/backend/app/schemas/admin_statistics.py). `knowledge` is
// proxied from Weave-Knowledge and null when that service is unconfigured or
// unreachable; `knowledge_error` then says why (German, from the backend).
export type KnowledgeStatistics = {
  documents: { total: number; by_status: { pending: number; indexed: number; blocked: number; superseded: number; failed: number } };
  chunks: { total: number; embedded: number; avg_chars: number | null };
  embedding_models: { model: string; chunks: number }[];
  collections: { total: number; with_documents: number };
  memory: { documents: number; collections: number };
  vector_backend: string;
  storage: { documents_bytes: number; chunks_bytes: number; memory_bytes: number; total_bytes: number } | null;
  generated_at: string | null;
};

export type AdminStatistics = {
  documents: {
    total: number;
    versions: number;
    by_status: { PENDING: number; RUNNING: number; FINISHED: number; FAILED: number };
    released: number;
    uploaded_last_30_days: number;
  };
  storage: { objects: number; bytes: number; by_backend: Record<string, { objects: number; bytes: number }> };
  collections: { total: number; public: number; restricted: number };
  users: { total: number; active: number; admins: number };
  teams: number;
  review_tasks: { open: number; in_progress: number };
  subscriptions: number;
  knowledge: KnowledgeStatistics | null;
  knowledge_error: string | null;
  generated_at: string;
};

type FormatNumber = (value: number, options?: Intl.NumberFormatOptions) => string;

/** Human-readable byte size (binary steps, locale decimal separator). */
export function formatBytes(value: number, formatNumber: FormatNumber): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let unit = 0;
  // Compare the value as it will be displayed (one decimal), so 1023.96 KB
  // becomes "1 MB" instead of "1.024 KB" (which reads like ~1 KB in German).
  while (Math.round(size * 10) / 10 >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${formatNumber(size, { maximumFractionDigits: unit === 0 ? 0 : 1 })} ${units[unit]}`;
}

const BACKEND_LABELS: Record<string, MessageKey> = {
  db: 'admin.overview.stats.storage.backend.db',
  s3: 'admin.overview.stats.storage.backend.s3',
};

type Row = { key: string; label: string; value: ReactNode };

function StatCard({ title, rows, note, children }: { title: string; rows: Row[]; note?: string | null; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-xs font-medium text-slate-500">{title}</h3>
      <dl className="mt-2 space-y-1">
        {rows.map((row) => (
          <div key={row.key} className="flex items-baseline justify-between gap-3 text-sm">
            <dt className="text-slate-600">{row.label}</dt>
            <dd className="text-right font-semibold text-slate-950">{row.value}</dd>
          </div>
        ))}
      </dl>
      {children}
      {note && <p className="mt-2 text-xs text-amber-700">{note}</p>}
    </div>
  );
}

export function AdminStatisticsSection() {
  const { t, formatNumber, locale } = useI18n();
  const { data, loading } = useAdminJson<AdminStatistics>('/api/v1/admin/statistics');
  const placeholder = loading ? '…' : '–';
  const knowledge = data?.knowledge ?? null;
  // The backend reason is German only; other locales get the localized note.
  const knowledgeNote =
    data && !knowledge ? (locale === 'de' && data.knowledge_error) || t('admin.overview.stats.knowledgeUnavailable') : null;

  const num = (value: number | null | undefined) => (value == null ? placeholder : formatNumber(value));
  const bytes = (value: number | null | undefined) => (value == null ? placeholder : formatBytes(value, formatNumber));
  // Values that only Weave-Knowledge knows: "–" whenever it did not answer.
  const kNum = (value: number | null | undefined) => (knowledge && value != null ? formatNumber(value) : placeholder);

  const documents = data?.documents;
  const processing = documents ? documents.by_status.PENDING + documents.by_status.RUNNING : null;
  const backends = data ? Object.entries(data.storage.by_backend).sort(([a], [b]) => a.localeCompare(b)) : [];

  const cards: { key: string; title: string; rows: Row[]; note?: string | null; extra?: ReactNode }[] = [
    {
      key: 'documents',
      title: t('admin.overview.stats.documents.title'),
      rows: [
        { key: 'total', label: t('admin.overview.stats.documents.total'), value: num(documents?.total) },
        { key: 'released', label: t('admin.overview.stats.documents.released'), value: num(documents?.released) },
        // The rows below count versions (every upload), not chain heads.
        { key: 'versions', label: t('admin.overview.stats.documents.versions'), value: num(documents?.versions) },
        { key: 'processing', label: t('admin.overview.stats.documents.processing'), value: num(processing) },
        { key: 'failed', label: t('admin.overview.stats.documents.failed'), value: num(documents?.by_status.FAILED) },
        { key: 'recent', label: t('admin.overview.stats.documents.recent'), value: num(documents?.uploaded_last_30_days) },
      ],
    },
    {
      key: 'storage',
      title: t('admin.overview.stats.storage.title'),
      rows: [
        { key: 'objects', label: t('admin.overview.stats.storage.objects'), value: num(data?.storage.objects) },
        { key: 'size', label: t('admin.overview.stats.storage.size'), value: bytes(data?.storage.bytes) },
        ...backends.map(([backend, item]) => ({
          key: `backend-${backend}`,
          label: BACKEND_LABELS[backend] ? t(BACKEND_LABELS[backend]) : backend,
          value: t('admin.overview.stats.storage.backendValue', {
            count: item.objects,
            objects: formatNumber(item.objects),
            size: formatBytes(item.bytes, formatNumber),
          }),
        })),
      ],
    },
    {
      key: 'index',
      title: t('admin.overview.stats.index.title'),
      note: knowledgeNote,
      rows: [
        { key: 'chunks', label: t('admin.overview.stats.index.chunks'), value: kNum(knowledge?.chunks.total) },
        { key: 'embedded', label: t('admin.overview.stats.index.embedded'), value: kNum(knowledge?.chunks.embedded) },
        { key: 'indexed', label: t('admin.overview.stats.index.indexedDocuments'), value: kNum(knowledge?.documents.by_status.indexed) },
        {
          key: 'avg',
          label: t('admin.overview.stats.index.avgChars'),
          value: knowledge && knowledge.chunks.avg_chars != null ? formatNumber(Math.round(knowledge.chunks.avg_chars)) : placeholder,
        },
        { key: 'backend', label: t('admin.overview.stats.index.vectorBackend'), value: knowledge?.vector_backend || placeholder },
        ...(knowledge?.storage
          ? [{ key: 'db-size', label: t('admin.overview.stats.index.databaseSize'), value: formatBytes(knowledge.storage.total_bytes, formatNumber) }]
          : []),
      ],
      extra:
        knowledge && knowledge.embedding_models.length > 0 ? (
          <div className="mt-3 border-t border-slate-100 pt-2">
            <h4 className="text-xs font-medium text-slate-500">{t('admin.overview.stats.index.models')}</h4>
            <dl className="mt-1 space-y-1">
              {knowledge.embedding_models.map((item) => (
                <div key={item.model} className="flex items-baseline justify-between gap-3 text-xs">
                  <dt className="min-w-0 truncate text-slate-600" title={item.model}>
                    {item.model}
                  </dt>
                  <dd className="flex-shrink-0 text-slate-950">{t('admin.overview.stats.index.modelChunks', { count: item.chunks, chunks: formatNumber(item.chunks) })}</dd>
                </div>
              ))}
            </dl>
          </div>
        ) : null,
    },
    {
      key: 'collections',
      title: t('admin.overview.stats.collections.title'),
      note: knowledgeNote,
      rows: [
        { key: 'total', label: t('admin.overview.stats.collections.total'), value: num(data?.collections.total) },
        { key: 'public', label: t('admin.overview.stats.collections.public'), value: num(data?.collections.public) },
        { key: 'restricted', label: t('admin.overview.stats.collections.restricted'), value: num(data?.collections.restricted) },
        { key: 'with-documents', label: t('admin.overview.stats.collections.withDocuments'), value: kNum(knowledge?.collections.with_documents) },
      ],
    },
    {
      key: 'memory',
      title: t('admin.overview.stats.memory.title'),
      note: knowledgeNote,
      rows: [
        { key: 'documents', label: t('admin.overview.stats.memory.documents'), value: kNum(knowledge?.memory.documents) },
        { key: 'collections', label: t('admin.overview.stats.memory.collections'), value: kNum(knowledge?.memory.collections) },
      ],
    },
    {
      key: 'users',
      title: t('admin.overview.stats.users.title'),
      rows: [
        { key: 'total', label: t('admin.overview.stats.users.total'), value: num(data?.users.total) },
        { key: 'active', label: t('admin.overview.stats.users.active'), value: num(data?.users.active) },
        { key: 'admins', label: t('admin.overview.stats.users.admins'), value: num(data?.users.admins) },
        { key: 'teams', label: t('admin.overview.stats.users.teams'), value: num(data?.teams) },
      ],
    },
    {
      key: 'review',
      title: t('admin.overview.stats.review.title'),
      rows: [
        { key: 'open', label: t('admin.overview.stats.review.open'), value: num(data?.review_tasks.open) },
        { key: 'in-progress', label: t('admin.overview.stats.review.inProgress'), value: num(data?.review_tasks.in_progress) },
        { key: 'subscriptions', label: t('admin.overview.stats.review.subscriptions'), value: num(data?.subscriptions) },
      ],
    },
  ];

  return (
    <section aria-labelledby="statistics-title" className="mb-6">
      <h2 id="statistics-title" className="mb-3 text-[17px] font-semibold text-slate-950">
        {t('admin.overview.stats.heading')}
      </h2>
      {!data && !loading && <p className="mb-3 text-sm text-slate-500">{t('admin.overview.stats.loadError')}</p>}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map((card) => (
          <StatCard key={card.key} title={card.title} rows={card.rows} note={card.note}>
            {card.extra}
          </StatCard>
        ))}
      </div>
    </section>
  );
}
