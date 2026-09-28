import { apiJson } from '@/lib/api';

export type SystemStatusValue = 'ok' | 'degraded' | 'down';
export type SystemArea = 'portal' | 'processing' | 'chat';

/** What every signed-in person sees: functional areas only. */
export type SystemStatus = {
  status: SystemStatusValue;
  checked_at: string;
  areas: { key: SystemArea; status: SystemStatusValue }[];
};

export type ComponentStatus = {
  key: string; area: SystemArea; status: SystemStatusValue;
  latency_ms: number | null; detail: string | null; target: string | null;
};

export type AdminSystemStatus = SystemStatus & { components: ComponentStatus[] };

export function loadSystemStatus(signal?: AbortSignal): Promise<SystemStatus> {
  return apiJson('/api/v1/system-status', { signal });
}

/** ``refresh`` skips the backend's short probe cache ("Jetzt prüfen"). */
export function loadAdminSystemStatus(refresh = false, signal?: AbortSignal): Promise<AdminSystemStatus> {
  return apiJson(`/api/v1/auth/admin/system-status${refresh ? '?refresh=true' : ''}`, { signal });
}
