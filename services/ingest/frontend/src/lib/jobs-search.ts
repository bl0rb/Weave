import { apiJson } from './api';

/**
 * Minimal mirror of JobResponse (services/ingest/backend/app/schemas/jobs.py)
 * — only the fields the "Braucht Hilfe" surfaces (sidebar badge, /aufgaben)
 * actually render.
 */
export type FailedJob = {
  id: string;
  original_filename: string;
  status: 'PENDING' | 'RUNNING' | 'FINISHED' | 'FAILED';
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type FailedJobsPage = { items: FailedJob[]; total: number };

/**
 * GET /api/v1/search?status=FAILED — reused rather than /api/v1/jobs
 * because it reports an exact `total` even for `limit=0` (see
 * search_documents in services/ingest/backend/app/api/routes.py), which is
 * all the sidebar badge needs.
 */
export function loadFailedJobs(limit = 20): Promise<FailedJobsPage> {
  const params = new URLSearchParams({ status: 'FAILED', limit: String(limit), offset: '0' });
  return apiJson(`/api/v1/search?${params}`);
}
