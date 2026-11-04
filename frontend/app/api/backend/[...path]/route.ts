import { NextRequest } from 'next/server';
import { proxy } from '@/lib/api/server';
export const runtime='nodejs';
export const dynamic='force-dynamic';
async function handler(request:NextRequest,{params}:{params:Promise<{path:string[]}>}) {return proxy(request,(await params).path);}
export {handler as GET,handler as POST,handler as PUT,handler as PATCH,handler as DELETE};
