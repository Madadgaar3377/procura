"use client";

import {useEffect, useRef} from 'react';
import Link from 'next/link';
import {IconArrowRight, IconCheck, IconLock, IconLifebuoy} from '@tabler/icons-react';
import {MarketingFooter, MarketingHeader} from '@/features/marketing/chrome';
import {marketingFont} from '@/features/marketing/fonts';

export function ResetSuccess({arabic}: {arabic: boolean}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const locale = arabic ? 'ar' : 'en';
  useEffect(() => { heading.current?.focus({preventScroll: true}); }, []);

  return <div className={`procura-marketing pm-reset-success ${marketingFont.variable}`} lang={locale} dir={arabic ? 'rtl' : 'ltr'} id="top">
    <MarketingHeader locale={locale}/>
    <main id="main-content" className="pm-reset-main">
      <div className="pm-reset-content">
        <section className="pm-reset-card" aria-labelledby="reset-success-title">
          <span className="pm-reset-check" aria-hidden="true"><IconCheck size={32} stroke={2}/></span>
          <span className="pm-eyebrow">{arabic ? 'اكتملت إعادة تعيين كلمة المرور' : 'PASSWORD RESET COMPLETE'}</span>
          <h1 id="reset-success-title" ref={heading} tabIndex={-1}>{arabic ? 'تم تحديث كلمة المرور' : 'Password updated'}</h1>
          <p className="pm-reset-description">{arabic ? 'تم حفظ كلمة المرور الجديدة بنجاح. يمكنك الآن تسجيل الدخول والعودة إلى أعمالك.' : 'Your new password has been saved successfully. Sign in to get back to your workspace.'}</p>
          <Link href={`/login?lang=${locale}`} className="pm-button pm-reset-login">{arabic ? 'العودة لتسجيل الدخول' : 'Back to login'}<IconArrowRight className="pm-arrow" size={18} aria-hidden="true"/></Link>
          <div className="pm-reset-note"><IconLock size={18} aria-hidden="true"/><p>{arabic ? 'استخدم كلمة المرور الجديدة في المرة القادمة التي تسجّل فيها الدخول.' : 'Use your new password the next time you sign in.'}</p></div>
          <Link href={`/?lang=${locale}`} className="pm-button pm-button-secondary pm-reset-home">{arabic ? 'العودة إلى الصفحة الرئيسية' : 'Return to homepage'}</Link>
        </section>
        <p className="pm-reset-help"><IconLifebuoy size={17} aria-hidden="true"/>{arabic ? 'هل تحتاج إلى مساعدة؟' : 'Need a hand?'} <Link href={`/contact?lang=${locale}`}>{arabic ? 'تواصل مع الدعم' : 'Contact support'}</Link></p>
      </div>
    </main>
    <MarketingFooter locale={locale}/>
  </div>;
}
