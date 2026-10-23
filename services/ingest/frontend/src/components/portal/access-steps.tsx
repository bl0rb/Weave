'use client';

import { Fragment, useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { ApiError } from '@/lib/api';
import { loadDirectoryTeams, portalError, searchDirectoryUsers, type CollectionRole, type DirectoryTeam, type DirectoryUser, type GrantInput } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';

/**
 * Building blocks shared by the access dialog (access-dialog.tsx) and the guided
 * "new knowledge space" flow (new-knowledge-space.tsx): one access draft, the
 * step navigation (same look as the bot editor in bots-tab.tsx) and the
 * access, people, groups and review steps.
 */

export type AccessPerson = { id: string; username: string; team?: string | null; role: CollectionRole; isActive?: boolean; /** The creator of a new space: always owner, not editable. */ locked?: boolean };
export type AccessTeamRole = Exclude<CollectionRole, 'owner'>;
/** A team without a known member count (own teams from the portal config) has member_count -1. */
export type AccessTeam = DirectoryTeam;

export const PERSON_ROLES: CollectionRole[] = ['owner', 'member', 'reader'];
export const TEAM_ROLES: AccessTeamRole[] = ['member', 'reader'];
const SEARCH_DEBOUNCE_MS = 250;

export function useAccessDraft({ initialMode, initialPersons, initialTeamRoles, fallbackTeams }: {
  initialMode: 'public' | 'restricted';
  initialPersons: AccessPerson[];
  initialTeamRoles: Record<string, AccessTeamRole>;
  /** Shown when the directory is closed to the caller (403: neither admin nor owner of any space yet). */
  fallbackTeams?: AccessTeam[];
}) {
  const { t, locale } = useI18n();
  const [mode, setMode] = useState(initialMode);
  const [persons, setPersons] = useState<AccessPerson[]>(initialPersons);
  const [teamRoles, setTeamRoles] = useState<Record<string, AccessTeamRole>>(initialTeamRoles);
  const [directoryTeams, setDirectoryTeams] = useState<AccessTeam[] | null>(null);
  const [directoryClosed, setDirectoryClosed] = useState(false);
  const [teamsError, setTeamsError] = useState('');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DirectoryUser[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    loadDirectoryTeams(controller.signal).then(data => {
      setDirectoryTeams(data.items);
      // Drop grants to teams that no longer exist: they have no row to
      // untick, and saving would 422 on them.
      const known = new Set(data.items.map(team => team.id));
      setTeamRoles(current => Object.fromEntries(Object.entries(current).filter(([id]) => known.has(id))));
    }).catch(err => {
      if (controller.signal.aborted) return;
      if (err instanceof ApiError && err.status === 403) setDirectoryClosed(true);
      else setTeamsError(portalError(err, locale));
    });
    return () => controller.abort();
  }, [locale]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed || directoryClosed) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      searchDirectoryUsers(trimmed, controller.signal)
        .then(data => setResults(data.items))
        .catch(() => { /* a failed search stays silent -- the field itself isn't required to save */ });
    }, SEARCH_DEBOUNCE_MS);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, directoryClosed]);

  const teams = useMemo(() => directoryTeams ?? (directoryClosed ? fallbackTeams ?? [] : []), [directoryTeams, directoryClosed, fallbackTeams]);
  // Cleared as soon as the query itself is empty, so a stale `results` batch from a just-cleared search never flashes back in.
  const visibleResults = useMemo(() => query.trim() ? results.filter(user => !persons.some(person => person.id === user.id)) : [], [query, results, persons]);
  const teamNames = useMemo(() => new Set(teams.filter(team => teamRoles[team.id]).map(team => team.name)), [teams, teamRoles]);
  const selectedTeams = teams.filter(team => teamRoles[team.id]);
  const hasOwner = persons.some(person => person.role === 'owner');

  // Reach: every granted team's member_count, plus granted persons whose
  // own team isn't already granted (avoids double-counting them). Unknown
  // when a granted team has no member count (directory closed).
  const reach = selectedTeams.some(team => team.member_count < 0) ? null
    : selectedTeams.reduce((sum, team) => sum + team.member_count, 0)
      + persons.filter(person => !(person.team && teamNames.has(person.team))).length;
  const summary = mode === 'public'
    ? t('portal.access.dialog.summaryPublic')
    : reach === null
      ? t('portal.access.dialog.summaryRestricted')
      : reach
        ? t('portal.access.dialog.summaryReach', { count: reach })
        : t('portal.access.dialog.summaryNoAccess');

  function addPerson(user: DirectoryUser) {
    setPersons(current => current.some(existing => existing.id === user.id) ? current : [...current, { id: user.id, username: user.username, team: user.team, role: 'reader' }]);
    setQuery('');
    setResults([]);
  }
  function onSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (visibleResults[0]) addPerson(visibleResults[0]);
    }
  }

  return {
    mode, setMode, persons, teamRoles, teams, selectedTeams, teamNames, teamsError, directoryClosed,
    query, setQuery, visibleResults, onSearchKeyDown, addPerson, hasOwner, summary,
    setPersonRole: (id: string, role: CollectionRole) => setPersons(current => current.map(person => person.id === id ? { ...person, role } : person)),
    removePerson: (id: string) => setPersons(current => current.filter(person => person.id !== id)),
    toggleTeam: (id: string, checked: boolean) => setTeamRoles(current => {
      const next = { ...current };
      if (checked) next[id] = current[id] ?? 'member'; else delete next[id];
      return next;
    }),
    setTeamRole: (id: string, role: AccessTeamRole) => setTeamRoles(current => ({ ...current, [id]: role })),
    /** Grants for the API; locked persons (the creator) are implicit owners and left out. */
    grants: (): GrantInput[] => [
      ...persons.filter(person => !person.locked).map(person => ({ user_id: person.id, role: person.role })),
      ...Object.entries(teamRoles).map(([team_id, role]) => ({ team_id, role })),
    ],
  };
}

export type AccessDraft = ReturnType<typeof useAccessDraft>;

/** Numbered step pills, identical to the bot editor's; steps are freely reachable. */
export function StepNav<S extends string>({ steps, current, onSelect, label, stepLabel, counts, problems }: {
  steps: readonly S[];
  current: S;
  onSelect: (step: S) => void;
  label: string;
  stepLabel: (step: S) => string;
  counts?: Partial<Record<S, number>>;
  problems?: Partial<Record<S, string[]>>;
}) {
  const { t } = useI18n();
  return <nav aria-label={label} className="mb-5">
    <ol className="flex flex-wrap gap-2">
      {steps.map((item, index) => <li key={item}>
        <button type="button" onClick={() => onSelect(item)} aria-current={item === current ? 'step' : undefined}
          className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm ${item === current ? 'border-[var(--accent)] bg-[var(--accent-soft)] font-semibold text-[var(--accent-ink)]' : 'border-[var(--line-2)] text-[var(--ink-2)] hover:bg-[var(--hover)]'}`}>
          <span className="text-xs tabular-nums">{index + 1}</span>{stepLabel(item)}
          {counts?.[item] !== undefined && <span className="text-xs text-[var(--muted)]">({counts[item]})</span>}
          {problems?.[item]?.length ? <span className="h-2 w-2 rounded-full bg-[var(--warn)]" role="img" aria-label={t('portal.access.dialog.stepHasProblems')} /> : null}
        </button>
      </li>)}
    </ol>
  </nav>;
}

export function AccessModeStep({ draft, disabled, intro, children }: { draft: AccessDraft; disabled?: boolean; intro?: ReactNode; children?: ReactNode }) {
  const { t } = useI18n();
  return <section aria-label={t('portal.access.dialog.step.mode')}>
    {intro}
    <fieldset className="portal-access-mode mt-4">
      <legend className="sr-only">{t('portal.access.dialog.modeLegend')}</legend>
      <label className="portal-access-mode-option">
        <input type="radio" name="access-mode" value="restricted" checked={draft.mode === 'restricted'} onChange={() => draft.setMode('restricted')} disabled={disabled} autoFocus />
        <span><strong>{t('portal.access.dialog.modeRestricted.title')}</strong><small>{t('portal.access.dialog.modeRestricted.hint')}</small></span>
      </label>
      <label className="portal-access-mode-option">
        <input type="radio" name="access-mode" value="public" checked={draft.mode === 'public'} onChange={() => draft.setMode('public')} disabled={disabled} />
        <span><strong>{t('portal.access.dialog.modePublic.title')}</strong><small>{t('portal.access.dialog.modePublic.hint')}</small></span>
      </label>
    </fieldset>
    {children}
    <p className="portal-field-hint">{t('portal.access.dialog.footer')}</p>
  </section>;
}

export function AccessPersonsStep({ draft, disabled }: { draft: AccessDraft; disabled?: boolean }) {
  const { t } = useI18n();
  const roleLabel = (role: CollectionRole) => t(`portal.access.role.${role}`);
  return <section aria-label={t('portal.access.dialog.step.persons')}>
    {draft.directoryClosed
      ? <p className="portal-field-hint">{t('portal.access.dialog.directoryClosed')}</p>
      : <>
        <div className="portal-access-picker">
          <div className="portal-access-search">
            <input id="access-search" type="search" autoComplete="off" placeholder={t('portal.access.dialog.searchPlaceholder')} value={draft.query} disabled={disabled}
              onChange={event => draft.setQuery(event.target.value)} onKeyDown={draft.onSearchKeyDown}
              aria-label={t('portal.access.dialog.personsLabel')} aria-describedby="access-hint" aria-controls="access-results" autoFocus />
          </div>
          <ul className="portal-access-results" id="access-results" aria-live="polite" aria-label={t('portal.access.dialog.resultsAriaLabel')}>
            {draft.visibleResults.map(user => <li key={user.id}>
              <button type="button" onClick={() => draft.addPerson(user)}>
                <span>{user.username}<small>{user.team ? `${t('portal.access.team', { team: user.team })}${draft.teamNames.has(user.team) ? ` · ${t('portal.access.dialog.hasTeamAccessSuffix')}` : ''}` : ''}</small></span>
              </button>
            </li>)}
            {draft.query.trim() && !draft.visibleResults.length && <li className="portal-access-no-hit">{t('portal.access.dialog.noResults')}</li>}
          </ul>
        </div>
        <p className="portal-field-hint" id="access-hint">{t('portal.access.dialog.searchHint')}</p>
      </>}
    {!draft.hasOwner && <p className="portal-field-hint portal-field-error" role="alert">{t('portal.access.dialog.ownerRequired')}</p>}
    <ul className="portal-access-grants mt-3" aria-label={t('portal.access.dialog.personsLabel')}>
      {draft.persons.map(person => <li className="portal-access-grant-row" key={person.id}>
        <span className="portal-access-grant-name">{person.username}{person.team && <small>{person.team}</small>}{person.isActive === false && <small className="portal-field-error">{t('portal.access.inactive')}</small>}{person.locked && <small>{t('portal.access.dialog.creatorHint')}</small>}</span>
        {person.locked
          ? <span className="text-[13px] font-semibold text-[var(--ink-2)]">{roleLabel(person.role)}</span>
          : <div className="portal-access-grant-actions">
            <select className="portal-role-select" value={person.role} disabled={disabled} onChange={event => draft.setPersonRole(person.id, event.target.value as CollectionRole)}
              aria-label={t('portal.access.dialog.roleAria', { name: person.username })}>
              {PERSON_ROLES.map(role => <option key={role} value={role}>{roleLabel(role)}</option>)}
            </select>
            <button type="button" className="portal-access-remove" disabled={disabled} onClick={() => draft.removePerson(person.id)} aria-label={t('portal.access.dialog.removePerson', { name: person.username })}><X size={14} aria-hidden="true" /></button>
          </div>}
      </li>)}
    </ul>
  </section>;
}

export function AccessTeamsStep({ draft, disabled }: { draft: AccessDraft; disabled?: boolean }) {
  const { t } = useI18n();
  const [filter, setFilter] = useState('');
  const roleLabel = (role: CollectionRole) => t(`portal.access.role.${role}`);
  // Selected groups first, then the rest; the filter only narrows the list, never the selection.
  const needle = filter.trim().toLowerCase();
  const visibleTeams = draft.teams
    .filter(team => !needle || team.name.toLowerCase().includes(needle))
    .sort((a, b) => Number(Boolean(draft.teamRoles[b.id])) - Number(Boolean(draft.teamRoles[a.id])) || a.name.localeCompare(b.name));
  return <section aria-label={t('portal.access.dialog.step.teams')}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <input type="search" autoComplete="off" className="ui-control max-w-sm flex-1" placeholder={t('portal.access.dialog.teamFilter')} aria-label={t('portal.access.dialog.teamFilterAria')}
        value={filter} disabled={disabled} onChange={event => setFilter(event.target.value)} />
      <span className="text-[13px] text-[var(--muted)]" aria-live="polite">{t('portal.access.dialog.selectedCount', { count: draft.selectedTeams.length })}</span>
    </div>
    {draft.teamsError && <p className="portal-field-hint">{draft.teamsError}</p>}
    {draft.directoryClosed && <p className="portal-field-hint">{t('portal.access.dialog.ownTeamsOnly')}</p>}
    <ul className="portal-access-grants mt-3 max-h-[22rem] overflow-y-auto pr-1" aria-label={t('portal.access.dialog.teamsLabel')}>
      {visibleTeams.map(team => <li className="portal-access-grant-row" key={team.id}>
        <label className="portal-access-grant-name">
          <input type="checkbox" checked={Boolean(draft.teamRoles[team.id])} disabled={disabled} onChange={event => draft.toggleTeam(team.id, event.target.checked)} />
          <span>{team.name}{team.member_count >= 0 && <small>{t('portal.access.dialog.memberCount', { count: team.member_count })}</small>}</span>
        </label>
        {draft.teamRoles[team.id] && <select className="portal-role-select" value={draft.teamRoles[team.id]} disabled={disabled}
          onChange={event => draft.setTeamRole(team.id, event.target.value as AccessTeamRole)}
          aria-label={t('portal.access.dialog.roleAria', { name: team.name })}>
          {TEAM_ROLES.map(role => <option key={role} value={role}>{roleLabel(role)}</option>)}
        </select>}
      </li>)}
    </ul>
    {draft.teams.length > 0 && !visibleTeams.length && <p className="portal-access-no-hit">{t('portal.access.dialog.noTeams')}</p>}
    <p className="portal-field-hint">{t('portal.access.dialog.teamRoleHint')}</p>
  </section>;
}

/** dt/dd rows for a review step's <dl>: access mode, persons per role, groups. */
export function AccessReviewRows({ draft }: { draft: AccessDraft }) {
  const { t } = useI18n();
  const roleLabel = (role: CollectionRole) => t(`portal.access.role.${role}`);
  const names = (role: CollectionRole) => draft.persons.filter(person => person.role === role).map(person => person.username).join(', ') || '—';
  const dt = 'font-medium text-[var(--ink-2)]';
  return <>
    <dt className={dt}>{t('portal.access.dialog.review.visibility')}</dt><dd>{draft.mode === 'public' ? t('portal.access.dialog.modePublic.title') : t('portal.access.dialog.modeRestricted.title')}</dd>
    {PERSON_ROLES.map(role => <Fragment key={role}><dt className={dt}>{roleLabel(role)}</dt><dd>{names(role)}</dd></Fragment>)}
    <dt className={dt}>{t('portal.access.dialog.review.teams')}</dt><dd>{draft.selectedTeams.map(team => `${team.name} (${roleLabel(draft.teamRoles[team.id])})`).join(', ') || '—'}</dd>
  </>;
}

/** The review step's problem list (each links back to its step) or the ready line. */
export function ReviewProblems<S extends string>({ problems, stepLabel, onSelect }: { problems: Partial<Record<S, string[]>>; stepLabel: (step: S) => string; onSelect: (step: S) => void }) {
  const { t } = useI18n();
  const entries = (Object.entries(problems) as [S, string[] | undefined][]).flatMap(([item, list]) => (list ?? []).map(problem => ({ item, problem })));
  return entries.length
    ? <ul className="list-disc space-y-1 rounded-xl border border-[var(--line-2)] bg-[var(--warn-bg)] px-4 py-3 pl-8 text-[14px] text-[var(--warn)]">
      {entries.map(({ item, problem }) => <li key={`${item}-${problem}`}><button type="button" className="underline" onClick={() => onSelect(item)}>{stepLabel(item)}</button>: {problem}</li>)}
    </ul>
    : <p className="text-[14px] text-[var(--accent-ink)]">{t('portal.access.dialog.review.ready')}</p>;
}
