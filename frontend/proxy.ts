import {NextRequest,NextResponse} from 'next/server';

// Public localization only. Authentication and dashboard requests do not pass here.
export function proxy(request:NextRequest){
  const query=request.nextUrl.searchParams.get('lang');
  const locale=query==='en'||query==='ar'?query:request.cookies.get('procura.locale')?.value==='ar'?'ar':'en';
  const requestHeaders=new Headers(request.headers);requestHeaders.set('x-procura-marketing-locale',locale);
  const response=NextResponse.next({request:{headers:requestHeaders}});
  if(request.nextUrl.protocol==='https:')response.headers.set('Cross-Origin-Opener-Policy','same-origin-allow-popups');
  if(query==='ar'||query==='en')response.cookies.set('procura.locale',locale,{path:'/',maxAge:31536000,sameSite:'lax',secure:request.nextUrl.protocol==='https:'});
  return response;
}

export const config={matcher:['/','/how-it-works','/suppliers/:path*','/industries','/pricing','/about','/contact','/faq','/privacy','/terms']};
