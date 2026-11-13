export type MarketingLocale = 'en' | 'ar';

const en = {
  nav: ['For Buyers', 'For Suppliers', 'How It Works', 'Industries', 'Supplier Directory', 'Memberships', 'Contact Us'],
  signIn: 'Sign In',
  getStarted: 'Get Started',
  openMenu: 'Open navigation menu',
  closeMenu: 'Close navigation menu',
  language: 'Switch to Arabic',
  tagline: 'Connecting Saudi Businesses with Trusted Suppliers.',
  footerGroups: ['Platform', 'For Buyers', 'For Suppliers', 'Company', 'Legal'],
  footerLinks: ['How It Works', 'Supplier Directory', 'Industries', 'Memberships', 'Create RFQ', 'Buyer Features', 'Quotation Comparison', 'Register', 'Supplier Features', 'Manage Capabilities', 'About', 'Contact', 'FAQs', 'Privacy Policy', 'Terms and Conditions'],
  footerNote: 'Built for business procurement in Saudi Arabia.',
  backToTop: 'Back to top',
  skipContent: 'Skip to content',
};

const ar: typeof en = {
  nav: ['للمشترين', 'للموردين', 'كيف تعمل المنصة', 'القطاعات', 'دليل الموردين', 'العضويات', 'تواصل معنا'],
  signIn: 'تسجيل الدخول',
  getStarted: 'ابدأ الآن',
  openMenu: 'فتح قائمة التنقل',
  closeMenu: 'إغلاق قائمة التنقل',
  language: 'التبديل إلى الإنجليزية',
  tagline: 'Connecting Saudi Businesses with Trusted Suppliers.',
  footerGroups: ['المنصة', 'للمشترين', 'للموردين', 'الشركة', 'الشؤون القانونية'],
  footerLinks: ['كيف تعمل المنصة', 'دليل الموردين', 'القطاعات', 'العضويات', 'إنشاء طلب عرض سعر', 'مزايا المشترين', 'مقارنة العروض', 'التسجيل', 'مزايا الموردين', 'إدارة قدرات التوريد', 'عن Procumaterial', 'تواصل معنا', 'الأسئلة الشائعة', 'سياسة الخصوصية', 'الشروط والأحكام'],
  footerNote: 'مصممة لمشتريات الشركات في المملكة العربية السعودية.',
  backToTop: 'العودة إلى الأعلى',
  skipContent: 'انتقل إلى المحتوى',
};

export const chromeCopy = { en, ar };
