import Link from 'next/link';
import { Globe, Users } from 'lucide-react';
import { accessSummary, type AccessSummaryInput } from '@/lib/access-summary';

/**
 * "Who can use this in chat" row on a Wissensbereiche card. Kept isolated
 * (rather than inlined in knowledge.tsx) because a later task swaps the
 * "Ändern" link for a dialog without touching the summary logic here.
 */
export function AccessLine({ collection, href, name }: { collection: AccessSummaryInput; href: string; name: string }) {
  const summary = accessSummary(collection);
  const isPublic = collection.visibility === 'public' || (!collection.visibility && collection.read_teams.length === 0);
  return (
    <div className="portal-access-line">
      {isPublic ? <Globe aria-hidden="true" /> : <Users aria-hidden="true" />}
      <span>
        <small>Im Chat nutzbar für</small>
        <strong>{summary}</strong>
      </span>
      <Link className="portal-text-btn" href={href} aria-label={`Zugriff für ${name} ändern`}>
        Ändern
      </Link>
    </div>
  );
}
