import { api } from '@/lib/api/client';
import type { Schemas } from '@/lib/api/generated/schema';
import { verificationPath } from './verification';

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
): Promise<string> {
  const verification = verificationPath(user);
  if (verification) return verification;
  if (user.is_admin) return '/app/admin/dashboard';
  const resolution = await api<WorkspaceResolution>('auth/workspace', { signal });
  return resolution.destination || '/organizations';
}
