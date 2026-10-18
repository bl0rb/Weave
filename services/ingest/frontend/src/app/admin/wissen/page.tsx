import { redirect } from 'next/navigation';

// Renamed to /admin/ki so the path matches the section label; keeps
// bookmarks and ?bereich= deep links working.
export default async function LegacyAdminPage({ searchParams }: { searchParams: Promise<{ bereich?: string | string[] }> }) {
  const { bereich } = await searchParams;
  const value = Array.isArray(bereich) ? bereich[0] : bereich;
  redirect(value ? `/admin/ki?bereich=${encodeURIComponent(value)}` : '/admin/ki');
}
