import Link from 'next/link';
import { IconArrowUp, IconMail, IconPhone } from '@tabler/icons-react';
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
          <div className="pm-footer-brand">
            <MarketingBrand />
            <p dir="ltr" className="pm-official-tagline">{c.tagline}</p>
            <p className="pm-footer-note">{c.footerNote}</p>
          </div>
          <a href="#top" className="pm-back-top">
            <span>{c.backToTop}</span>
            <IconArrowUp size={16} stroke={1.75} aria-hidden="true" />
          </a>
        </div>

        <div className="pm-footer-links">
          {footerHrefs.map((links, index) => (
            <nav key={c.footerGroups[index]} aria-label={c.footerGroups[index]}>
              <h3>{c.footerGroups[index]}</h3>
              <ul>
                {links.map((href) => (
                  <li key={href}>
                    <Link href={href} prefetch={false}>
                      {c.footerLinks[labelIndex++]}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="pm-footer-support">
          <h3 className="pm-footer-support-heading">{c.supportHeading}</h3>
          <div className="pm-footer-support-items">
            <div className="pm-footer-contact-item">
              <span className="pm-footer-contact-label">{c.supportEmailLabel}</span>
              <a
                href="mailto:support@procumaterial.com"
                className="pm-footer-contact-link"
                dir="ltr"
              >
                <IconMail size={16} stroke={1.75} aria-hidden="true" />
                <span>support@procumaterial.com</span>
              </a>
            </div>
            <div className="pm-footer-contact-item">
              <span className="pm-footer-contact-label">{c.supportPhoneLabel}</span>
              <a
                href="tel:0501496516"
                className="pm-footer-contact-link"
                dir="ltr"
              >
                <IconPhone size={16} stroke={1.75} aria-hidden="true" />
                <span>0501496516</span>
              </a>
            </div>
          </div>
        </div>

        <div className="pm-footer-bottom">
          <div className="pm-footer-legal-meta">
            <span>© 2026 Procumaterial. {c.allRightsReserved}</span>
            <span className="pm-footer-sep" aria-hidden="true">·</span>
            <span className="pm-footer-credit">
              {c.builtBy}{' '}
              <a
                href="https://www.abubakkar.dev"
                target="_blank"
                rel="noopener noreferrer"
              >
                Abubakkar Sajid
              </a>
            </span>
          </div>
          <MarketingLanguage locale={locale} />
        </div>
      </div>
    </footer>
  );
}
