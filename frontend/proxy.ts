import { NextRequest, NextResponse } from 'next/server';
import { isSafeInternalRedirect } from '@/lib/auth/route-guard';

const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === 'true' && process.env.NODE_ENV !== 'production';

export function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;

  // Protected workspace routes: enforce no-store caching and fast-path
  // unauthenticated redirects before any protected layout renders.
  if (pathname.startsWith('/app/') || pathname === '/app') {
    if (!DEMO_MODE) {
      const hasAccess = Boolean(request.cookies.get('procura_access')?.value);
      const hasRefresh = Boolean(request.cookies.get('procura_refresh')?.value);
      if (!hasAccess && !hasRefresh) {
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
    '/app',
    '/app/:path*',
  ],
};
