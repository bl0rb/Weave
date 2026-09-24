'use client';

import { useRef, useState } from 'react';
import { Building2, Check, LoaderCircle, Pencil, Plus, Trash2, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import type { Team, TeamRequest } from '@/lib/auth-types';
import {
  apiSend,
  ConfirmDialog,
  ErrorNotice,
  errorMessage,
  inputClass,
  LoadingState,
  SectionCard,
  useAdminList,
} from '@/components/admin/admin-shared';
import { useI18n } from '@/i18n/provider';

export function TeamsTab() {
  const { t, formatDate } = useI18n();
  const teams = useAdminList<Team>('/api/v1/auth/admin/teams');
  const newNameRef = useRef<HTMLInputElement>(null);

  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  const [deleting, setDeleting] = useState<Team | null>(null);

  async function createTeam(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      const body: TeamRequest = { name: newName.trim() };
      await apiJson<Team>('/api/v1/auth/admin/teams', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setNewName('');
      await teams.reload();
    } catch (err) {
      setCreateError(errorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  function startRename(team: Team) {
    setRenamingId(team.id);
    setRenameValue(team.name);
    setRenameError(null);
  }

  async function saveRename(id: string) {
    setRenameBusy(true);
    setRenameError(null);
    try {
      const body: TeamRequest = { name: renameValue.trim() };
      await apiJson<Team>(`/api/v1/auth/admin/teams/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setRenamingId(null);
      await teams.reload();
    } catch (err) {
      setRenameError(errorMessage(err));
    } finally {
      setRenameBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionCard title={t('admin.teams.createTitle')} description={t('admin.teams.createDescription')}>
        <form onSubmit={createTeam} className="flex flex-wrap items-center gap-3">
          <input
            ref={newNameRef}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={t('admin.teams.namePlaceholder')}
            required
            className={`${inputClass} mt-0 w-full max-w-xs`}
          />
          <Button type="submit" disabled={creating || !newName.trim()}>
            {creating ? (
              <LoaderCircle className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            {t('admin.teams.createTitle')}
          </Button>
        </form>
        {createError && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            {createError}
          </p>
        )}
      </SectionCard>

      <SectionCard title={t('admin.teams.title')} description={t('admin.teams.description')}>
        <ErrorNotice message={teams.error} />
        {teams.loading ? (
          <LoadingState label={t('admin.teams.loading')} />
        ) : teams.items.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <Building2 className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-500">{t('admin.teams.empty')}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                newNameRef.current?.focus();
                newNameRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
              }}
            >
              <Plus className="h-4 w-4" />
              {t('admin.teams.createTitle')}
            </Button>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {teams.items.map((team) => (
              <li key={team.id} className="flex items-center justify-between gap-3 py-3">
                {renamingId === team.id ? (
                  <div className="flex flex-1 flex-wrap items-center gap-2">
                    <input
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      className={`${inputClass} mt-0 w-full max-w-xs`}
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          void saveRename(team.id);
                        }
                        if (e.key === 'Escape') setRenamingId(null);
                      }}
                    />
                    <button
                      onClick={() => saveRename(team.id)}
                      disabled={renameBusy || !renameValue.trim()}
                      aria-label={t('admin.teams.saveNameAria')}
                      title={t('common.save')}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-emerald-600 transition hover:bg-emerald-50 disabled:opacity-50"
                    >
                      {renameBusy ? (
                        <LoaderCircle className="h-4 w-4 animate-spin" />
                      ) : (
                        <Check className="h-4 w-4" />
                      )}
                    </button>
                    <button
                      onClick={() => setRenamingId(null)}
                      disabled={renameBusy}
                      aria-label={t('admin.teams.cancelRenameAria')}
                      title={t('common.cancel')}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-50 hover:text-slate-700"
                    >
                      <X className="h-4 w-4" />
                    </button>
                    {renameError && (
                      <p role="alert" className="w-full text-sm text-red-700">
                        {renameError}
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-950">{team.name}</p>
                    <p className="text-xs text-slate-400">
                      {t('admin.teams.createdOn', { date: formatDate(team.created_at) })}
                    </p>
                  </div>
                )}
                {renamingId !== team.id && (
                  <div className="flex flex-shrink-0 gap-1">
                    <button
                      onClick={() => startRename(team)}
                      aria-label={t('admin.teams.renameAria', { team: team.name })}
                      title={t('admin.teams.renameTooltip')}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-50 hover:text-slate-700"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => setDeleting(team)}
                      aria-label={t('admin.teams.deleteAria', { team: team.name })}
                      title={t('common.delete')}
                      className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-red-50 hover:text-red-600"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      {deleting && (
        <ConfirmDialog
          title={t('admin.teams.deleteTitle')}
          body={
            <p>
              {t('admin.teams.deleteBodyPrefix')} <span className="font-semibold text-slate-950">{deleting.name}</span>
              {t('admin.teams.deleteBodySuffix')}
            </p>
          }
          confirmLabel={t('admin.teams.deleteTitle')}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await apiSend(`/api/v1/auth/admin/teams/${deleting.id}`, { method: 'DELETE' });
            setDeleting(null);
            await teams.reload();
          }}
        />
      )}
    </div>
  );
}
