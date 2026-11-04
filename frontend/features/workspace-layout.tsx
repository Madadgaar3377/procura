"use client";
import {useLocale} from '@/lib/locale';
import { usePathname } from 'next/navigation';
import { SessionGate } from '@/features/auth/session';
import { PortalShell } from '@/components/route-view';

export default function WorkspaceLayout({children}:{children:React.ReactNode}){
 const {t}=useLocale();
 const path=usePathname();const segments=path.split('/').filter(Boolean);
 const role=segments[1];
 if(role!=='buyer'&&role!=='supplier'&&role!=='admin')return <main className="container public-content">{t("Workspace unavailable")}</main>;
 if(process.env.NEXT_PUBLIC_DEMO_MODE==='true'&&process.env.NODE_ENV!=='production')return <>{children}</>;
 return <SessionGate role={role}><PortalShell role={role} path={path} segments={segments.slice(2)}>{children}</PortalShell></SessionGate>;
}
