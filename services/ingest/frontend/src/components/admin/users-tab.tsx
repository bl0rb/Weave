'use client';

import { useState } from 'react';
import { Briefcase, LoaderCircle, Pencil, Plus, Trash2, Users as UsersIcon } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api';
import type {
  AdminUserCreateRequest,
  AdminUserUpdateRequest,
  AuthUser,
  ClaimOwnerlessRequest,
  ClaimOwnerlessResponse,
  Team,
  UserRole,
} from '@/lib/auth-types';
import {
  apiSend,
  Badge,
  ConfirmDialog,
  ErrorNotice,
  errorMessage,
  Field,
  inputClass,
  LoadingState,
  Modal,
  SectionCard,
  Toggle,
  useAdminList,
} from '@/components/admin/admin-shared';
import { useI18n } from '@/i18n/provider';

const NO_TEAM = '';

export function UsersTab() {
  const { t, formatDate } = useI18n();
  const users = useAdminList<AuthUser>('/api/v1/auth/admin/users');
  const teams = useAdminList<Team>('/api/v1/auth/admin/teams');

  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<AuthUser | null>(null);
  const [deleting, setDeleting] = useState<AuthUser | null>(null);

  const teamName = (id: string | null) =>
    id === null ? '—' : (teams.items.find((t) => t.id === id)?.name ?? `#${id}`);
  const teamRoleLabel = (role: 'member' | 'reader') =>
    role === 'reader' ? t('admin.users.teamRole.reader') : t('admin.users.teamRole.member');

  return (
    <div className="space-y-6">
      <SectionCard
        title={t('admin.users.title')}
        description={t('admin.users.description')}
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" />
            {t('admin.users.create')}
          </Button>
        }
      >
        <ErrorNotice message={users.error} />
        {users.loading ? (
          <LoadingState label={t('admin.users.loading')} />
        ) : users.items.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <UsersIcon className="h-8 w-8 text-slate-300" />
            <p className="text-sm text-slate-500">{t('admin.users.empty')}</p>
            <Button variant="outline" size="sm" onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" />
              {t('admin.users.create')}
            </Button>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full table-auto text-left text-xs sm:text-sm">
              <thead className="text-slate-500">
                <tr>
                  <th className="pb-2 pr-3 font-semibold">{t('common.username')}</th>
                  <th className="hidden pb-2 pr-3 font-semibold md:table-cell">{t('common.email')}</th>
                  <th className="pb-2 pr-3 font-semibold">{t('admin.users.column.role')}</th>
                  <th className="hidden pb-2 pr-3 font-semibold lg:table-cell">{t('admin.users.column.teams')}</th>
                  <th className="pb-2 pr-3 font-semibold">{t('admin.users.active')}</th>
                  <th className="hidden pb-2 pr-3 font-semibold sm:table-cell">{t('admin.users.column.sso')}</th>
                  <th className="hidden pb-2 pr-3 font-semibold xl:table-cell">{t('admin.users.column.created')}</th>
                  <th className="pb-2 font-semibold" />
                </tr>
              </thead>
              <tbody>
                {users.items.map((u) => (
                  <tr key={u.id} className="border-t border-slate-100">
                    <td className="py-3 pr-3 font-medium text-slate-950">{u.username}</td>
                    <td className="hidden py-3 pr-3 text-slate-700 md:table-cell">{u.email}</td>
                    <td className="py-3 pr-3">
                      <Badge tone={u.role === 'admin' ? 'emerald' : 'slate'}>
                        {u.role === 'admin' ? t('admin.users.role.admin') : t('admin.users.role.user')}
                      </Badge>
                    </td>
                    <td className="hidden py-3 pr-3 text-slate-700 lg:table-cell">
                      <span className="block max-w-xs break-words">
                        {(u.team_ids ?? (u.team_id ? [u.team_id] : [])).map((id) => `${teamName(id)} (${teamRoleLabel(u.team_roles?.[id] ?? 'member')})`).join(', ') || '—'}
                      </span>
                    </td>
                    <td className="py-3 pr-3">
                      <Badge tone={u.is_active ? 'emerald' : 'red'}>
                        {u.is_active ? t('admin.users.active') : t('admin.users.inactive')}
                      </Badge>
                    </td>
                    <td className="hidden py-3 pr-3 sm:table-cell">
                      {u.oidc_provider_id !== null ? (
                        <Badge tone="emerald">{t('admin.users.column.sso')}</Badge>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="hidden py-3 pr-3 text-slate-700 xl:table-cell">
                      {formatDate(u.created_at)}
                    </td>
                    <td className="py-3">
                      <div className="flex justify-end gap-1">
                        <button
                          onClick={() => setEditing(u)}
                          aria-label={t('admin.users.editAria', { username: u.username })}
                          title={t('common.edit')}
                          className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-50 hover:text-slate-700"
                        >
                          <Pencil className="h-4 w-4" />
                        </button>
                        <button
                          onClick={() => setDeleting(u)}
                          aria-label={t('admin.users.deleteAria', { username: u.username })}
                          title={t('common.delete')}
                          className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-red-50 hover:text-red-600"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <ClaimOwnerlessCard users={users.items} />

      {creating && (
        <CreateUserModal
          teams={teams.items}
          onClose={() => setCreating(false)}
          onSaved={async () => {
            setCreating(false);
            await users.reload();
          }}
        />
      )}

      {editing && (
        <EditUserModal
          user={editing}
          teams={teams.items}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await users.reload();
          }}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title={t('admin.users.deleteTitle')}
          body={
            <p>
              {t('admin.users.deleteBodyPrefix')} <span className="font-semibold text-slate-950">{deleting.username}</span>
              {t('admin.users.deleteBodySuffix')}
            </p>
          }
          confirmLabel={t('admin.users.deleteTitle')}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await apiSend(`/api/v1/auth/admin/users/${deleting.id}`, { method: 'DELETE' });
            setDeleting(null);
            await users.reload();
          }}
        />
      )}
    </div>
  );
}

function TeamMembershipFields({
  teams,
  primary,
  selected,
  roles,
  onChange,
}: {
  teams: Team[];
  primary: string;
  selected: string[];
  roles: Record<string, 'member' | 'reader'>;
  onChange: (primary: string, selected: string[], roles: Record<string, 'member' | 'reader'>) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="space-y-3">
      <Field label={t('admin.users.primaryTeam')}>
        <select
          aria-label={t('admin.users.primaryTeam')}
          value={primary}
          onChange={(event) => {
            const next = event.target.value;
            onChange(next, next ? Array.from(new Set([...selected, next])) : [], roles);
          }}
          className={inputClass}
        >
          <option value={NO_TEAM}>{t('admin.users.noTeam')}</option>
          {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
        </select>
      </Field>
      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm font-medium text-slate-700">{t('admin.users.teamAccess')}</legend>
        <div className="max-h-48 space-y-2 overflow-y-auto">
          {teams.map((team) => (
            <label key={team.id} className="flex items-start gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={selected.includes(team.id)}
                disabled={team.id === primary}
                onChange={(event) => {
                  const next = event.target.checked ? [...selected, team.id] : selected.filter((id) => id !== team.id);
                  onChange(primary || next[0] || NO_TEAM, next, { ...roles, ...(event.target.checked ? { [team.id]: roles[team.id] ?? 'member' } : {}) });
                }}
                className="mt-1 shrink-0"
              />
              <span className="min-w-0 break-words">{team.name}</span>
              {selected.includes(team.id) && <select aria-label={t('admin.users.teamRoleAria', { team: team.name })} value={roles[team.id] ?? 'member'} onChange={(event) => onChange(primary, selected, { ...roles, [team.id]: event.target.value as 'member' | 'reader' })} className="ml-auto rounded border border-slate-200 px-1 py-0.5 text-xs"><option value="member">{t('admin.users.teamRole.member')}</option><option value="reader">{t('admin.users.teamRole.reader')}</option></select>}
            </label>
          ))}
        </div>
      </fieldset>
    </div>
  );
}

function CreateUserModal({
  teams,
  onClose,
  onSaved,
}: {
  teams: Team[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<UserRole>('user');
  const [teamId, setTeamId] = useState<string>(NO_TEAM);
  const [teamIds, setTeamIds] = useState<string[]>([]);
  const [teamRoles, setTeamRoles] = useState<Record<string, 'member' | 'reader'>>({});
  const [isActive, setIsActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body: AdminUserCreateRequest = {
      username: username.trim(),
      email: email.trim(),
      role,
      is_active: isActive,
      ...(password ? { password } : {}),
      ...(teamId !== NO_TEAM ? { team_id: teamId } : {}),
      team_ids: teamIds,
      team_roles: teamRoles,
    };
    try {
      await apiJson<AuthUser>('/api/v1/auth/admin/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      await onSaved();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Modal title={t('admin.users.create')} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label={t('common.username')}>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className={inputClass}
            required
            autoFocus
          />
        </Field>
        <Field label={t('common.email')}>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputClass}
            required
          />
        </Field>
        <Field label={t('common.password')} hint={t('admin.users.passwordHintCreate')}>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
            minLength={8}
            autoComplete="new-password"
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('admin.users.column.role')}>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as UserRole)}
              className={inputClass}
            >
              <option value="user">{t('admin.users.role.user')}</option>
              <option value="admin">{t('admin.users.role.admin')}</option>
            </select>
          </Field>
        </div>
        <TeamMembershipFields teams={teams} primary={teamId} selected={teamIds} roles={teamRoles} onChange={(primary, selected, roles) => { setTeamId(primary); setTeamIds(selected); setTeamRoles(roles); }} />
        <Toggle checked={isActive} onChange={setIsActive} label={t('admin.users.active')} />
        <ErrorNotice message={error} />
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" size="sm" disabled={busy}>
            {busy && <LoaderCircle className="h-4 w-4 animate-spin" />}
            {t('admin.users.create')}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function EditUserModal({
  user,
  teams,
  onClose,
  onSaved,
}: {
  user: AuthUser;
  teams: Team[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [email, setEmail] = useState(user.email);
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<UserRole>(user.role);
  const [teamId, setTeamId] = useState<string>(user.team_id === null ? NO_TEAM : String(user.team_id));
  const [teamIds, setTeamIds] = useState<string[]>(user.team_ids ?? (user.team_id ? [user.team_id] : []));
  const [teamRoles, setTeamRoles] = useState<Record<string, 'member' | 'reader'>>(
    Object.fromEntries((user.team_ids ?? (user.team_id ? [user.team_id] : [])).map((id) => [id, user.team_roles?.[id] ?? 'member'])),
  );
  const [isActive, setIsActive] = useState(user.is_active);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body: AdminUserUpdateRequest = {
      email: email.trim(),
      role,
      is_active: isActive,
      ...(password ? { password } : {}),
      ...(teamId === NO_TEAM ? { clear_team: true } : { team_id: teamId }),
      team_ids: teamIds,
      team_roles: teamRoles,
    };
    try {
      await apiJson<AuthUser>(`/api/v1/auth/admin/users/${user.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      await onSaved();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Modal title={t('admin.users.editAria', { username: user.username })} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label={t('common.email')}>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputClass}
            required
          />
        </Field>
        <Field label={t('admin.users.newPassword')} hint={t('admin.users.passwordHintEdit')}>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
            minLength={8}
            autoComplete="new-password"
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('admin.users.column.role')}>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as UserRole)}
              className={inputClass}
            >
              <option value="user">{t('admin.users.role.user')}</option>
              <option value="admin">{t('admin.users.role.admin')}</option>
            </select>
          </Field>
        </div>
        <TeamMembershipFields teams={teams} primary={teamId} selected={teamIds} roles={teamRoles} onChange={(primary, selected, roles) => { setTeamId(primary); setTeamIds(selected); setTeamRoles(roles); }} />
        <Toggle checked={isActive} onChange={setIsActive} label={t('admin.users.active')} />
        <ErrorNotice message={error} />
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" size="sm" disabled={busy}>
            {busy && <LoaderCircle className="h-4 w-4 animate-spin" />}
            {t('admin.users.saveChanges')}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ClaimOwnerlessCard({ users }: { users: AuthUser[] }) {
  const { t } = useI18n();
  const [ownerId, setOwnerId] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function claim() {
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const res = await apiJson<ClaimOwnerlessResponse>('/api/v1/auth/admin/jobs/claim-ownerless', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ owner_id: ownerId } satisfies ClaimOwnerlessRequest),
      });
      setResult(t('admin.users.jobsAssigned', { count: res.claimed }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SectionCard
      title={t('admin.users.claimTitle')}
      description={t('admin.users.claimDescription')}
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-full max-w-xs">
          <Field label={t('admin.users.assignTo')}>
            <select
              value={ownerId}
              onChange={(e) => setOwnerId(e.target.value)}
              className={inputClass}
            >
              <option value="">{t('admin.users.selectUser')}</option>
              {users.map((u) => (
                <option key={u.id} value={String(u.id)}>
                  {u.username}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Button variant="outline" onClick={claim} disabled={busy || ownerId === ''}>
          {busy ? (
            <LoaderCircle className="h-4 w-4 animate-spin" />
          ) : (
            <Briefcase className="h-4 w-4" />
          )}
          {t('admin.users.assignJobs')}
        </Button>
      </div>
      <div aria-live="polite">
        {result && <p className="mt-3 text-sm font-medium text-emerald-700">{result}</p>}
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
    </SectionCard>
  );
}
