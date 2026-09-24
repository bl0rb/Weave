'use client';

import { useCallback, useEffect, useState } from 'react';
import { LoaderCircle, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ApiError, apiFetch, apiJson, type ApiFetchInit } from '@/lib/api';
import type { ListResponse } from '@/lib/auth-types';

/** Shared input styling (matches the app's form fields) — see `.ui-control` in globals.css for the actual sizing/radius/focus tokens. */
export const inputClass = 'ui-control mt-1.5';

/** Normalize any thrown value into a user-facing message (backend detail verbatim). */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return 'Unexpected error';
}

/**
 * Like apiJson but for endpoints whose success body we do not need
 * (e.g. DELETE, which may return an empty body).
 */
export async function apiSend(path: string, init?: ApiFetchInit): Promise<void> {
  const res = await apiFetch(path, init);
  if (!res.ok) {
    let detail = `Request failed with status ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // Non-JSON error body — keep the generic message.
    }
    throw new ApiError(res.status, detail);
  }
}

/** Fetch + refetch a {items: T[]} list endpoint. */
export function useAdminList<T>(path: string): {
  items: T[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
} {
  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const data = await apiJson<ListResponse<T>>(path);
      setItems(data.items);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [path]);

  // Initial load (reload() covers post-mutation refreshes).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJson<ListResponse<T>>(path);
        if (!cancelled) {
          setItems(data.items);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(errorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [path]);

  return { items, loading, error, reload };
}

export function SectionCard({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-[var(--line)] bg-[var(--surface)] shadow-[0_1px_2px_rgb(0_0_0_/_5%)]">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
        <div>
          <h2 className="text-[17px] font-semibold text-[var(--ink)]">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-[var(--muted)]">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

export function ErrorNotice({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      {message}
    </div>
  );
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-[var(--muted)]">
      <LoaderCircle className="h-4 w-4 animate-spin text-emerald-600" />
      {label}
    </div>
  );
}

export function Badge({
  tone,
  children,
}: {
  tone: 'emerald' | 'slate' | 'red' | 'amber';
  children: React.ReactNode;
}) {
  const tones: Record<string, string> = {
    emerald: 'bg-emerald-50 text-emerald-700',
    slate: 'bg-slate-100 text-slate-600',
    red: 'bg-red-50 text-red-700',
    amber: 'bg-amber-50 text-amber-700',
  };
  return (
    <span
      className={`inline-flex h-6 items-center rounded-full px-2.5 text-xs font-semibold uppercase tracking-wide ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  /** Optional validation message, rendered in place of `hint` when present. */
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-[13px] font-semibold text-[var(--ink-2)]">
      {label}
      {children}
      {error ? (
        <span className="mt-1.5 block text-xs font-normal text-red-600">{error}</span>
      ) : (
        hint && <span className="mt-1.5 block text-xs font-normal text-[var(--muted)]">{hint}</span>
      )}
    </label>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  /** Blocks pointer AND keyboard activation (native `disabled`, not just a pointer-events CSS hack). */
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-disabled={disabled}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex items-center gap-2.5 disabled:pointer-events-none disabled:opacity-50"
    >
      <span
        className={`relative inline-block h-5 w-9 flex-shrink-0 rounded-full transition ${
          checked ? 'bg-emerald-600' : 'bg-slate-200'
        }`}
      >
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${
            checked ? 'left-[18px]' : 'left-0.5'
          }`}
        />
      </span>
      <span className="text-sm font-medium text-slate-700">{label}</span>
    </button>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  /** Optional right-aligned action row, rendered as a bordered footer strip below the body. */
  footer?: React.ReactNode;
}) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-0 sm:p-4">
      <div
        className="absolute inset-0 bg-slate-950/30 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="relative flex max-h-[90dvh] w-full max-w-lg flex-col overflow-y-auto rounded-xl border border-[var(--line)] bg-[var(--surface)] shadow-2xl">
        <div className="flex items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
          <h3 className="min-w-0 break-words text-[17px] font-semibold text-[var(--ink)]">{title}</h3>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-[var(--muted)] transition hover:bg-[var(--hover)] hover:text-[var(--ink)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="p-5">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-[var(--line)] px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  /** May throw — the error detail is rendered inside the dialog. */
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="danger" size="sm" onClick={handleConfirm} disabled={busy}>
            {busy && <LoaderCircle className="h-4 w-4 animate-spin" />}
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="text-sm text-[var(--ink-2)]">{body}</div>
      <ErrorNoticeSpaced message={error} />
    </Modal>
  );
}

function ErrorNoticeSpaced({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      {message}
    </div>
  );
}
