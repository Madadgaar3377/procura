import { notFound } from 'next/navigation';
import RouteView from '@/components/route-view';
import LivePortal from '@/features/portal';
import { isValidWorkspaceSegment, isWorkspaceRole } from '@/lib/auth/route-guard';
import { enforceServerWorkspaceAccess } from '@/lib/auth/server-guard';

export const dynamic = 'force-dynamic';

export default async function Page({
  params,
}: {
  params: Promise<{ workspace: string[] }>;
}) {
  const { workspace } = await params;
  const segments = ['app', ...(workspace || [])];
  const role = workspace?.[0];
  const current = workspace?.[1] || 'dashboard';
  const entity = workspace?.[2];

  if (process.env.NEXT_PUBLIC_DEMO_MODE === 'true' && process.env.NODE_ENV !== 'production') {
    return <RouteView segments={segments} />;
  }

  if (!isWorkspaceRole(role)) {
    notFound();
  }

  const pathname = '/' + segments.join('/');
  // Enforce backend-verified role & workspace authorization BEFORE rendering
  // any protected page or layout. Unauthorized users are redirected on the server.
  await enforceServerWorkspaceAccess(role, current, pathname);

  if (!isValidWorkspaceSegment(role, current)) {
    notFound();
  }

  return <LivePortal role={role} current={current} entity={entity} />;
}
