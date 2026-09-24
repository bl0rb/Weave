'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertTriangle, Archive, Download, HardDriveDownload, RotateCcw, Trash2, Upload } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ApiError, apiFetch, apiJson, loginUrl } from '@/lib/api';
import { useVisiblePolling } from '@/lib/data-cache';
import {
  Badge,
  ConfirmDialog,
  ErrorNotice,
  Field,
  LoadingState,
  SectionCard,
  apiSend,
  errorMessage,
  inputClass,
} from './admin-shared';

const BASE_PATH = '/api/v1/admin/backup';
const MIN_PASSPHRASE_LENGTH = 12;
const POLL_INTERVAL_MS = 2000;

type BackupRunStatus = 'queued' | 'running' | 'finished' | 'failed';
type BackupRunKind = 'export' | 'import';

type ImportReport = {
  tables: Record<string, number>;
  files_restored: number;
  warnings: string[];
  skipped: string[];
  requeued_releases: number;
  requires_relogin: boolean;
  indexing_note: string;
};

type ExportReport = { tables: Record<string, number> };

type BackupRun = {
  id: string;
  kind: BackupRunKind;
  status: BackupRunStatus;
  file_name: string | null;
  size_bytes: number | null;
  progress: { table?: string; rows_done?: number };
  report: ExportReport | ImportReport | null;
  error_message: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
};

type TargetState = { fresh: boolean; reasons: string[] };

function isRunActive(run: BackupRun | null | undefined): boolean {
  return run != null && (run.status === 'queued' || run.status === 'running');
}

function formatDate(value: string | null): string {
  if (!value) return '–';
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
}

function formatBytes(value: number | null): string {
  if (value == null) return '–';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function rowTotal(tables: Record<string, number> | undefined): number {
  if (!tables) return 0;
  return Object.values(tables).reduce((sum, count) => sum + count, 0);
}

export function BackupTab() {
  const [runs, setRuns] = useState<BackupRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [targetState, setTargetState] = useState<TargetState | null>(null);
  const [targetStateError, setTargetStateError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<BackupRun | null>(null);
  // A finished import wipes-and-reinserts `users`, which cascades onto the
  // importing admin's own session row (see backup.py's module docstring on
  // requires_relogin). The very next poll after that commit gets a 401, so
  // we use skipAuthRedirect here and show our own "please log in again"
  // panel instead of letting apiFetch hard-redirect the admin away before
  // the completion report can be seen.
  const [sessionExpired, setSessionExpired] = useState(false);

  const reloadRuns = useCallback(async () => {
    try {
      const data = await apiJson<{ runs: BackupRun[] }>(`${BASE_PATH}/runs`, { skipAuthRedirect: true });
      setRuns(data.runs);
      setRunsError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setSessionExpired(true);
        return;
      }
      setRunsError(errorMessage(err));
    } finally {
      setRunsLoading(false);
    }
  }, []);

  const reloadTargetState = useCallback(async () => {
    try {
      const data = await apiJson<TargetState>(`${BASE_PATH}/target-state`, { skipAuthRedirect: true });
      setTargetState(data);
      setTargetStateError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setSessionExpired(true);
        return;
      }
      setTargetStateError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    // Both loaders only set state after their first await, so nothing
    // cascades synchronously; the rule can't see through the async call.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reloadRuns();
    void reloadTargetState();
  }, [reloadRuns, reloadTargetState]);

  const activeRun = runs.find(isRunActive) ?? null;

  useVisiblePolling(() => {
    void reloadRuns();
  }, activeRun && !sessionExpired ? POLL_INTERVAL_MS : null);

  // A finished/failed import changes what target-state reports (freshness),
  // so refresh it whenever the active run disappears.
  const hadActiveRun = useRef(false);
  useEffect(() => {
    if (activeRun) {
      hadActiveRun.current = true;
    } else if (hadActiveRun.current) {
      hadActiveRun.current = false;
      void reloadTargetState();
    }
  }, [activeRun, reloadTargetState]);

  const exportRuns = runs.filter((run) => run.kind === 'export');
  const latestImportRun = runs.find((run) => run.kind === 'import') ?? null;

  return (
    <div className="space-y-6">
      <SectionCard
        title="Sicherung erstellen"
        description="Exportiert alle bestehenden Daten (Nutzer, Wissensbereiche, Aufträge, Verbindungen samt Zugangsdaten, Dateien) in ein einzelnes, verschlüsseltes Archiv. Nicht enthalten: Chat-Unterhaltungen (Weave-API), lokale Bot-YAMLs und der Wissensindex — dieser wird nach einer Wiederherstellung automatisch neu aufgebaut."
      >
        <ExportForm disabled={isRunActive(activeRun)} onStarted={reloadRuns} />

        <h3 className="mt-6 mb-2 text-sm font-semibold text-slate-800">Vorhandene Sicherungen</h3>
        <ErrorNotice message={runsError} />
        {runsLoading ? (
          <LoadingState label="Sicherungen werden geladen…" />
        ) : exportRuns.length === 0 ? (
          <p className="py-4 text-sm text-slate-500">Noch keine Sicherung erstellt.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {exportRuns.map((run) => (
              <ExportRunRow key={run.id} run={run} onDelete={() => setDeleting(run)} />
            ))}
          </ul>
        )}
      </SectionCard>

      <SectionCard
        title="Wiederherstellung"
        description="Spielt ein zuvor erstelltes Sicherungsarchiv ein. Im Notfall: Ingest frisch installieren, den ersten neuen Administrator anlegen (er muss nicht der ursprüngliche sein) und hier das Archiv einspielen."
      >
        {sessionExpired ? (
          <SessionExpiredNotice />
        ) : (
          <>
            <TargetStateBadge state={targetState} error={targetStateError} />
            <ImportForm
              disabled={isRunActive(activeRun)}
              allowForce={targetState ? !targetState.fresh : false}
              onStarted={async () => {
                await reloadRuns();
              }}
            />
            {latestImportRun && <ImportRunStatusPanel run={latestImportRun} />}
          </>
        )}
      </SectionCard>

      {deleting && (
        <ConfirmDialog
          title="Sicherung löschen"
          body={
            <p>
              Die Sicherung <strong className="text-slate-950">{deleting.file_name}</strong> unwiderruflich löschen?
              Das Archiv kann danach nicht mehr heruntergeladen oder eingespielt werden.
            </p>
          }
          confirmLabel="Sicherung löschen"
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await apiSend(`${BASE_PATH}/exports/${encodeURIComponent(deleting.id)}`, { method: 'DELETE' });
            setDeleting(null);
            await reloadRuns();
          }}
        />
      )}
    </div>
  );
}

// The wipe-and-reinsert of `users` during an import invalidates the
// importing admin's own session (see backup.py's requires_relogin). The
// completion report only lands in the DB after that commit, so it can
// never be fetched by the now-dead session — the admin has to log back in
// to see it. Shown in place of the import form/status once a 401 fires.
function SessionExpiredNotice() {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
      <div className="flex items-center gap-2 font-medium">
        <AlertTriangle size={15} />
        Sitzung abgelaufen
      </div>
      <p className="mt-1">
        Die Wiederherstellung hat bestehende Sitzungen ungültig gemacht — bitte neu anmelden. Das Ergebnis der
        Wiederherstellung wird danach hier angezeigt.
      </p>
      <a className="mt-2 inline-block font-medium text-amber-900 underline" href={loginUrl()}>
        Zur Anmeldung
      </a>
    </div>
  );
}

function TargetStateBadge({ state, error }: { state: TargetState | null; error: string | null }) {
  if (error) return <ErrorNotice message={error} />;
  if (!state) return <LoadingState label="Zielzustand wird geprüft…" />;
  if (state.fresh) {
    return (
      <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
        Dieses Ziel ist frisch — eine Wiederherstellung kann ohne Überschreiben eingespielt werden.
      </div>
    );
  }
  return (
    <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
      <div className="flex items-center gap-2 font-medium">
        <AlertTriangle size={15} />
        Dieses Ziel ist nicht frisch — es enthält bereits Daten.
      </div>
      {state.reasons.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-5">
          {state.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ExportForm({ disabled, onStarted }: { disabled: boolean; onStarted: () => Promise<void> }) {
  const [passphrase, setPassphrase] = useState('');
  const [confirmPassphrase, setConfirmPassphrase] = useState('');
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tooShort = passphrase.length > 0 && passphrase.length < MIN_PASSPHRASE_LENGTH;
  const mismatch = confirmPassphrase.length > 0 && passphrase !== confirmPassphrase;
  const canStart =
    !disabled && !starting && passphrase.length >= MIN_PASSPHRASE_LENGTH && passphrase === confirmPassphrase;

  async function start(event: FormEvent) {
    event.preventDefault();
    if (!canStart) return;
    setStarting(true);
    setError(null);
    try {
      await apiJson(`${BASE_PATH}/exports`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passphrase }),
      });
      setPassphrase('');
      setConfirmPassphrase('');
      await onStarted();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setStarting(false);
    }
  }

  return (
    <form className="space-y-4" onSubmit={start}>
      <ErrorNotice message={error} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Passphrase für die Sicherung" hint={`Mindestens ${MIN_PASSPHRASE_LENGTH} Zeichen. Verschlüsselt das Archiv — separat vom Server aufbewahren, sie wird nirgends gespeichert.`}>
          <input
            className={inputClass}
            type="password"
            required
            minLength={MIN_PASSPHRASE_LENGTH}
            value={passphrase}
            onChange={(event) => setPassphrase(event.target.value)}
            autoComplete="new-password"
          />
          {tooShort && <span className="mt-1 block text-xs text-red-600">Mindestens {MIN_PASSPHRASE_LENGTH} Zeichen.</span>}
        </Field>
        <Field label="Passphrase wiederholen">
          <input
            className={inputClass}
            type="password"
            required
            value={confirmPassphrase}
            onChange={(event) => setConfirmPassphrase(event.target.value)}
            autoComplete="new-password"
          />
          {mismatch && <span className="mt-1 block text-xs text-red-600">Stimmt nicht überein.</span>}
        </Field>
      </div>
      <Button type="submit" disabled={!canStart}>
        <HardDriveDownload size={15} />
        {starting ? 'Wird gestartet…' : disabled ? 'Es läuft bereits ein Vorgang…' : 'Sicherung erstellen'}
      </Button>
    </form>
  );
}

function ExportRunRow({ run, onDelete }: { run: BackupRun; onDelete: () => void }) {
  const report = run.report as ExportReport | null;
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Archive size={15} className="text-emerald-700" />
          <span className="font-medium text-slate-950">{run.file_name ?? `Sicherung ${run.id.slice(0, 8)}`}</span>
          <RunStatusBadge status={run.status} />
        </div>
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-slate-500">
          <span>{formatDate(run.created_at)}</span>
          <span>{formatBytes(run.size_bytes)}</span>
          {report && <span>{rowTotal(report.tables)} Zeilen in {Object.keys(report.tables).length} Tabellen</span>}
        </div>
        {run.status === 'running' && run.progress.table && (
          <p className="mt-1 text-xs text-slate-500">
            Läuft: Tabelle „{run.progress.table}“ ({run.progress.rows_done ?? 0} Zeilen)…
          </p>
        )}
        {run.status === 'failed' && run.error_message && (
          <p className="mt-1 text-xs text-red-600">{run.error_message}</p>
        )}
      </div>
      <div className="flex gap-1">
        {run.status === 'finished' && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              void (async () => {
                const response = await apiFetch(`${BASE_PATH}/exports/${encodeURIComponent(run.id)}/download`);
                if (!response.ok) return;
                const blob = await response.blob();
                const href = window.URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = href;
                link.download = run.file_name ?? 'weave-backup.tar.gz';
                document.body.appendChild(link);
                link.click();
                link.remove();
                window.URL.revokeObjectURL(href);
              })()
            }
          >
            <Download size={15} />
            Herunterladen
          </Button>
        )}
        {run.status !== 'queued' && run.status !== 'running' && (
          <Button variant="ghost" size="sm" onClick={onDelete} aria-label={`${run.file_name ?? 'Sicherung'} löschen`}>
            <Trash2 size={15} />
            Löschen
          </Button>
        )}
      </div>
    </li>
  );
}

function RunStatusBadge({ status }: { status: BackupRunStatus }) {
  const labels: Record<BackupRunStatus, string> = {
    queued: 'Wartet',
    running: 'Läuft',
    finished: 'Fertig',
    failed: 'Fehlgeschlagen',
  };
  const tones: Record<BackupRunStatus, 'slate' | 'amber' | 'emerald' | 'red'> = {
    queued: 'slate',
    running: 'amber',
    finished: 'emerald',
    failed: 'red',
  };
  return <Badge tone={tones[status]}>{labels[status]}</Badge>;
}

function ImportForm({
  disabled,
  allowForce,
  onStarted,
}: {
  disabled: boolean;
  allowForce: boolean;
  onStarted: () => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [passphrase, setPassphrase] = useState('');
  const [force, setForce] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canStart = !disabled && !starting && file != null && passphrase.length > 0 && (allowForce ? force : true);

  async function start(event: FormEvent) {
    event.preventDefault();
    if (!file || !canStart) return;
    setStarting(true);
    setError(null);
    try {
      const body = new FormData();
      body.append('file', file);
      body.append('passphrase', passphrase);
      body.append('force', String(force));
      const response = await apiFetch(`${BASE_PATH}/imports`, { method: 'POST', body });
      if (!response.ok) {
        let detail = `Wiederherstellung fehlgeschlagen (${response.status})`;
        try {
          const responseBody = await response.json();
          if (typeof responseBody?.detail === 'string') detail = responseBody.detail;
        } catch {
          // Non-JSON error body — keep the generic message.
        }
        throw new Error(detail);
      }
      setFile(null);
      setPassphrase('');
      setForce(false);
      await onStarted();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setStarting(false);
    }
  }

  return (
    <form className="space-y-4 border-t border-slate-100 pt-4" onSubmit={start}>
      <ErrorNotice message={error} />
      <Field label="Sicherungsarchiv" hint="Die .weave-backup.tar.gz-Datei aus einer früheren Sicherung.">
        <input
          className={inputClass}
          type="file"
          accept=".gz,.tar.gz"
          required
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </Field>
      <Field label="Passphrase des Archivs" hint="Die beim Erstellen der Sicherung vergebene Passphrase.">
        <input
          className={inputClass}
          type="password"
          required
          value={passphrase}
          onChange={(event) => setPassphrase(event.target.value)}
          autoComplete="current-password"
        />
      </Field>
      {allowForce && (
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={force} onChange={(event) => setForce(event.target.checked)} />
          Vorhandene Daten überschreiben
        </label>
      )}
      <Button type="submit" disabled={!canStart}>
        <Upload size={15} />
        {starting ? 'Wird gestartet…' : disabled ? 'Es läuft bereits ein Vorgang…' : 'Wiederherstellung starten'}
      </Button>
    </form>
  );
}

function ImportRunStatusPanel({ run }: { run: BackupRun }) {
  if (run.status === 'queued' || run.status === 'running') {
    return (
      <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        <div className="flex items-center gap-2 font-medium">
          <RotateCcw size={15} className="animate-spin" />
          Wiederherstellung läuft…
        </div>
        {run.progress.table && (
          <p className="mt-1 text-xs">
            Tabelle „{run.progress.table}“ ({run.progress.rows_done ?? 0} Zeilen)
          </p>
        )}
      </div>
    );
  }
  if (run.status === 'failed') {
    return (
      <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        <p className="font-medium">Wiederherstellung fehlgeschlagen.</p>
        {run.error_message && <p className="mt-1">{run.error_message}</p>}
      </div>
    );
  }
  if (run.status === 'finished' && run.report) {
    const report = run.report as ImportReport;
    return (
      <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
        <p className="font-medium">Wiederherstellung abgeschlossen.</p>
        <ul className="mt-2 space-y-0.5">
          <li>{rowTotal(report.tables)} Zeilen in {Object.keys(report.tables).length} Tabellen wiederhergestellt</li>
          <li>{report.files_restored} Datei(en) wiederhergestellt</li>
          <li>{report.requeued_releases} Veröffentlichung(en) zur Neuindizierung eingereiht</li>
        </ul>
        <p className="mt-2 font-medium">Der Wissensindex wird jetzt automatisch neu aufgebaut.</p>
        <p className="mt-1 text-xs text-emerald-800">{report.indexing_note}</p>
        {report.requires_relogin && (
          <p className="mt-2 font-medium text-emerald-900">Bitte neu anmelden — bestehende Sitzungen wurden durch die Wiederherstellung ungültig.</p>
        )}
        {report.skipped.length > 0 && (
          <p className="mt-2 text-xs">Übersprungen: {report.skipped.join(', ')}</p>
        )}
        {report.warnings.length > 0 && (
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-amber-800">
            {report.warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  return null;
}
