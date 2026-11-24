import { api } from '@/lib/api/client';
import type { Schemas } from '@/lib/api/generated/schema';
import { verificationPath } from './verification';
import { resolvePostLoginDestination } from '@/lib/auth/route-guard';

export type WorkspaceOption = {
  role: 'buyer' | 'supplier' | 'admin';
  org_id: string | null;
  org_name: string;
  verified_status?: string;
};

export type WorkspaceResolution = {
  role: 'buyer' | 'supplier' | 'admin' | null;
  org_id: string | null;
  destination: string;
  workspaces: WorkspaceOption[];
  needs_onboarding: boolean;
};

export async function resolveWorkspaceDestination(
  user: Schemas['UserOut'],
  signal?: AbortSignal,
  nextParam?: string | null,
): Promise<string> {
  const verification = verificationPath(user);
  if (verification) return verification;

  const requestedNext = nextParam !== undefined
    ? nextParam
    : (typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('next') : null);

  if (user.is_admin) {
    const adminResolution: WorkspaceResolution = {
      role: 'admin',
      org_id: null,
      destination: '/app/admin/dashboard',
      workspaces: [{ role: 'admin', org_id: null, org_name: 'Procumaterial Operations' }],
      needs_onboarding: false,
    };
    return resolvePostLoginDestination(requestedNext, user, adminResolution);
  }

  const resolution = await api<WorkspaceResolution>('auth/workspace', { signal });
  return resolvePostLoginDestination(requestedNext, user, resolution);
}
