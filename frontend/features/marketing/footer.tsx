import Link from 'next/link';
import { IconArrowUp } from '@tabler/icons-react';
import { chromeCopy, type MarketingLocale } from './chrome-copy';
import { MarketingBrand, MarketingLanguage } from './chrome';

const footerHrefs = [
  ['/how-it-works', '/suppliers', '/industries', '/pricing'],
  ['/app/buyer/rfqs/new', '/#buyers', '/#comparison'],
  ['/register?role=supplier', '/#suppliers', '/app/supplier/company'],
  ['/about', '/contact', '/#faq'],
  ['/privacy', '/terms'],
];

export function MarketingFooterServer({ locale = 'en' }: { locale?: MarketingLocale }) {
  const c = chromeCopy[locale];
  let labelIndex = 0;
  return (
    <footer className="pm-footer">
      <div className="pm-container">
        <div className="pm-footer-top">
          <div>
            <MarketingBrand />
            <p dir="ltr" className="pm-official-tagline">{c.tagline}</p>
            <p>{c.footerNote}</p>
          </div>
          <a href="#top" className="pm-back-top">
            {c.backToTop}
            <IconArrowUp size={18} aria-hidden="true" />
          </a>
        </div>
        <div className="pm-footer-links">
          {footerHrefs.map((links, index) => (
            <nav key={c.footerGroups[index]} aria-label={c.footerGroups[index]}>
              <h3>{c.footerGroups[index]}</h3>
              {links.map((href) => (
                <Link key={href} href={href} prefetch={false}>
                  {c.footerLinks[labelIndex++]}
                </Link>
              ))}
            </nav>
          ))}
        </div>
        <div className="pm-footer-support">
          <strong>{locale === 'ar' ? 'الدعم' : 'Support'}</strong>
          <a href="mailto:support@procumaterial.com">support@procumaterial.com</a>
          <a href="tel:0501496516">0501496516</a>
        </div>
        <div className="pm-footer-bottom">
          <span>© {new Date().getFullYear()} Procumaterial</span>
          <MarketingLanguage locale={locale} />
        </div>
      </div>
    </footer>
  );
}
