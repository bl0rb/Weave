'use client';

import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ErrorNotice, Modal } from '@/components/admin/admin-shared';
import { loadDirectoryTeams, portalError, searchDirectoryUsers, updateCollectionAccess, type CollectionGrant, type CollectionRole, type DirectoryTeam, type DirectoryUser, type GrantInput, type KnowledgeSpace } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';

/** Minimal shape the dialog needs from a collection -- both the portal cards'
 * KnowledgeSpace and the admin tab's ManagedCollection satisfy this. */
export type AccessDialogCollection = {
  collection_id: string;
  name: string;
  visibility: 'public' | 'restricted';
  grants: CollectionGrant[];
};

type Person = { id: string; username: string; team?: string | null; role: CollectionRole; isActive?: boolean };
type TeamRole = Exclude<CollectionRole, 'owner'>;

const PERSON_ROLES: CollectionRole[] = ['owner', 'member', 'reader'];
const TEAM_ROLES: TeamRole[] = ['member', 'reader'];
const SEARCH_DEBOUNCE_MS = 250;

/**
 * Knowledge-space access dialog (ADR 0008): who may read the space, and
 * which persons and teams are owners, members or readers. Reuses the app's
 * existing Modal (admin-shared.tsx), already used for the other portal
 * editors (KnowledgeSpaceEditor, ConfirmDialog) and already dark-mode-safe
 * via globals.css's html.dark overrides.
 */
export function AccessDialog({ collection, onClose, onSaved }: {
  collection: AccessDialogCollection;
  onClose: () => void;
  /** Called once PATCH succeeds, with the backend's fresh collection -- the caller merges it into its own state instead of reloading. */
  onSaved: (updated: KnowledgeSpace) => void;
}) {
  const { t, locale } = useI18n();
  const [mode, setMode] = useState<'public' | 'restricted'>(collection.visibility);
  const [persons, setPersons] = useState<Person[]>(() => collection.grants
    .filter(grant => grant.user_id)
    .map(grant => ({ id: grant.user_id as string, username: grant.name, team: grant.team, role: grant.role, isActive: grant.is_active })));
  const [teamRoles, setTeamRoles] = useState<Record<string, TeamRole>>(() => Object.fromEntries(collection.grants
    .filter(grant => grant.team_id)
    .map(grant => [grant.team_id as string, grant.role === 'reader' ? 'reader' : 'member'])));
  const [teams, setTeams] = useState<DirectoryTeam[]>([]);
  const [teamsError, setTeamsError] = useState('');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DirectoryUser[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Modal itself has no focus trap or return -- restore focus to whatever
  // opened the dialog (the "Ändern"/"Zugriff ändern" button) once it closes.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    return () => opener?.focus?.();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadDirectoryTeams(controller.signal).then(data => {
      setTeams(data.items);
      // Drop grants to teams that no longer exist: they have no row to
      // untick, and PATCH would 422 on them.
      const known = new Set(data.items.map(team => team.id));
      setTeamRoles(current => Object.fromEntries(Object.entries(current).filter(([id]) => known.has(id))));
    }).catch(err => setTeamsError(portalError(err, locale)));
    return () => controller.abort();
  }, [locale]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      searchDirectoryUsers(trimmed, controller.signal)
        .then(data => setResults(data.items))
        .catch(() => { /* a failed search stays silent -- the field itself isn't required to save */ });
    }, SEARCH_DEBOUNCE_MS);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query]);

  // Cleared as soon as the query itself is empty, so a stale `results` batch from a just-cleared search never flashes back in.
  const visibleResults = useMemo(() => query.trim() ? results.filter(user => !persons.some(person => person.id === user.id)) : [], [query, results, persons]);
  const teamNames = useMemo(() => new Set(teams.filter(team => teamRoles[team.id]).map(team => team.name)), [teams, teamRoles]);
  const hasOwner = persons.some(person => person.role === 'owner');

  function addPerson(user: DirectoryUser) {
    setPersons(current => current.some(existing => existing.id === user.id) ? current : [...current, { id: user.id, username: user.username, team: user.team, role: 'reader' }]);
    setQuery('');
    setResults([]);
  }
  function setPersonRole(id: string, role: CollectionRole) {
    setPersons(current => current.map(person => person.id === id ? { ...person, role } : person));
  }
  function removePerson(id: string) {
    setPersons(current => current.filter(person => person.id !== id));
  }
  function toggleTeam(id: string, checked: boolean) {
    setTeamRoles(current => {
      const next = { ...current };
      if (checked) next[id] = 'member'; else delete next[id];
      return next;
    });
  }
  function onSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (visibleResults[0]) addPerson(visibleResults[0]);
    }
  }

  // Reach: every granted team's member_count, plus granted persons whose
  // own team isn't already granted (avoids double-counting them).
  const reach = teams.filter(team => teamRoles[team.id]).reduce((sum, team) => sum + team.member_count, 0)
    + persons.filter(person => !(person.team && teamNames.has(person.team))).length;
  const summary = mode === 'public'
    ? t('portal.access.dialog.summaryPublic')
    : reach
      ? t('portal.access.dialog.summaryReach', { count: reach })
      : t('portal.access.dialog.summaryNoAccess');

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!hasOwner) return;
    setSaving(true);
    setError('');
    const grants: GrantInput[] = [
      ...persons.map(person => ({ user_id: person.id, role: person.role })),
      ...Object.entries(teamRoles).map(([team_id, role]) => ({ team_id, role })),
    ];
    try {
      onSaved(await updateCollectionAccess(collection.collection_id, { visibility: mode, grants }));
    } catch (err) {
      setError(portalError(err, locale));
      setSaving(false);
    }
  }

  const roleLabel = (role: CollectionRole) => t(`portal.access.role.${role}`);

  return <Modal title={collection.name} onClose={onClose}>
    <p className="portal-eyebrow">{t('portal.access.dialog.eyebrow')}</p>
    <ErrorNotice message={error || null} />
    <form className="portal-access-form" onSubmit={save} noValidate>
      <p className="text-sm text-[var(--ink-2)]">{t('portal.access.dialog.intro')}</p>
      <fieldset className="portal-access-mode">
        <legend className="sr-only">{t('portal.access.dialog.modeLegend')}</legend>
        <label className="portal-access-mode-option">
          <input type="radio" name="access-mode" value="restricted" checked={mode === 'restricted'} onChange={() => setMode('restricted')} disabled={saving} autoFocus />
          <span><strong>{t('portal.access.dialog.modeRestricted.title')}</strong><small>{t('portal.access.dialog.modeRestricted.hint')}</small></span>
        </label>
        <label className="portal-access-mode-option">
          <input type="radio" name="access-mode" value="public" checked={mode === 'public'} onChange={() => setMode('public')} disabled={saving} />
          <span><strong>{t('portal.access.dialog.modePublic.title')}</strong><small>{t('portal.access.dialog.modePublic.hint')}</small></span>
        </label>
      </fieldset>

      <label className="portal-access-label" htmlFor="access-search">{t('portal.access.dialog.personsLabel')}</label>
      <ul className="portal-access-grants" aria-label={t('portal.access.dialog.personsLabel')}>
        {persons.map(person => <li className="portal-access-grant-row" key={person.id}>
          <span className="portal-access-grant-name">{person.username}{person.team && <small>{person.team}</small>}{person.isActive === false && <small className="portal-field-error">{t('portal.access.inactive')}</small>}</span>
          <div className="portal-access-grant-actions">
            <select className="portal-role-select" value={person.role} disabled={saving} onChange={event => setPersonRole(person.id, event.target.value as CollectionRole)}
              aria-label={t('portal.access.dialog.roleAria', { name: person.username })}>
              {PERSON_ROLES.map(role => <option key={role} value={role}>{roleLabel(role)}</option>)}
            </select>
            <button type="button" className="portal-access-remove" disabled={saving} onClick={() => removePerson(person.id)} aria-label={t('portal.access.dialog.removePerson', { name: person.username })}><X size={14} aria-hidden="true" /></button>
          </div>
        </li>)}
      </ul>
      <div className="portal-access-picker">
        <div className="portal-access-search">
          <input id="access-search" type="search" autoComplete="off" placeholder={t('portal.access.dialog.searchPlaceholder')} value={query} disabled={saving}
            onChange={event => setQuery(event.target.value)} onKeyDown={onSearchKeyDown}
            aria-describedby="access-hint" aria-controls="access-results" />
        </div>
        <ul className="portal-access-results" id="access-results" aria-live="polite" aria-label={t('portal.access.dialog.resultsAriaLabel')}>
          {visibleResults.map(user => <li key={user.id}>
            <button type="button" onClick={() => addPerson(user)}>
              <span>{user.username}<small>{user.team ? `${t('portal.access.team', { team: user.team })}${teamNames.has(user.team) ? ` · ${t('portal.access.dialog.hasTeamAccessSuffix')}` : ''}` : ''}</small></span>
            </button>
          </li>)}
          {query.trim() && !visibleResults.length && <li className="portal-access-no-hit">{t('portal.access.dialog.noResults')}</li>}
        </ul>
      </div>
      <p className="portal-field-hint" id="access-hint">{t('portal.access.dialog.searchHint')}</p>
      {!hasOwner && <p className="portal-field-hint portal-field-error" role="alert">{t('portal.access.dialog.ownerRequired')}</p>}

      <p className="portal-access-label" id="access-teams-label">{t('portal.access.dialog.teamsLabel')}</p>
      {teamsError && <p className="portal-field-hint">{teamsError}</p>}
      <ul className="portal-access-grants" aria-labelledby="access-teams-label">
        {teams.map(team => <li className="portal-access-grant-row" key={team.id}>
          <label className="portal-access-grant-name">
            <input type="checkbox" checked={Boolean(teamRoles[team.id])} disabled={saving} onChange={event => toggleTeam(team.id, event.target.checked)} />
            <span>{team.name}<small>{t('portal.access.dialog.memberCount', { count: team.member_count })}</small></span>
          </label>
          {teamRoles[team.id] && <select className="portal-role-select" value={teamRoles[team.id]} disabled={saving}
            onChange={event => setTeamRoles(current => ({ ...current, [team.id]: event.target.value as TeamRole }))}
            aria-label={t('portal.access.dialog.roleAria', { name: team.name })}>
            {TEAM_ROLES.map(role => <option key={role} value={role}>{roleLabel(role)}</option>)}
          </select>}
        </li>)}
      </ul>
      <p className="portal-field-hint">{t('portal.access.dialog.teamRoleHint')}</p>

      <p className="portal-access-summary" aria-live="polite"><strong>{summary}</strong></p>
      <div className="portal-form-actions">
        <Button type="submit" disabled={saving || !hasOwner}>{saving ? t('portal.access.dialog.saving') : t('portal.access.dialog.save')}</Button>
        <Button type="button" variant="ghost" disabled={saving} onClick={onClose}>{t('common.cancel')}</Button>
      </div>
      <p className="portal-field-hint">{t('portal.access.dialog.footer')}</p>
    </form>
  </Modal>;
}
