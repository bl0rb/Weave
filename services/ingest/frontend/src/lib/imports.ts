/**
 * Types + tiny helpers for the Confluence import surface.
 * Field names mirror backend/app/schemas/import_.py exactly.
 */

import { DEFAULT_LOCALE, type Locale } from '@/i18n/config';
import { translate } from '@/i18n/messages';

/** Confluence: cloud_basic | pat_bearer. Website / intranet: none | http_basic | bearer
 * (form logins, SSO/OAuth and Kerberos/NTLM are not supported by the backend). */
export type ImportAuthType = 'cloud_basic' | 'pat_bearer' | 'none' | 'http_basic' | 'bearer';

export type ImportSourceKind = 'confluence' | 'website';

/** Website crawl scope: the start page only, its section (sub-path) or the whole host. */
export type CrawlScope = 'page' | 'prefix' | 'host';

export type ImportRunStatus = 'pending' | 'running' | 'finished' | 'failed' | 'cancelled';

export type ImportJobStatus = 'PENDING' | 'RUNNING' | 'FINISHED' | 'FAILED';

export type ImportSource = {
  id: string;
  name: string;
  // Missing on older backends: treat as 'confluence'.
  kind?: ImportSourceKind;
  base_url: string;
  server_kind: string;
  auth_type: ImportAuthType;
  auth_username: string;
  has_credential: boolean;
  // Website connections an admin released for an internal (private-network) host.
  allow_private_target?: boolean;
  last_validated_at: string | null;
  created_at: string;
  // Number of configured imports (syncs) on this connection.
  sync_count?: number;
};

/** One configured Confluence import: scope + target space + options + schedule (GET /api/v1/import/syncs). */
export type ImportSync = {
  id: string;
  source_id: string;
  source_name: string;
  source_kind?: ImportSourceKind;
  scope_type: string;
  scope_value: string;
  root_page_title: string;
  collection_id: string | null;
  collection_name: string | null;
  options: ImportRunOptions;
  enabled: boolean;
  interval_seconds: number | null;
  last_success_at: string | null;
  last_error: string | null;
  next_refresh_at: string | null;
  last_run: ImportSyncLastRun | null;
  created_at: string;
};

export type ImportSyncLastRun = {
  id: string;
  status: ImportRunStatus;
  is_refresh: boolean;
  created_at: string;
  finished_at: string | null;
  pages_imported: number;
  pages_failed: number;
};

/** Options stored on runs and syncs (backend ImportRunOptions). */
export type ImportRunOptions = {
  collection_id?: string | null;
  max_pages: number | null;
  max_depth: number | null;
  include_attachments: boolean;
  ocr_attachments: boolean;
  ocr_profile_id: string | null;
  folder?: string;
  subfolder?: string;
  tags: string[];
  email: string;
  path_tags: boolean;
  webhook_connection_id: string | null;
  // Website sources only.
  crawl_scope?: CrawlScope;
  legal_source?: boolean;
  catalog_entry_id?: string | null;
};

export type ImportSyncListResponse = { items: ImportSync[] };

/** Auto-sync part of POST /api/v1/import/runs and PATCH /api/v1/import/syncs/{id}. */
export type ImportSyncSchedule = { enabled: boolean; interval_seconds?: number | null };

/** PATCH /api/v1/import/sources/{id} body -- all fields optional, only refresh_* used by the Sources UI so far. */
export type ImportSourceUpdateRequest = {
  name?: string;
  base_url?: string;
  auth_type?: ImportAuthType;
  auth_username?: string;
  credential?: string;
  allow_private_target?: boolean;
};

export function sourceKind(source: Pick<ImportSource, 'kind'>): ImportSourceKind {
  return source.kind === 'website' ? 'website' : 'confluence';
}

/** Host of an http(s) URL, lower-cased; '' when unparsable. */
export function hostOf(url: string): string {
  try { return new URL(url).hostname.toLowerCase(); } catch { return ''; }
}

/** Origin (scheme://host[:port]) of a URL, lower-cased; '' when unparsable. */
export function originOf(url: string): string {
  try { return new URL(url).origin.toLowerCase(); } catch { return ''; }
}

/**
 * Readable version of a sync's last_error (the backend reports English
 * technical text). Known patterns get a plain-language line; anything else
 * is shown as reported so nothing is hidden.
 */
export function friendlySyncError(error: string, locale: Locale = DEFAULT_LOCALE): string {
  const text = error.toLowerCase();
  const key = /http 40[137]\b|authentication|credential rejected|access denied/.test(text) ? 'portal.importSync.error.auth'
    : /http 404|http 410|page not found/.test(text) ? 'portal.importSync.error.notFound'
      : /robots\.txt/.test(text) ? 'portal.importSync.error.robots'
        : /private|loopback|link-local|blocked address/.test(text) ? 'portal.importSync.error.private'
          : /stalled|worker lost|worker was lost/.test(text) ? 'portal.importSync.error.stalled'
            : /http 429|rate limit/.test(text) ? 'portal.importSync.error.rateLimited'
              : /collection no longer|no longer controllable|collection assignment/.test(text) ? 'portal.importSync.error.space'
                : null;
  return key ? `${translate(locale, key)} (${error})` : error;
}

/**
 * Auto-refresh cadence choices surfaced in the Sources UI, in seconds. The
 * server clamps below its configured minimum
 * (confluence_refresh_min_interval_seconds, default 900s) rather than
 * rejecting the request.
 */
export function refreshIntervalOptions(locale: Locale = DEFAULT_LOCALE): { value: number; label: string }[] {
  return [
    { value: 3600, label: translate(locale, 'portal.imports.refreshHourly') },
    { value: 21600, label: translate(locale, 'portal.imports.refreshEvery6Hours') },
    { value: 86400, label: translate(locale, 'portal.imports.refreshDaily') },
    { value: 604800, label: translate(locale, 'portal.imports.refreshWeekly') },
  ];
}

/**
 * Label for a refresh_interval_seconds value not covered by
 * REFRESH_INTERVAL_OPTIONS -- notably the server's own floor default
 * (confluence_refresh_min_interval_seconds, 900s) that PATCH /import/sources
 * applies when a source is enabled via the toggle alone, without the caller
 * ever picking one of the options above. Without this, the Sources select
 * would carry a `value` matching none of its `<option>`s and render blank.
 */
export function formatRefreshInterval(seconds: number, locale: Locale = DEFAULT_LOCALE): string {
  const preset = refreshIntervalOptions(locale).find(option => option.value === seconds);
  if (preset) return preset.label;
  if (seconds % 3600 === 0) return translate(locale, 'portal.imports.everyHours', { count: seconds / 3600 });
  if (seconds % 60 === 0) return translate(locale, 'portal.imports.everyMinutes', { count: seconds / 60 });
  return translate(locale, 'portal.imports.everySeconds', { count: seconds });
}

export type ImportSourceListResponse = {
  items: ImportSource[];
};

export type ImportSourceTestResponse = {
  ok: boolean;
  detail: string | null;
  server_kind: string | null;
};

export type ImportRun = {
  id: string;
  sync_id?: string | null;
  kind: string;
  status: ImportRunStatus;
  scope_type: string;
  scope_value: string;
  root_page_title: string;
  can_sync?: boolean;
  // Optional while older API deployments are rolling forward. A missing
  // value preserves the existing terminal-run edit affordance.
  can_edit?: boolean;
  missing_page_count?: number;
  pages_discovered: number;
  pages_imported: number;
  pages_failed: number;
  attachments_saved: number;
  artifact_bytes: number;
  content_bytes: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  owner: { id: string; username: string } | null;
};

export type ImportRunListResponse = {
  items: ImportRun[];
};

export type ImportRunError = {
  page_id: string;
  title: string;
  error: string;
};

export type ImportRunJobSummary = {
  id: string;
  title: string;
  status: ImportJobStatus;
};

export type ImportRunDetail = ImportRun & {
  missing_pages?: MissingConfluencePage[];
  can_remove_missing?: boolean;
  current_page_title: string;
  error_message: string | null;
  cancel_requested: boolean;
  errors: ImportRunError[];
  jobs: ImportRunJobSummary[];
};

export type MissingConfluencePage = {
  page_id: string;
  title: string;
  job_id: string | null;
  url: string;
  withdrawal_status?: 'pending' | 'sent';
  withdrawal_error?: string | null;
};

export type ImportRunCancelResponse = {
  id: string;
  status: ImportRunStatus;
  cancel_requested: boolean;
};

export const RUN_ACTIVE_STATUSES: ImportRunStatus[] = ['pending', 'running'];

export function isRunActive(status: ImportRunStatus): boolean {
  return RUN_ACTIVE_STATUSES.includes(status);
}

/** Status chip classes, matching the job-status badge visual language. */
export const runStatusChip: Record<ImportRunStatus, string> = {
  pending: 'bg-slate-100 text-slate-700',
  running: 'bg-sky-100 text-sky-800',
  finished: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-amber-100 text-amber-800',
};

export const importJobStatusChip: Record<ImportJobStatus, string> = {
  PENDING: 'bg-slate-100 text-slate-700',
  RUNNING: 'bg-sky-100 text-sky-800',
  FINISHED: 'bg-emerald-100 text-emerald-800',
  FAILED: 'bg-red-100 text-red-700',
};

export function runScopeLabel(run: Pick<ImportRun, 'scope_type' | 'scope_value'>, locale: Locale = DEFAULT_LOCALE): string {
  if (run.scope_type === 'url') return translate(locale, 'portal.imports.scopeUrl', { value: run.scope_value });
  return run.scope_type === 'space'
    ? translate(locale, 'portal.imports.scopeSpace', { value: run.scope_value })
    : translate(locale, 'portal.imports.scopePage', { value: run.scope_value });
}

export function runTitle(run: Pick<ImportRun, 'root_page_title' | 'scope_type' | 'scope_value'>, locale: Locale = DEFAULT_LOCALE): string {
  return run.root_page_title.trim() || runScopeLabel(run, locale);
}

/**
 * Matches import_test_cooldown_seconds' documented default: the 429 from
 * POST /import/sources/{id}/test carries no Retry-After header, so the
 * client falls back to this window before re-enabling the button.
 */
export const TEST_COOLDOWN_FALLBACK_MS = 10_000;
