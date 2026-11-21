"use client";

import { useMemo, useState, type ReactNode } from 'react';
import { apiUrl } from '@/lib/api/client';
import type { Schemas } from '@/lib/api/generated/schema';
import { useLocale } from '@/lib/locale';
import { Button } from '@/components/ui/button';
import './grouped-rfq-materials.css';

export type GroupableRfqItem = {
  id?: string;
  key?: string;
  material_id?: string | null;
  main_category_id?: string | null;
  subcategory_id?: string | null;
  description: string;
  specifications?: string | null;
  unit: string;
  quantity: string | number;
};

export type GroupedSubcategory<T extends GroupableRfqItem> = {
  subcategory_id: string | null;
  subcategory_name: string;
  items: { item: T; originalIndex: number }[];
};

export type GroupedMainCategory<T extends GroupableRfqItem> = {
  key: string;
  main_category_id: string | null;
  main_category_name: string;
  totalItems: number;
  subcategories: GroupedSubcategory<T>[];
};

export function categoryDisplayName(
  category: Schemas['CategoryOut'] | undefined,
  arabic: boolean,
  fallback: string,
): string {
  if (!category) return fallback;
  if (arabic && category.name_ar && category.name_ar.trim()) return category.name_ar;
  return category.name_en || fallback;
}

/**
 * Group RFQ items strictly by Main Category -> Subcategory -> Materials,
 * displaying each category heading once while preserving original item IDs and indexes.
 */
export function groupRfqItems<T extends GroupableRfqItem>(
  items: T[],
  categories: Schemas['CategoryOut'][],
  arabic = false,
): GroupedMainCategory<T>[] {
  const byId = new Map(categories.map((c) => [c.id, c]));
  const mainMap = new Map<string, {
    key: string;
    main_category_id: string | null;
    main_category_name: string;
    totalItems: number;
    subMap: Map<string, GroupedSubcategory<T>>;
  }>();

  items.forEach((item, originalIndex) => {
    const mainKey = item.main_category_id || '__unclassified__';
    const subKey = item.subcategory_id || '__general__';
    let mainGroup = mainMap.get(mainKey);
    if (!mainGroup) {
      mainGroup = {
        key: mainKey,
        main_category_id: item.main_category_id || null,
        main_category_name: categoryDisplayName(
          item.main_category_id ? byId.get(item.main_category_id) : undefined,
          arabic,
          arabic ? 'غير مصنف' : 'Unclassified',
        ),
        totalItems: 0,
        subMap: new Map(),
      };
      mainMap.set(mainKey, mainGroup);
    }
    let subGroup = mainGroup.subMap.get(subKey);
    if (!subGroup) {
      subGroup = {
        subcategory_id: item.subcategory_id || null,
        subcategory_name: categoryDisplayName(
          item.subcategory_id ? byId.get(item.subcategory_id) : undefined,
          arabic,
          arabic ? 'عام' : 'General',
        ),
        items: [],
      };
      mainGroup.subMap.set(subKey, subGroup);
    }
    subGroup.items.push({ item, originalIndex });
    mainGroup.totalItems += 1;
  });

  return Array.from(mainMap.values()).map((group) => ({
    key: group.key,
    main_category_id: group.main_category_id,
    main_category_name: group.main_category_name,
    totalItems: group.totalItems,
    subcategories: Array.from(group.subMap.values()),
  }));
}

export function GroupedRfqMaterials<T extends GroupableRfqItem>({
  items,
  categories,
  renderItem,
  emptyMessage,
}: {
  items: T[];
  categories: Schemas['CategoryOut'][];
  renderItem?: (item: T, originalIndex: number) => ReactNode;
  emptyMessage?: string;
}) {
  const { t, arabic } = useLocale();
  const w = (en: string, ar: string) => (arabic ? ar : t(en));
  const groups = useMemo(() => groupRfqItems(items, categories, arabic), [items, categories, arabic]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  if (!items.length) {
    return emptyMessage ? <p className="rfq-grouped-summary">{emptyMessage}</p> : null;
  }

  const allCollapsed = groups.length > 0 && groups.every((g) => collapsed[g.key]);

  return (
    <div className="rfq-grouped-wrap" dir={arabic ? 'rtl' : 'ltr'}>
      <div className="rfq-grouped-toolbar">
        <p className="rfq-grouped-summary" role="status">
          {items.length} {w('materials', 'مواد')} · {groups.length} {w('main categories', 'فئات رئيسية')}
        </p>
        {groups.length > 1 && (
          <div className="rfq-grouped-controls">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                const next: Record<string, boolean> = {};
                groups.forEach((g) => {
                  next[g.key] = !allCollapsed;
                });
                setCollapsed(next);
              }}
            >
              {allCollapsed ? w('Expand all categories', 'توسيع جميع الفئات') : w('Collapse all categories', 'طي جميع الفئات')}
            </Button>
          </div>
        )}
      </div>

      {groups.map((mainGroup) => {
        const isCollapsed = Boolean(collapsed[mainGroup.key]);
        return (
          <section className="rfq-cat-card" key={mainGroup.key} aria-label={mainGroup.main_category_name}>
            <button
              type="button"
              className="rfq-cat-header"
              aria-expanded={!isCollapsed}
              onClick={() => setCollapsed((prev) => ({ ...prev, [mainGroup.key]: !prev[mainGroup.key] }))}
            >
              <span>{mainGroup.main_category_name}</span>
              <span className="rfq-cat-header-meta">
                <span>
                  {mainGroup.totalItems} {w('items', 'عناصر')}
                </span>
                <span aria-hidden="true">{isCollapsed ? '▸' : '▾'}</span>
              </span>
            </button>

            {!isCollapsed && (
              <div className="rfq-cat-body">
                {mainGroup.subcategories.map((subGroup) => (
                  <div className="rfq-subcat-block" key={subGroup.subcategory_id || '__general__'}>
                    <div className="rfq-subcat-header">
                      <span>{subGroup.subcategory_name}</span>
                      <span className="rfq-subcat-count">
                        {subGroup.items.length} {w('materials', 'مواد')}
                      </span>
                    </div>
                    <div className="rfq-subcat-items">
                      {renderItem ? (
                        subGroup.items.map(({ item, originalIndex }) => (
                          <div key={item.id || item.key || originalIndex}>
                            {renderItem(item, originalIndex)}
                          </div>
                        ))
                      ) : (
                        <div className="table-wrap">
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th>{w('Description', 'الوصف')}</th>
                                <th>{w('Specifications', 'المواصفات')}</th>
                                <th>{w('Unit', 'الوحدة')}</th>
                                <th>{w('Quantity', 'الكمية')}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {subGroup.items.map(({ item, originalIndex }) => (
                                <tr key={item.id || item.key || originalIndex}>
                                  <td>{item.description}</td>
                                  <td>{item.specifications || '—'}</td>
                                  <td>{item.unit}</td>
                                  <td>{item.quantity}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

export function RfqExportActions({ rfqId }: { rfqId: string }) {
  const { t, arabic } = useLocale();
  const w = (en: string, ar: string) => (arabic ? ar : t(en));
  const [busy, setBusy] = useState<'pdf' | 'xlsx' | null>(null);
  const [error, setError] = useState('');

  async function download(format: 'pdf' | 'xlsx') {
    setBusy(format);
    setError('');
    try {
      const lang = arabic ? 'ar' : 'en';
      const response = await fetch(apiUrl(`rfqs/${rfqId}/export.${format}?lang=${lang}`), {
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error(w('Could not export RFQ', 'تعذر تصدير طلب عرض الأسعار'));
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `rfq-${rfqId}.${format}`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : w('Export failed', 'فشل التصدير'));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rfq-export-bar">
      <Button type="button" variant="outline" disabled={busy !== null} onClick={() => void download('pdf')}>
        {busy === 'pdf' ? w('Exporting PDF...', 'جارٍ تصدير PDF...') : w('Export PDF', 'تصدير PDF')}
      </Button>
      <Button type="button" variant="outline" disabled={busy !== null} onClick={() => void download('xlsx')}>
        {busy === 'xlsx' ? w('Exporting Excel...', 'جارٍ تصدير Excel...') : w('Export Excel', 'تصدير Excel')}
      </Button>
      {error && <span role="alert">{error}</span>}
    </div>
  );
}
