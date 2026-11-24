import type { Schemas } from '@/lib/api/generated/schema';
import type { WorkspaceResolution } from '@/features/auth/workspace';
import { verificationPath } from '@/features/auth/verification';

export type WorkspaceRole = 'buyer' | 'supplier' | 'admin';

export const ONBOARDING_EXEMPT_SEGMENTS = new Set([
  'company',
  'company-verification',
  'settings',
  'sessions',
  'profile',
  'notifications',
]);

export const VALID_ROLE_SEGMENTS: Record<WorkspaceRole, Set<string>> = {
  buyer: new Set([
    'dashboard', 'reports', 'company', 'company-verification', 'settings',
    'sessions', 'profile', 'notifications', 'rfqs', 'quote', 'quotations',
    'compare', 'matches', 'matching',
  ]),
  supplier: new Set([
    'dashboard', 'reports', 'company', 'company-verification', 'settings',
    'sessions', 'profile', 'notifications', 'rfqs', 'quote', 'quotations',
    'inbox', 'price-book', 'capabilities', 'membership',
  ]),
  admin: new Set([
    'dashboard', 'reports', 'company', 'company-verification', 'settings',
    'sessions', 'profile', 'notifications', 'automation', 'rfqs', 'quote',
    'quotations', 'compare', 'matches', 'matching', 'users', 'contact-inquiries',
    'buyers', 'suppliers', 'verification', 'claims', 'materials', 'categories',
    'bulk-import', 'supplier-import', 'material-import', 'membership', 'audit', 'roles',
  ]),
};

export function isWorkspaceRole(value: string | undefined): value is WorkspaceRole {
  return value === 'buyer' || value === 'supplier' || value === 'admin';
}

export function isValidWorkspaceSegment(role: WorkspaceRole, segment: string): boolean {
  return VALID_ROLE_SEGMENTS[role].has(segment);
}

/**
 * Validates that a redirect target is a safe, relative, same-origin path.
 * Rejects protocol-relative URLs, external origins, backslashes, control chars,
 * and auth-loop paths.
 */
export function isSafeInternalRedirect(candidate: string | null | undefined): string | null {
  if (!candidate || typeof candidate !== 'string') return null;
  const trimmed = candidate.trim();
  if (!trimmed.startsWith('/') || trimmed.startsWith('//') || trimmed.startsWith('/\\')) return null;
  if (/[\r\n\t\\]/.test(trimmed)) return null;
  try {
    const parsed = new URL(trimmed, 'https://procumaterial.local');
    if (parsed.origin !== 'https://procumaterial.local') return null;
    const path = parsed.pathname + parsed.search;
    if (path === '/login' || path.startsWith('/login?') || path === '/session-expired' || path === '/unauthorized') {
      return null;
    }
    return path;
  } catch {
    return null;
  }
}

/**
 * Resolves whether an authenticated user is authorized for the requested
 * workspace role and segment. Returns `null` if authorized, or a safe internal
 * redirect destination otherwise.
 */
export function resolveAuthorizedDestination(
  role: WorkspaceRole,
  user: Pick<Schemas['UserOut'], 'is_admin' | 'is_active' | 'email_verified' | 'phone_verified' | 'email' | 'phone'>,
  workspaceInfo: WorkspaceResolution | undefined,
  currentSegment = 'dashboard',
  currentPathname?: string,
): string | null {
  if (user.is_active === false) {
    return '/session-expired';
  }
  const verifyDest = verificationPath(user as Schemas['UserOut']);
  if (verifyDest && verifyDest !== currentPathname) {
    return verifyDest;
  }

  // Platform administrators have a dedicated operations workspace separate from
  // buyer and supplier organization roles.
  if (user.is_admin) {
    if (role !== 'admin') {
      return '/app/admin/dashboard';
    }
    return null;
  }

  // Non-admin users can NEVER access /app/admin/*.
  const fallbackDest = isSafeInternalRedirect(workspaceInfo?.destination) || '/organizations';
  if (role === 'admin') {
    return fallbackDest === currentPathname ? '/organizations' : fallbackDest;
  }

  // Check whether the user holds at least one active membership for `role`.
  const authorizedForRole = (workspaceInfo?.workspaces || []).some(
    ws => ws.role === role && Boolean(ws.org_id),
  );

  if (!authorizedForRole) {
    if (workspaceInfo?.needs_onboarding) {
      if (ONBOARDING_EXEMPT_SEGMENTS.has(currentSegment)) {
        return null;
      }
      const onboardDest = isSafeInternalRedirect(workspaceInfo.destination) || `/app/${role}/company`;
      return onboardDest === currentPathname ? null : onboardDest;
    }
    if (fallbackDest && fallbackDest !== currentPathname && !fallbackDest.startsWith(`/app/${role}/`)) {
      return fallbackDest;
    }
    return currentPathname === '/organizations' ? null : '/organizations';
  }

  return null;
}

/**
 * Validates a post-login `?next=` target against the user's actual authorized
 * workspaces so a non-admin redirected from `/app/admin/*` lands on their own
 * authorized dashboard after signing in.
 */
export function resolvePostLoginDestination(
  nextParam: string | null | undefined,
  user: Pick<Schemas['UserOut'], 'is_admin' | 'is_active' | 'email_verified' | 'phone_verified' | 'email' | 'phone'>,
  workspaceInfo: WorkspaceResolution,
): string {
  const defaultDest = user.is_admin
    ? '/app/admin/dashboard'
    : (isSafeInternalRedirect(workspaceInfo.destination) || '/organizations');
  const safeNext = isSafeInternalRedirect(nextParam);
  if (!safeNext) return defaultDest;
  const segments = safeNext.split('?')[0].split('/').filter(Boolean);
  if (segments[0] === 'app' && isWorkspaceRole(segments[1])) {
    const requestedRole = segments[1];
    const requestedSeg = segments[2] || 'dashboard';
    if (!isValidWorkspaceSegment(requestedRole, requestedSeg)) {
      return defaultDest;
    }
    const redirectOverride = resolveAuthorizedDestination(
      requestedRole,
      user,
      workspaceInfo,
      requestedSeg,
      safeNext,
    );
    return redirectOverride ?? safeNext;
  }
  return safeNext;
}
