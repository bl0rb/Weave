'use client';

import { useEffect, useMemo, useState, type KeyboardEvent } from 'react';
import { X } from 'lucide-react';
import { searchDirectoryUsers, type DirectoryUser, type TeamRef } from '@/lib/portal';
import type { BotGrant, BotRole } from '@/lib/bots';
import { useI18n } from '@/i18n/provider';

const SEARCH_DEBOUNCE_MS = 250;

/**
 * Who may use a bot (ADR 0008): everyone, or the granted persons and teams;
 * owners maintain it. Shared by the admin bot editor (which also assigns
 * owners) and the owners' own "Meine Bots" page (where owners are fixed).
 */
export function BotSharing({ isPublic, onPublicChange, grants, onGrantsChange, teams, canEditOwners, disabled }: {
  isPublic: boolean;
  onPublicChange: (value: boolean) => void;
  grants: BotGrant[];
  onGrantsChange: (grants: BotGrant[]) => void;
  teams: TeamRef[];
  /** Administrators assign owners; owners themselves only manage users. */
  canEditOwners: boolean;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DirectoryUser[]>([]);
  const persons = grants.filter(grant => grant.user_id);
  const teamIds = new Set(grants.filter(grant => grant.team_id).map(grant => grant.team_id));

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

  const visibleResults = useMemo(
    () => query.trim() ? results.filter(user => !persons.some(person => person.user_id === user.id)) : [],
    [query, results, persons],
  );

  function addPerson(user: DirectoryUser) {
    onGrantsChange([...grants, { user_id: user.id, team_id: null, role: 'user', name: user.username, team: user.team }]);
    setQuery('');
    setResults([]);
  }
  function setRole(userId: string, role: BotRole) {
    onGrantsChange(grants.map(grant => grant.user_id === userId ? { ...grant, role } : grant));
  }
  function removePerson(userId: string) {
    onGrantsChange(grants.filter(grant => grant.user_id !== userId));
  }
  function toggleTeam(team: TeamRef, checked: boolean) {
    onGrantsChange(checked
      ? [...grants, { user_id: null, team_id: team.id, role: 'user', name: team.name }]
      : grants.filter(grant => grant.team_id !== team.id));
  }
  function onSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (visibleResults[0]) addPerson(visibleResults[0]);
    }
  }
  const roleLabel = (role: BotRole) => t(`portal.bots.role.${role}`);

  return <fieldset className="space-y-2" disabled={disabled}>
    <legend className="text-sm font-medium text-[var(--ink-2)]">{t('portal.bots.sharing.title')}</legend>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={isPublic} onChange={event => onPublicChange(event.target.checked)} />{t('portal.bots.sharing.public')}</label>
    <p className="portal-field-hint">{isPublic ? t('portal.bots.sharing.publicHint') : t('portal.bots.sharing.restrictedHint')}</p>

    <p className="portal-access-label">{t('portal.access.dialog.personsLabel')}</p>
    <ul className="portal-access-grants" aria-label={t('portal.access.dialog.personsLabel')}>
      {persons.map(person => {
        const locked = person.role === 'owner' && !canEditOwners;
        return <li className="portal-access-grant-row" key={person.user_id}>
          <span>{person.name}{person.team && <small>{person.team}</small>}</span>
          {canEditOwners
            ? <select className="portal-role-select" value={person.role} onChange={event => setRole(person.user_id as string, event.target.value as BotRole)} aria-label={t('portal.access.dialog.roleAria', { name: person.name })}>
              <option value="owner">{roleLabel('owner')}</option>
              <option value="user">{roleLabel('user')}</option>
            </select>
            : <small>{roleLabel(person.role)}</small>}
          {!locked && <button type="button" className="portal-access-remove" onClick={() => removePerson(person.user_id as string)} aria-label={t('portal.access.dialog.removePerson', { name: person.name })}><X size={14} aria-hidden="true" /></button>}
        </li>;
      })}
    </ul>
    <div className="portal-access-picker">
      <div className="portal-access-search">
        <input type="search" autoComplete="off" placeholder={t('portal.access.dialog.searchPlaceholder')} value={query}
          onChange={event => setQuery(event.target.value)} onKeyDown={onSearchKeyDown} aria-label={t('portal.bots.sharing.searchAria')} />
      </div>
      <ul className="portal-access-results" aria-live="polite" aria-label={t('portal.access.dialog.resultsAriaLabel')}>
        {visibleResults.map(user => <li key={user.id}><button type="button" onClick={() => addPerson(user)}><span>{user.username}<small>{user.team ?? ''}</small></span></button></li>)}
        {query.trim() && !visibleResults.length && <li className="portal-access-no-hit">{t('portal.access.dialog.noResults')}</li>}
      </ul>
    </div>
    <p className="portal-field-hint">{t('portal.bots.sharing.searchHint')}</p>

    {teams.length > 0 && <>
      <p className="portal-access-label">{t('portal.access.dialog.teamsLabel')}</p>
      <ul className="portal-access-grants">
        {teams.map(team => <li className="portal-access-grant-row" key={team.id}>
          <label><input type="checkbox" checked={teamIds.has(team.id)} onChange={event => toggleTeam(team, event.target.checked)} /><span>{team.name}</span></label>
        </li>)}
      </ul>
    </>}
    <p className="portal-field-hint">{t('portal.bots.sharing.answersHint')}</p>
  </fieldset>;
}
