"use client";
import '@/app/industries.css';

import {useEffect, useMemo, useState} from 'react';
import Link from 'next/link';
import {IconArrowRight, IconBolt, IconBuilding, IconCheck, IconSettings} from '@tabler/icons-react';
import {useApi} from '@/lib/api/use-api';
import {useLocale} from '@/lib/locale';
import {MarketingCTA} from './chrome';
import type {Schemas} from '@/lib/api/generated/schema';

type Catalog = Schemas['IndustryCatalogPage'];
type Card = Schemas['IndustryCategoryCard'];
type Material = Schemas['MaterialOut'];
const supported = ['Civil', 'Electrical', 'Mechanical'];

function ExpandedMaterials({category, discipline, locale, text}: {category: Card; discipline: string; locale: string; text: (en: string, ar: string) => string}) {
  const [offset, setOffset] = useState(5);
  const id = category.subcategory_id || category.main_category_id;
  const path = `taxonomy/materials?${category.subcategory_id ? 'subcategory_id' : 'main_category_id'}=${encodeURIComponent(id)}&discipline=${encodeURIComponent(discipline)}&limit=100&offset=${offset}`;
  const result = useApi<Material[]>(path);
  return <div className="pm-industry-expanded" aria-live="polite">
    {result.loading && <p>{text('Loading more materials…', 'جارٍ تحميل المزيد من المواد…')}</p>}
    {result.error && <p role="alert">{text('More materials could not be loaded.', 'تعذر تحميل المزيد من المواد.')}</p>}
    {!!result.data?.length && <ul>{result.data.map(item => <li key={item.id}><span><IconCheck size={15}/></span><div><strong dir="auto">{locale === 'ar' && item.name_ar ? item.name_ar : item.name_en}</strong><p dir="auto">{locale === 'ar' && item.arabic_description ? item.arabic_description : item.description || item.normalized_description || item.name_en}</p></div><small>{item.unit}</small></li>)}</ul>}
    {offset + (result.data?.length || 0) < category.material_count && <button type="button" onClick={() => setOffset(value => value + 100)}>{text('Load next 100', 'تحميل ١٠٠ أخرى')}</button>}
  </div>;
}

export function IndustryCatalog({initialDiscipline='Civil'}: {initialDiscipline?: string}) {
  const {arabic} = useLocale();
  const locale = arabic ? 'ar' : 'en';
  const text = (en: string, ar: string) => arabic ? ar : en;
  const [discipline, setDiscipline] = useState(supported.includes(initialDiscipline) ? initialDiscipline : 'Civil');
  const [input, setInput] = useState('');
  const [term, setTerm] = useState('');
  const [page, setPage] = useState(1);
  useEffect(() => {
    const timer = window.setTimeout(() => {setTerm(input.trim()); setPage(1);}, 250);
    return () => window.clearTimeout(timer);
  }, [input]);
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set('discipline', discipline);
    window.history.replaceState(null, '', url);
  }, [discipline]);
  const params = new URLSearchParams({discipline, q: term, page: String(page), limit: '9'});
  const result = useApi<Catalog>(`taxonomy/industry-catalog?${params}`);
  const categories = result.data?.categories || [];
  const groups = useMemo(() => {
    const grouped = new Map<string, {id: string; name: string; cards: Card[]}>();
    for (const card of categories) {
      const group = grouped.get(card.main_category_id) || {id: card.main_category_id,
        name: arabic && card.main_category_ar ? card.main_category_ar : card.main_category, cards: []};
      group.cards.push(card);
      grouped.set(card.main_category_id, group);
    }
    return [...grouped.values()];
  }, [categories, arabic]);
  const disciplineData = result.data?.disciplines || [];
  const selectedCount = disciplineData.find(item => item.id.toLowerCase() === discipline.toLowerCase());
  const icon = discipline === 'Electrical' ? <IconBolt size={21}/> : discipline === 'Mechanical' ? <IconSettings size={21}/> : <IconBuilding size={21}/>;
  const clear = () => {setInput(''); setTerm(''); setPage(1); setDiscipline('Civil');};
  const linkFor = (card: Card) => {
    const query = new URLSearchParams({main_category_id: card.main_category_id, lang: locale});
    if (card.subcategory_id) query.set('subcategory_id', card.subcategory_id);
    return `/suppliers?${query}`;
  };

  return <div className="pm-industry" aria-busy={result.loading}>
    <section className="pm-industry-tools" aria-label={text('Find materials by industry', 'ابحث عن المواد حسب القطاع')}>
      <label className="pm-industry-search"><span className="sr-only">{text('Search materials, categories or products', 'ابحث عن المواد أو الفئات أو المنتجات')}</span>
        <input type="search" maxLength={100} value={input} onChange={event => setInput(event.target.value)} placeholder={text('Search materials, categories or products...', 'ابحث عن المواد أو الفئات أو المنتجات...')}/>
      </label>
      <div className="pm-industry-tabs" role="group" aria-label={text('Select a discipline', 'اختر التخصص')}>
        {supported.map(name => {
          const stats = disciplineData.find(item => item.id.toLowerCase() === name.toLowerCase());
          const label = arabic ? stats?.name_ar || name : name;
          const Icon = name === 'Electrical' ? IconBolt : name === 'Mechanical' ? IconSettings : IconBuilding;
          return <button type="button" key={name} aria-pressed={discipline === name} className={discipline === name ? 'is-active' : ''} onClick={() => {setDiscipline(name);setPage(1);}}>
            <Icon size={19}/><span>{label}</span><small>{stats?.category_count ?? 0}</small>
          </button>;
        })}
      </div>
      <div className="pm-industry-results" role="status" aria-live="polite">
        <span>{result.loading ? text('Loading categories…', 'جارٍ تحميل الفئات…') : `${result.data?.total ?? 0} ${text('categories', 'فئات')}${selectedCount ? ` · ${selectedCount.material_count} ${text('materials', 'مواد')}` : ''}`}</span>
        {(input || discipline !== 'Civil') && <button type="button" onClick={clear}>{text('Clear filters', 'مسح عوامل التصفية')}</button>}
      </div>
    </section>

    {result.error ? <div className="pm-industry-state" role="alert"><h2>{text('Categories could not be loaded', 'تعذر تحميل الفئات')}</h2><p>{text('Try again in a moment.', 'يرجى المحاولة مرة أخرى.')}</p><button type="button" onClick={result.reload}>{text('Retry', 'إعادة المحاولة')}</button></div>
      : result.loading && !result.data ? <div className="pm-industry-grid" aria-hidden="true">{[0,1,2].map(item => <div className="pm-industry-card pm-industry-skeleton" key={item}><i/><i/><i/><i/></div>)}</div>
      : !categories.length ? <div className="pm-industry-state"><span>{icon}</span><h2>{text('No matching categories', 'لا توجد فئات مطابقة')}</h2><p>{input ? text('Try a different material or category term.', 'جرّب كلمة أخرى للبحث عن مادة أو فئة.') : text('No active Material Master categories are assigned to this discipline yet.', 'لا توجد فئات نشطة مرتبطة بهذا التخصص حالياً.')}</p>{input && <button type="button" onClick={clear}>{text('Clear filters', 'مسح عوامل التصفية')}</button>}</div>
      : <div className="pm-industry-groups">{groups.map(group => <section className="pm-industry-group" key={group.id}>
        <header><div><span className="pm-industry-group-icon">{icon}</span><div><h2>{group.name}</h2><p>{text('Main category', 'الفئة الرئيسية')}</p></div></div><span className="pm-industry-group-count">{group.cards.length} {text('subcategories', 'فئات فرعية')}</span></header>
        <div className="pm-industry-grid">{group.cards.map(card => {
          const title = arabic && card.name_ar ? card.name_ar : card.name_en;
          const key = `${discipline}:${card.id}`;
          const preview = card.materials || [];
          const more = Math.max(0, card.material_count - preview.length);
          return <article className="pm-industry-card" key={key}>
            <div className="pm-industry-card-top"><span>{icon}</span><small>{card.material_count} {text('materials', 'مواد')}</small></div>
            <h3><Link href={linkFor(card)}>{title}</Link></h3>
            <p className="pm-industry-card-path">{group.name}{card.subcategory_id ? ` / ${title}` : ''}</p>
            {!!preview.length && <><h4>{text('Material preview', 'نماذج المواد')}</h4><ul className="pm-industry-materials">{preview.map(material => {
              const materialName = arabic && material.name_ar ? material.name_ar : material.name_en;
              const description = arabic && material.description_ar ? material.description_ar : material.description;
              return <li key={material.id}><span><IconCheck size={15}/></span><div><strong dir="auto">{materialName}</strong>
                {description && description !== material.name_en && description !== material.name_ar && <p dir="auto">{description}</p>}</div>{material.unit && <small>{material.unit}</small>}
              </li>;
            })}</ul></>}
            {more > 0 && <details className="pm-industry-more"><summary>{text(`+${more} more materials`, `+${more} مواد إضافية`)}</summary><ExpandedMaterials category={card} discipline={discipline} locale={locale} text={text}/></details>}
            <Link className="pm-industry-explore" href={linkFor(card)}>{text('Explore Suppliers', 'استكشف الموردين')}<IconArrowRight className="pm-arrow" size={18}/></Link>
          </article>;
        })}</div>
      </section>)}</div>}
    {result.data && result.data.total > result.data.limit && <nav className="pm-industry-pagination" aria-label={text('Category pages', 'صفحات الفئات')}>
      <button type="button" disabled={page <= 1 || result.loading} onClick={() => setPage(value => value - 1)}>{text('Previous', 'السابق')}</button><span>{page} / {Math.ceil(result.data.total / result.data.limit)}</span>
      <button type="button" disabled={page >= Math.ceil(result.data.total / result.data.limit) || result.loading} onClick={() => setPage(value => value + 1)}>{text('Next', 'التالي')}</button>
    </nav>}
    <section className="pm-industry-cta"><div><span>{text('Start your next procurement search', 'ابدأ بحثك عن متطلبات مشروعك')}</span><h2>{text('Find suppliers for your material requirements.', 'اعثر على موردين لمتطلبات المواد الخاصة بك.')}</h2><p>{text('Explore relevant suppliers by the same Material Master categories.', 'استكشف الموردين ذوي الصلة حسب فئات دليل المواد.')}</p></div><MarketingCTA role="buyer">{text('Create an RFQ', 'إنشاء طلب عرض سعر')}</MarketingCTA></section>
  </div>;
}
