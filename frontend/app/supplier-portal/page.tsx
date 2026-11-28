import type { Metadata } from 'next';
import '../globals.css';
import { SupplierPortal } from '@/features/supplier-portal/portal';
import { LocaleProvider } from '@/lib/locale';

// Dedicated supplier invitation route. It must never fall through to the public
// catch-all (which rendered the About page) or be redirected to login: invitation
// links are /supplier-portal#token=..., and the token is exchanged client-side.
export const metadata: Metadata = {
  title: 'Supplier RFQ invitation',
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
};

export default function SupplierPortalPage() {
  return <LocaleProvider><SupplierPortal /></LocaleProvider>;
}
