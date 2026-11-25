import type {Metadata} from 'next';
import {GuestInvitation} from '@/features/quotations/guest-invitation';

export const metadata:Metadata={title:'RFQ invitation | Procumaterial',robots:{index:false,follow:false},referrer:'no-referrer'};
export default function Page(){return <GuestInvitation/>;}
