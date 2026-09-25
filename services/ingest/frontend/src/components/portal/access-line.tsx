'use client';

import { Globe, Users } from 'lucide-react';
import { accessSummary, type AccessSummaryInput } from '@/lib/access-summary';
import { useI18n } from '@/i18n/provider';

/**
 * "Who can use this in chat" row on a Wissensbereiche card. Kept isolated
 * (rather than inlined in knowledge.tsx) because it's reused as-is while the
 * "Ändern" action itself (opening <AccessDialog>, see access-dialog.tsx) is
 * owned by the caller.
 */
export function AccessLine({ collection, name, canManage, onChangeAccess }: {
  collection: AccessSummaryInput;
  name: string;
  /** Only a manager (owner or admin) may open the access dialog -- everyone else just sees the summary. */
  canManage: boolean;
  onChangeAccess: () => void;
}) {
  const { t, locale } = useI18n();
  const summary = accessSummary(collection, locale);
  const isPublic = collection.visibility === 'public' || (!collection.visibility && collection.read_teams.length === 0);
  return (
    <div className="portal-access-line">
      {isPublic ? <Globe aria-hidden="true" /> : <Users aria-hidden="true" />}
      <span>
        <small>{t('portal.access.usableInChat')}</small>
        <strong>{summary}</strong>
      </span>
      {canManage && <button type="button" className="portal-text-btn" onClick={onChangeAccess} aria-label={t('portal.access.changeAria', { name })}>
        {t('portal.access.change')}
      </button>}
    </div>
  );
}
