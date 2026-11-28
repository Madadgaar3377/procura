import { NextRequest, NextResponse } from 'next/server';
import { isSafeInternalRedirect } from '@/lib/auth/route-guard';

const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === 'true' && process.env.NODE_ENV !== 'production';

function isCrossOriginApi(request: NextRequest): boolean {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  if (!configured) return false;
  try {
    const apiHost = new URL(configured).hostname.toLowerCase();
    const reqHost = (request.headers.get('host') || request.nextUrl.hostname || '').split(':')[0].toLowerCase();
    return Boolean(apiHost && reqHost && apiHost !== reqHost);
  } catch {
    return false;
  }
}

export function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;

  // Protected workspace routes: enforce strict no-store caching so shared CDNs
  // never cache authenticated HTML/RSC payloads.
  if (pathname.startsWith('/app/') || pathname === '/app') {
    if (!DEMO_MODE) {
      const hasAccess = Boolean(request.cookies.get('procura_access')?.value);
      const hasRefresh = Boolean(request.cookies.get('procura_refresh')?.value);
      // When the API is same-origin, missing cookies guarantee an unauthenticated
      // visitor and we can redirect immediately at the proxy. When the API is on
      // a separate subdomain (e.g. api.procumaterial.com), allow SSR/SessionGate
      // to verify so legacy host-only cookies cannot cause a /login redirect loop.
      if (!hasAccess && !hasRefresh && !isCrossOriginApi(request)) {
        const loginUrl = request.nextUrl.clone();
        loginUrl.pathname = '/login';
        const safeNext = isSafeInternalRedirect(pathname + request.nextUrl.search);
        loginUrl.search = safeNext ? `?next=${encodeURIComponent(safeNext)}` : '';
        const redirectRes = NextResponse.redirect(loginUrl);
        redirectRes.headers.set('Cache-Control', 'private, no-store, no-cache, must-revalidate');
        redirectRes.headers.set('Pragma', 'no-cache');
        redirectRes.headers.set('Vary', 'Cookie');
        return redirectRes;
      }
    }
    const response = NextResponse.next();
    response.headers.set('Cache-Control', 'private, no-store, no-cache, must-revalidate');
    response.headers.set('Pragma', 'no-cache');
    response.headers.set('Vary', 'Cookie');
    if (request.nextUrl.protocol === 'https:') {
      response.headers.set('Cross-Origin-Opener-Policy', 'same-origin-allow-popups');
    }
    return response;
  }

  // Public localization for marketing and auth pages.
  const query = request.nextUrl.searchParams.get('lang');
  const locale = query === 'en' || query === 'ar'
    ? query
    : request.cookies.get('procura.locale')?.value === 'ar'
      ? 'ar'
      : 'en';
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-procura-marketing-locale', locale);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  if (request.nextUrl.protocol === 'https:') {
    response.headers.set('Cross-Origin-Opener-Policy', 'same-origin-allow-popups');
  }
  if (query === 'ar' || query === 'en') {
    response.cookies.set('procura.locale', locale, {
      path: '/',
      maxAge: 31536000,
      sameSite: 'lax',
      secure: request.nextUrl.protocol === 'https:',
    });
  }
  return response;
}

export const config = {
  matcher: [
    '/',
    '/login',
    '/how-it-works',
    '/suppliers/:path*',
    '/industries',
    '/pricing',
    '/about',
    '/contact',
    '/faq',
    '/privacy',
    '/terms',
    '/supplier-portal',
    '/app',
    '/app/:path*',
  ],
};
