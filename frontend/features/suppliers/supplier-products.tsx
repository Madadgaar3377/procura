"use client";

import {useEffect, useMemo, useState} from 'react';
import Link from 'next/link';
import {useApi} from '@/lib/api/use-api';
import {useLocale} from '@/lib/locale';
import type {Schemas} from '@/lib/api/generated/schema';

type Group = Schemas['PublicCapabilityGroup'];
type Product = {
  id: string;
  name: string;
  name_ar: string;
  description: string;
  description_ar: string;
  brand: string;
  model: string;
  unit: string | null;
  specifications: Record<string, unknown>;
  main_category_id: string;
  main_category: string;
  main_category_ar: string;
  subcategory_id: string | null;
  subcategory: string | null;
  subcategory_ar: string;
  source: 'material_master' | 'supplier_description';
};
type Page = {items: Product[]; total: number; page: number; limit: number};

export default function SupplierProducts({id, groups}: {id: string; groups: Group[]}) {
  const {arabic} = useLocale();
  const tr = (en: string, ar: string) => arabic ? ar : en;
  const [input, setInput] = useState('');
  const [term, setTerm] = useState('');
  const [main, setMain] = useState('');
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<string[]>([]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setTerm(input.trim());
      setPage(1);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [input]);

  const params = new URLSearchParams({q: term, page: String(page), limit: '20'});
  if (main) params.set('main_category_id', main);
  const result = useApi<Page>(`directory/suppliers/${encodeURIComponent(id)}/products?${params}`);
  const categories = useMemo(() => {
    const values = new Map<string, {name: string; groups: Map<string, {name: string; rows: Product[]}>}>();
    for (const item of result.data?.items || []) {
      const category = values.get(item.main_category_id) || {name: arabic && item.main_category_ar ? item.main_category_ar : item.main_category, groups: new Map()};
      const key = item.subcategory_id || item.main_category_id;
      const subcategory = category.groups.get(key) || {name: arabic && item.subcategory_ar ? item.subcategory_ar : item.subcategory || item.main_category, rows: []};
      subcategory.rows.push(item);
      category.groups.set(key, subcategory);
      values.set(item.main_category_id, category);
    }
    return [...values.entries()];
  }, [result.data?.items, arabic]);
  const categoryName = (group: Group) => arabic && group.name_ar ? group.name_ar : group.name_en;
  const keys = categories.flatMap(([id, category]) => [id, ...category.groups.keys()]);

  return <section className="sp-panel sp-products" id="supply-capabilities">
    <div className="sp-section-heading">
      <div><span className="sp-eyebrow">{tr('SUPPLY CAPABILITIES', 'قدرات التوريد')}</span><h2>{tr('Products & Services', 'المنتجات والخدمات')}</h2></div>
      <span className="sp-count">{groups.length} {tr('main categories', 'فئات رئيسية')}</span>
    </div>
    <div className="sp-product-filters">
      {groups.length > 1 && <label>{tr('Main Category', 'الفئة الرئيسية')}
        <select value={main} onChange={event => {setMain(event.target.value); setPage(1);}}>
          <option value="">{tr('All Categories', 'كل الفئات')}</option>
          {groups.map(group => <option key={group.id} value={group.id}>{categoryName(group)}</option>)}
        </select>
      </label>}
      <label className="sp-product-search">{tr('Search this supplier’s products, categories or brands...', 'ابحث في منتجات المورد أو فئاته أو علاماته التجارية')}
        <input type="search" maxLength={100} value={input} onChange={event => setInput(event.target.value)} placeholder={tr('Search this supplier’s products, categories or brands...', 'ابحث في منتجات المورد أو فئاته أو علاماته التجارية')} />
      </label>
    </div>
    <nav className="sp-published-categories" aria-label={tr('Supply categories', 'فئات التوريد')}>
      {groups.flatMap(group => (group.subcategories || []).map(sub => <Link key={sub.id} href={`/suppliers?main_category_id=${encodeURIComponent(group.id)}&subcategory_id=${encodeURIComponent(sub.id)}&lang=${arabic ? 'ar' : 'en'}`}>
        {categoryName(group)} / {arabic && sub.name_ar ? sub.name_ar : sub.name_en}
      </Link>))}
    </nav>
    <div className="sp-product-actions">
      <span role="status" aria-live="polite">{result.loading ? tr('Searching…', 'جارٍ البحث…') : `${result.data?.total ?? 0} ${tr('matching records', 'سجلات مطابقة')}`}</span>
      <button type="button" onClick={() => setExpanded(keys)}>{tr('Expand All', 'توسيع الكل')}</button>
      <button type="button" onClick={() => setExpanded([])}>{tr('Collapse All', 'طي الكل')}</button>
      {(input || main) && <button type="button" onClick={() => {setInput(''); setTerm(''); setMain(''); setPage(1);}}>{tr('Clear Filters', 'مسح عوامل التصفية')}</button>}
    </div>
    {result.error ? <p role="alert" className="sp-muted">{tr('Products could not be loaded. Retry the page.', 'تعذر تحميل المنتجات. أعد تحميل الصفحة.')}</p>
      : !result.loading && !result.data?.total ? <p className="sp-muted">{input ? tr('No products match your search.', 'لا توجد منتجات تطابق بحثك.') : tr('No product details have been provided.', 'لم يتم توفير تفاصيل المنتجات.')}</p>
      : <div className="sp-product-groups">{categories.map(([categoryId, category]) => <section className="sp-product-category" key={categoryId}>
        <h3>{category.name}</h3>
        {[...category.groups.entries()].map(([key, group]) => <details className="sp-product-subcategory" key={key} open={expanded.includes(key) || expanded.includes(categoryId)}>
          <summary>{group.name}<span className="sp-count">{group.rows.length} {tr('items', 'منتجات')}</span></summary>
          <div className="sp-product-list">{group.rows.map(item => <article className="sp-product-row" key={item.id}>
            <h4 dir="auto">{arabic && item.name_ar ? item.name_ar : item.name}</h4>
            {item.source === 'supplier_description' && <span className="sp-unstructured-label">{tr('Supplier provided description', 'وصف مقدم من المورد')}</span>}
            {item.brand && <span className="sp-product-brand">{tr('Brand', 'العلامة التجارية')}: {item.brand}</span>}
            {item.model && <span className="sp-product-model">{tr('Model', 'الطراز')}: {item.model}</span>}
            {(arabic ? item.description_ar || item.description : item.description) && <p dir="auto">{arabic ? item.description_ar || item.description : item.description}</p>}
            {item.unit && <span className="sp-product-unit">{tr('Unit', 'الوحدة')}: {item.unit}</span>}
            {Object.keys(item.specifications || {}).length > 0 && <dl>{Object.entries(item.specifications).map(([name, value]) => <div key={name}><dt>{name}</dt><dd dir="auto">{String(value)}</dd></div>)}</dl>}
          </article>)}</div>
        </details>)}
      </section>)}</div>}
    {result.data && result.data.total > result.data.limit && <nav className="sp-product-pagination" aria-label={tr('Product pages', 'صفحات المنتجات')}>
      <button type="button" disabled={page <= 1 || result.loading} onClick={() => setPage(value => value - 1)}>{tr('Previous', 'السابق')}</button>
      <span>{page} / {Math.ceil(result.data.total / result.data.limit)}</span>
      <button type="button" disabled={page >= Math.ceil(result.data.total / result.data.limit) || result.loading} onClick={() => setPage(value => value + 1)}>{tr('Next', 'التالي')}</button>
    </nav>}
  </section>;
}
