'use client';

import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ErrorNotice, Modal } from '@/components/admin/admin-shared';
import { loadDirectoryTeams, portalError, searchDirectoryUsers, updateCollectionAccess, type DirectoryTeam, type DirectoryUser, type KnowledgeSpace } from '@/lib/portal';
import { useI18n } from '@/i18n/provider';

/** Minimal shape the dialog needs from a collection -- both the portal cards'
 * KnowledgeSpace and the admin tab's ManagedCollection satisfy this. */
export type AccessDialogCollection = {
  collection_id: string;
  name: string;
  read_teams: string[];
  visibility?: 'public' | 'restricted';
  read_users?: string[];
  read_user_details?: { id: string; username: string; display_name?: string | null; team?: string | null }[];
};

type Person = { id: string; username: string; display_name?: string | null; team?: string | null };

const personLabel = (person: Person) => person.display_name?.trim() || person.username;
const SEARCH_DEBOUNCE_MS = 250;

/** Same public/restricted inference accessSummary() uses for a collection that predates the `visibility` field. */
function initialMode(collection: AccessDialogCollection): 'public' | 'restricted' {
  if (collection.visibility) return collection.visibility;
  return collection.read_teams.length === 0 ? 'public' : 'restricted';
}

/**
 * Knowledge-space access dialog -- who may use a collection's documents in
 * chat. Reuses the app's existing Modal (admin-shared.tsx), already used
 * for the other portal editors (KnowledgeSpaceEditor, ConfirmDialog) and
 * already dark-mode-safe via globals.css's html.dark overrides.
 */
export function AccessDialog({ collection, onClose, onSaved }: {
  collection: AccessDialogCollection;
  onClose: () => void;
  /** Called once PATCH succeeds, with the backend's fresh collection -- the caller merges it into its own state instead of reloading. */
  onSaved: (updated: KnowledgeSpace) => void;
}) {
  const { t, locale } = useI18n();
  const [mode, setMode] = useState<'public' | 'restricted'>(() => initialMode(collection));
  const [selectedTeams, setSelectedTeams] = useState<string[]>(collection.read_teams);
  const [persons, setPersons] = useState<Person[]>(collection.read_user_details ?? []);
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
      // Drop grants to teams that no longer exist (deleted, or a typo from
      // create): they have no chip to untick, and PATCH would 422 on them.
      const known = new Set(data.items.map(team => team.name));
      setSelectedTeams(current => current.filter(name => known.has(name)));
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

  function addPerson(person: Person) {
    setPersons(current => current.some(existing => existing.id === person.id) ? current : [...current, person]);
    setQuery('');
    setResults([]);
  }
  function removePerson(id: string) {
    setPersons(current => current.filter(person => person.id !== id));
  }
  function toggleTeam(name: string, checked: boolean) {
    setSelectedTeams(current => checked ? [...current, name] : current.filter(team => team !== name));
  }
  function onSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (visibleResults[0]) addPerson(visibleResults[0]);
    }
  }

  // Reach: every selected team's member_count, plus selected persons whose
  // own team isn't already selected (avoids double-counting them).
  const reach = mode === 'public' ? null
    : teams.filter(team => selectedTeams.includes(team.name)).reduce((sum, team) => sum + team.member_count, 0)
      + persons.filter(person => !(person.team && selectedTeams.includes(person.team))).length;
  const detail = [
    selectedTeams.map(team => t('portal.access.team', { team })).join(', '),
    persons.length === 1 ? personLabel(persons[0]) : persons.length ? t('portal.access.dialog.personsCount', { count: persons.length }) : '',
  ].filter(Boolean).join(' + ');
  const summary = mode === 'public'
    ? t('portal.access.dialog.summaryPublic')
    : reach
      ? `${t('portal.access.dialog.summaryReach', { count: reach })} · ${detail}`
      : t('portal.access.dialog.summaryNoAccess');

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      const updated = await updateCollectionAccess(collection.collection_id, {
        visibility: mode,
        read_teams: mode === 'public' ? [] : selectedTeams,
        read_users: mode === 'public' ? [] : persons.map(person => person.id),
      });
      onSaved(updated);
    } catch (err) {
      setError(portalError(err, locale));
      setSaving(false);
    }
  }

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
      {mode === 'restricted' && <>
        <p className="portal-access-label" id="access-teams-label">{t('portal.access.dialog.teamsLabel')}</p>
        {teamsError && <p className="portal-field-hint">{teamsError}</p>}
        <div className="portal-access-team-chips" role="group" aria-labelledby="access-teams-label">
          {teams.map(team => <label className="portal-access-team-chip" key={team.name}>
            <input type="checkbox" checked={selectedTeams.includes(team.name)} disabled={saving} onChange={event => toggleTeam(team.name, event.target.checked)} />
            <span>{team.name}<small>{team.member_count}</small></span>
          </label>)}
        </div>
        <label className="portal-access-label" htmlFor="access-search">{t('portal.access.dialog.personsLabel')}</label>
        <div className="portal-access-picker">
          <div className="portal-access-person-chips">
            {persons.map(person => <span className="portal-access-person-chip" key={person.id}>
              {personLabel(person)}{person.team && <small>{person.team}</small>}
              <button type="button" disabled={saving} onClick={() => removePerson(person.id)} aria-label={t('portal.access.dialog.removePerson', { name: personLabel(person) })}><X size={14} aria-hidden="true" /></button>
            </span>)}
          </div>
          <div className="portal-access-search">
            <input id="access-search" type="search" autoComplete="off" placeholder={t('portal.access.dialog.searchPlaceholder')} value={query} disabled={saving}
              onChange={event => setQuery(event.target.value)} onKeyDown={onSearchKeyDown}
              aria-describedby="access-hint" aria-controls="access-results" />
          </div>
          <ul className="portal-access-results" id="access-results" aria-live="polite" aria-label={t('portal.access.dialog.resultsAriaLabel')}>
            {visibleResults.map(user => <li key={user.id}>
              <button type="button" onClick={() => addPerson(user)}>
                <span>{personLabel(user)}<small>{user.team ? `${t('portal.access.team', { team: user.team })}${selectedTeams.includes(user.team) ? ` · ${t('portal.access.dialog.hasTeamAccessSuffix')}` : ''}` : ''}</small></span>
              </button>
            </li>)}
            {query.trim() && !visibleResults.length && <li className="portal-access-no-hit">{t('portal.access.dialog.noResults')}</li>}
          </ul>
        </div>
        <p className="portal-field-hint" id="access-hint">{t('portal.access.dialog.searchHint')}</p>
      </>}
      <p className="portal-access-summary" aria-live="polite"><strong>{summary}</strong></p>
      <div className="portal-form-actions">
        <Button type="submit" disabled={saving}>{saving ? t('portal.access.dialog.saving') : t('portal.access.dialog.save')}</Button>
        <Button type="button" variant="ghost" disabled={saving} onClick={onClose}>{t('common.cancel')}</Button>
      </div>
      <p className="portal-field-hint">{t('portal.access.dialog.footer')}</p>
    </form>
  </Modal>;
}
