import type { Schemas } from '@/lib/api/generated/schema';

export function verificationPath(user: Pick<Schemas['UserOut'],'phone'|'email'|'phone_verified'|'email_verified'>): string | null {
  if (!user.phone_verified) {
    try {
      sessionStorage.setItem('procura.pending-verification-phone', user.phone);
      sessionStorage.setItem('procura.pending-verification-email', user.email || '');
    } catch { /* The verification page can recover destinations from auth/me. */ }
    return '/verify-mobile';
  }
  return user.email && !user.email_verified ? '/verify-email' : null;
}
