/**
 * Shared source catalog (GET/POST/PATCH/DELETE /api/v1/source-catalog).
 * Field names mirror backend/app/schemas/source_catalog.py exactly.
 */

import { apiJson } from '@/lib/api';
import type { CrawlScope } from '@/lib/imports';

export type SourceCatalogCategory = 'internal' | 'legal' | 'reference';

export const CATALOG_CATEGORIES: SourceCatalogCategory[] = ['internal', 'legal', 'reference'];

export type SourceCatalogEntry = {
  id: string;
  title: string;
  description: string;
  category: SourceCatalogCategory;
  publisher: string;
  kind: string;
  start_url: string;
  crawl_scope: CrawlScope;
  include_linked_documents: boolean;
  max_pages: number | null;
  interval_seconds: number | null;
  legal_source: boolean;
  host: string;
  created_at: string;
  updated_at: string;
};

export type SourceCatalogListResponse = { items: SourceCatalogEntry[]; can_manage: boolean };

export type SourceCatalogEntryInput = Omit<SourceCatalogEntry, 'id' | 'kind' | 'host' | 'created_at' | 'updated_at'>;

export function loadSourceCatalog(params: { q?: string; category?: string } = {}, signal?: AbortSignal): Promise<SourceCatalogListResponse> {
  const query = new URLSearchParams();
  if (params.q) query.set('q', params.q);
  if (params.category) query.set('category', params.category);
  const suffix = query.toString() ? `?${query}` : '';
  return apiJson<SourceCatalogListResponse>(`/api/v1/source-catalog${suffix}`, { cache: 'no-store', signal });
}
