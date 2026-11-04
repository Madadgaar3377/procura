"use client";
import { usePathname } from 'next/navigation';
import RouteView from '@/components/route-view';
import LivePortal from '@/features/portal';
export default function Page(){const segments=usePathname().split('/').filter(Boolean);const role=segments[1];if(process.env.NEXT_PUBLIC_DEMO_MODE==='true'&&process.env.NODE_ENV!=='production')return <RouteView segments={segments}/>;if(role!=='buyer'&&role!=='supplier'&&role!=='admin')return null;return <LivePortal role={role} current={segments[2]||'dashboard'} entity={segments[3]}/>;}
