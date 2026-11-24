"use client";
import { verificationPath } from './verification';
import type { WorkspaceOption, WorkspaceResolution } from './workspace';
import {
  isSafeInternalRedirect,
  resolveAuthorizedDestination,
  type WorkspaceRole,
} from '@/lib/auth/route-guard';
import { useLocale } from '@/lib/locale';
import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { api } from '@/lib/api/client';
import { ApiError } from '@/lib/api/errors';
import type { Schemas } from '@/lib/api/generated/schema';
import Link from 'next/link';
import { ApiScope, clearApiCache } from '@/lib/api/use-api';
import { AppLoadingScreen, ErrorState } from '@/features/shared/loading';

type User = Schemas['UserOut'];
type Org = Schemas['OrganizationOut'];
type Session = {
  user: User;
  organizations: Org[];
  org?: Org;
  allWorkspaces: WorkspaceOption[];
  select: (id: string) => void;
  switchWorkspace: (nextRole: 'buyer' | 'supplier', nextOrgId: string) => Promise<void>;
  reload: () => Promise<void>;
  syncUser: (u: User) => void;
};

const Context = createContext<Session | null>(null);
export function useOptionalSession() { return useContext(Context); }
export function useSession() {
  const value = useContext(Context);
  if (!value) throw new Error('Session provider required');
  return value;
}

const LOGOUT_SYNC_KEY = 'procura:auth-logout';

function clearSensitiveClientState() {
  clearApiCache();
  try {
    const keysToClear: string[] = [];
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const k = sessionStorage.key(i);
      if (k && (k.startsWith('procura.organization:') || k.startsWith('procura.rfq') || k.startsWith('procura.pending-'))) {
        keysToClear.push(k);
      }
    }
    for (const k of keysToClear) sessionStorage.removeItem(k);
  } catch {}
  try {
    localStorage.removeItem('procura:supplier-import');
  } catch {}
}

export function SessionGate({ role, children }: { role: WorkspaceRole; children: React.ReactNode }) {
  const { t } = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User>();
  const [organizations, setOrganizations] = useState<Org[]>([]);
  const [workspaceInfo, setWorkspaceInfo] = useState<WorkspaceResolution>();
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [unauthenticated, setUnauthenticated] = useState(false);
  const redirectedTo = useRef<string | null>(null);

  async function reload() {
    const u = await api<User>('auth/me');
    if (u.is_active === false) {
      setVerifying(true);
      router.replace('/session-expired');
      return;
    }
    const destination = verificationPath(u);
    if (destination) {
      setVerifying(true);
      router.replace(destination);
      return;
    }
    const [orgs, ws] = u.is_admin
      ? [
          [],
          {
            role: 'admin' as const,
            org_id: null,
            destination: '/app/admin/dashboard',
            workspaces: [{ role: 'admin' as const, org_id: null, org_name: 'Procumaterial Operations' }],
            needs_onboarding: false,
          },
        ]
      : await Promise.all([api<Org[]>('organizations'), api<WorkspaceResolution>('auth/workspace')]);

    setVerifying(false);
    setUnauthenticated(false);
    setUser(u);
    setOrganizations(orgs);
    setWorkspaceInfo(ws);
    const initialSelected: Record<string, string> = {};
    for (const w of ['buyer', 'supplier', 'admin']) {
      if (ws.role === w && ws.org_id) {
        initialSelected[w] = ws.org_id;
      } else if (u.last_org_id) {
        initialSelected[w] = u.last_org_id;
      } else {
        try {
          initialSelected[w] = sessionStorage.getItem(`procura.organization:${u.id}:${w}`) || '';
        } catch {}
      }
    }
    setSelected(initialSelected);
  }

  useEffect(() => {
    let active = true;
    reload()
      .then(() => { if (active) setReady(true); })
      .catch((cause: unknown) => {
        if (!active) return;
        if (cause instanceof ApiError && cause.status === 401) {
          setUnauthenticated(true);
          const safeNext = isSafeInternalRedirect(pathname);
          router.replace(safeNext ? `/login?next=${encodeURIComponent(safeNext)}` : '/login');
        } else {
          setError('We could not load your workspace. Please try again.');
        }
        setReady(true);
      });
    const expire = () => {
      clearSensitiveClientState();
      router.replace('/session-expired');
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === LOGOUT_SYNC_KEY && event.newValue) {
        clearSensitiveClientState();
        router.replace('/login');
      }
    };
    window.addEventListener('procura:session-expired', expire);
    window.addEventListener('storage', onStorage);
    return () => {
      active = false;
      window.removeEventListener('procura:session-expired', expire);
      window.removeEventListener('storage', onStorage);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  const currentSegment = pathname.split('/').filter(Boolean)[2] || 'dashboard';
  const redirectTarget = ready && !verifying && user
    ? resolveAuthorizedDestination(role, user, workspaceInfo, currentSegment, pathname)
    : null;

  useEffect(() => {
    if (!redirectTarget || redirectTarget === pathname) return;
    if (redirectedTo.current === redirectTarget) return;
    redirectedTo.current = redirectTarget;
    router.replace(redirectTarget);
  }, [redirectTarget, pathname, router]);

  useEffect(() => {
    redirectedTo.current = null;
  }, [pathname]);

  // Never render unauthorized layouts, sidebars, or "Access restricted" placeholders.
  if (!ready || verifying || unauthenticated || Boolean(redirectTarget)) {
    return <AppLoadingScreen />;
  }

  if (!user) {
    return (
      <main className="auth-page">
        <div className="panel">
          <h1>{t('Sign in to continue')}</h1>
          <ErrorState
            error={new Error(error || 'Your session could not be verified.')}
            retry={() => {
              setReady(false);
              setError('');
              reload()
                .catch(() => setError('We could not load your workspace. Please try again.'))
                .finally(() => setReady(true));
            }}
          />
          <Link href="/login">{t('Sign in')}</Link>
        </div>
      </main>
    );
  }

  const eligible = organizations.filter(
    o => o.is_active && (role === 'admin' || (role === 'buyer' && o.is_buyer) || (role === 'supplier' && o.is_supplier)),
  );
  const org = role === 'admin' ? undefined : eligible.find(o => o.id === selected[role]) || eligible[0];
  const authorizedWorkspaces = (workspaceInfo?.workspaces || []).filter(
    ws => (user.is_admin ? ws.role === 'admin' : ws.role !== 'admin'),
  );

  async function persistWorkspace(nextRole: 'buyer' | 'supplier', nextOrgId: string) {
    clearApiCache();
    setSelected(previous => ({ ...previous, [nextRole]: nextOrgId }));
    try {
      sessionStorage.setItem(`procura.organization:${user!.id}:${nextRole}`, nextOrgId);
    } catch {}
    try {
      const updated = await api<WorkspaceResolution>('auth/workspace', {
        method: 'PUT',
        body: JSON.stringify({ role: nextRole, org_id: nextOrgId }),
      });
      setWorkspaceInfo(updated);
    } catch {
      /* Server validation failure will be reflected on next reload. */
    }
  }

  return (
    <Context.Provider
      value={{
        user,
        organizations: eligible,
        org,
        allWorkspaces: authorizedWorkspaces,
        select: (id) => { if (role !== 'admin') void persistWorkspace(role, id); },
        switchWorkspace: async (nextRole, nextOrgId) => {
          if (user.is_admin) return;
          await persistWorkspace(nextRole, nextOrgId);
          if (nextRole !== role) router.push(`/app/${nextRole}/dashboard`);
        },
        reload,
        syncUser: (u) => setUser(u),
      }}
    >
      <ApiScope.Provider key={`${user.id}:${role}:${org?.id || 'none'}`} value={`${user.id}:${role}:${org?.id || 'none'}`}>
        {children}
      </ApiScope.Provider>
    </Context.Provider>
  );
}

export async function signOut() {
  try {
    await api('auth/logout', { method: 'POST' });
  } finally {
    clearSensitiveClientState();
    try {
      localStorage.setItem(LOGOUT_SYNC_KEY, String(Date.now()));
    } catch {}
    window.location.replace('/login');
  }
}
