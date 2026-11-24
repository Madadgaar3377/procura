import 'server-only';
import { cookies, headers } from 'next/headers';
import { redirect } from 'next/navigation';
import type { Schemas } from '@/lib/api/generated/schema';
import type { WorkspaceResolution } from '@/features/auth/workspace';
import {
  isSafeInternalRedirect,
  resolveAuthorizedDestination,
  type WorkspaceRole,
} from './route-guard';

const ACCESS_COOKIE = 'procura_access';
const REFRESH_COOKIE = 'procura_refresh';

function upstreamOrigin(): string {
  const value = process.env.FASTAPI_INTERNAL_URL || process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('Invalid FASTAPI_INTERNAL_URL');
  }
  return url.origin;
}

async function isCrossOriginApiRequest(): Promise<boolean> {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  if (!configured) return false;
  try {
    const apiHost = new URL(configured).hostname.toLowerCase();
    const headerStore = await headers();
    const reqHost = (headerStore.get('host') || '').split(':')[0].toLowerCase();
    return Boolean(apiHost && reqHost && apiHost !== reqHost);
  } catch {
    return false;
  }
}

async function fetchWithBearer<T>(path: string, accessToken: string): Promise<{ status: number; data: T | null }> {
  const res = await fetch(`${upstreamOrigin()}/api/v1/${path}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: 'no-store',
    redirect: 'error',
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) {
    return { status: res.status, data: null };
  }
  const data = (await res.json().catch(() => null)) as T | null;
  return { status: res.status, data };
}

/**
 * Server-side route protection for `/app/*` routes.
 * Performs actual backend authorization checks before rendering any protected
 * dashboard layout or page content.
 */
export async function enforceServerWorkspaceAccess(
  role: WorkspaceRole,
  currentSegment: string,
  pathname: string,
): Promise<void> {
  if (process.env.NEXT_PUBLIC_DEMO_MODE === 'true' && process.env.NODE_ENV !== 'production') {
    return;
  }

  const cookieStore = await cookies();
  const accessToken = cookieStore.get(ACCESS_COOKIE)?.value;
  const refreshToken = cookieStore.get(REFRESH_COOKIE)?.value;

  const safeNext = isSafeInternalRedirect(pathname);
  const loginUrl = safeNext ? `/login?next=${encodeURIComponent(safeNext)}` : '/login';

  if (!accessToken && !refreshToken) {
    // On cross-origin API deployments (www.procumaterial.com -> api.procumaterial.com),
    // defer to SessionGate (which renders only a neutral loading screen) if cookies
    // are not yet visible on the frontend host, preventing infinite /login loops.
    if (await isCrossOriginApiRequest()) {
      return;
    }
    redirect(loginUrl);
  }

  // If access token is absent (expired cookie) but refresh token exists, allow
  // the client SessionGate -> auth/browser/refresh flow to rotate once cleanly.
  if (!accessToken) {
    return;
  }

  let userRes: { status: number; data: Schemas['UserOut'] | null };
  try {
    userRes = await fetchWithBearer<Schemas['UserOut']>('auth/me', accessToken);
  } catch {
    return;
  }

  if (userRes.status === 401 || !userRes.data) {
    if (refreshToken) {
      return;
    }
    redirect(loginUrl);
  }

  const user = userRes.data;
  if (user.is_active === false) {
    redirect('/session-expired');
  }

  let workspaceInfo: WorkspaceResolution | undefined;
  if (user.is_admin) {
    workspaceInfo = {
      role: 'admin',
      org_id: null,
      destination: '/app/admin/dashboard',
      workspaces: [{ role: 'admin', org_id: null, org_name: 'Procumaterial Operations' }],
      needs_onboarding: false,
    };
  } else {
    try {
      const wsRes = await fetchWithBearer<WorkspaceResolution>('auth/workspace', accessToken);
      if (wsRes.status === 401) {
        if (!refreshToken) redirect(loginUrl);
        return;
      }
      workspaceInfo = wsRes.data ?? undefined;
    } catch {
      return;
    }
  }

  const destination = resolveAuthorizedDestination(
    role,
    user,
    workspaceInfo,
    currentSegment,
    pathname,
  );
  if (destination && destination !== pathname) {
    redirect(destination);
  }
}
