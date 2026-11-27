"use client";

import { useEffect, useMemo, useRef, useState } from 'react';
import { api, json } from '@/lib/api/client';
import type { Schemas } from '@/lib/api/generated/schema';
import { useLocale } from '@/lib/locale';
import { Button } from '@/components/ui/button';
import type { MaterialLine } from './material-row';
import { keepBuyerDescription } from './buyer-description';
import './grouped-rfq-materials.css';

type ClassificationResult = {
  material_id: string | null;
  material_code: string | null;
  material_name: string | null;
  main_category_id: string | null;
  subcategory_id: string | null;
  main_category_name: string | null;
  subcategory_name: string | null;
  suggested_unit: string | null;
  confidence: number;
  match_source: string;
  explanation: string;
  needs_confirmation: boolean;
  ai_used: boolean;
  fallback_used: boolean;
};

type AutocompleteMaterial = {
  id: string;
  code?: string;
  name_en: string;
  name_ar?: string | null;
  unit?: string | null;
  main_category_id?: string | null;
  subcategory_id?: string | null;
  category_id?: string | null;
};

export function QuickMaterialEntry({
  categories,
  units,
  onAdd,
  onOpenImport,
}: {
  categories: Schemas['CategoryOut'][];
  units: { code: string; name_en: string }[];
  onAdd: (line: MaterialLine) => void;
  onOpenImport: () => void;
}) {
  const { t, arabic } = useLocale();
  const w = (en: string, ar: string) => (arabic ? ar : t(en));

  const [description, setDescription] = useState('');
  const [specifications, setSpecifications] = useState('');
  const [unit, setUnit] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [materialId, setMaterialId] = useState<string | null>(null);
  const [mainCategoryId, setMainCategoryId] = useState('');
  const [subcategoryId, setSubcategoryId] = useState('');
  const [manualCategoryOverride, setManualCategoryOverride] = useState(false);

  const [suggestions, setSuggestions] = useState<AutocompleteMaterial[]>([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [classifying, setClassifying] = useState(false);
  const [classification, setClassification] = useState<ClassificationResult | null>(null);
  const [localError, setLocalError] = useState('');

  const classifySeq = useRef(0);

  const roots = useMemo(
    () => categories.filter((c) => !c.parent_id).sort((a, b) => a.name_en.localeCompare(b.name_en)),
    [categories],
  );
  const subcategories = useMemo(
    () =>
      categories
        .filter((c) => c.parent_id === mainCategoryId)
        .sort((a, b) => a.name_en.localeCompare(b.name_en)),
    [categories, mainCategoryId],
  );

  // Default unit once approved units load
  useEffect(() => {
    if (!unit && units.length) {
      const preferred = units.find((u) => u.code === 'NO.') || units[0];
      setUnit(preferred.code);
    }
  }, [unit, units]);

  // Debounced Material Master autocomplete across all categories
  useEffect(() => {
    const q = description.trim();
    if (q.length < 2 || materialId) {
      setSuggestions([]);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const res = await api<AutocompleteMaterial[]>(
          `taxonomy/materials?q=${encodeURIComponent(q)}&limit=12&autocomplete=true`,
        );
        setSuggestions(Array.isArray(res) ? res : []);
      } catch {
        setSuggestions([]);
      }
    }, 220);
    return () => clearTimeout(timer);
  }, [description, materialId]);

  // Debounced automatic category/subcategory suggestion for custom descriptions
  useEffect(() => {
    const q = description.trim();
    if (q.length < 3 || materialId || manualCategoryOverride) {
      return;
    }
    const seq = ++classifySeq.current;
    const timer = setTimeout(async () => {
      setClassifying(true);
      try {
        const result = await api<ClassificationResult>('rfqs/classify-material', {
          method: 'POST',
          body: json({
            description: q,
            specifications: specifications.trim(),
            unit: unit || null,
            ai_enabled: true,
          }),
        });
        if (seq !== classifySeq.current) return;
        setClassification(result);
        if (result.main_category_id) {
          setMainCategoryId(result.main_category_id);
          setSubcategoryId(result.subcategory_id || '');
        }
        if (result.suggested_unit && units.some((u) => u.code === result.suggested_unit)) {
          setUnit(result.suggested_unit);
        }
      } catch {
        // Keep manual selection available even if classification fails
      } finally {
        if (seq === classifySeq.current) setClassifying(false);
      }
    }, 450);
    return () => clearTimeout(timer);
  }, [description, specifications, materialId, manualCategoryOverride, unit, units]);

  function selectAutocomplete(item: AutocompleteMaterial) {
    const byId = new Map(categories.map((c) => [c.id, c]));
    const leaf = item.category_id ? byId.get(item.category_id) : undefined;
    const resolvedMain = item.main_category_id || (leaf?.parent_id ? leaf.parent_id : leaf?.id) || '';
    const resolvedSub = item.subcategory_id || (leaf?.parent_id ? leaf.id : '') || '';

    setDescription(keepBuyerDescription(description, arabic && item.name_ar ? item.name_ar : item.name_en));
    setMaterialId(item.id);
    setMainCategoryId(resolvedMain);
    setSubcategoryId(resolvedSub);
    setManualCategoryOverride(false);
    if (item.unit && units.some((u) => u.code === item.unit)) {
      setUnit(item.unit);
    }
    setClassification({
      material_id: item.id,
      material_code: item.code || null,
      material_name: item.name_en,
      main_category_id: resolvedMain || null,
      subcategory_id: resolvedSub || null,
      main_category_name: byId.get(resolvedMain)?.name_en || null,
      subcategory_name: byId.get(resolvedSub)?.name_en || null,
      suggested_unit: item.unit || null,
      confidence: 1,
      match_source: 'exact_material',
      explanation: w('Selected from Material Master.', 'تم الاختيار من دليل المواد.'),
      needs_confirmation: false,
      ai_used: false,
      fallback_used: false,
    });
    setSuggestions([]);
    setDropdownOpen(false);
    setLocalError('');
  }

  function handleAdd() {
    const desc = description.trim();
    if (desc.length < 3) {
      setLocalError(w('Enter a material description with at least 3 characters.', 'أدخل وصف المادة (3 أحرف على الأقل).'));
      return;
    }
    if (!mainCategoryId) {
      setLocalError(w('Select or confirm a main category.', 'يرجى اختيار أو تأكيد الفئة الرئيسية.'));
      return;
    }
    if (subcategories.length > 0 && !subcategoryId) {
      setLocalError(w('Select or confirm a subcategory.', 'يرجى اختيار أو تأكيد الفئة الفرعية.'));
      return;
    }
    if (!unit || !units.some((u) => u.code === unit)) {
      setLocalError(w('Choose an approved unit.', 'اختر وحدة قياس معتمدة.'));
      return;
    }
    const qtyNum = Number(quantity);
    if (!Number.isFinite(qtyNum) || qtyNum <= 0) {
      setLocalError(w('Quantity must be greater than zero.', 'يجب أن تكون الكمية أكبر من صفر.'));
      return;
    }

    onAdd({
      key: `rfq-line:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`,
      material_id: materialId,
      main_category_id: mainCategoryId,
      subcategory_id: subcategoryId || null,
      description: desc,
      specifications: specifications.trim(),
      unit,
      quantity: String(quantity).trim(),
    });

    // Reset for fast sequential entry
    setDescription('');
    setSpecifications('');
    setQuantity('1');
    setMaterialId(null);
    setMainCategoryId('');
    setSubcategoryId('');
    setManualCategoryOverride(false);
    setClassification(null);
    setSuggestions([]);
    setLocalError('');
  }

  const labelFor = (c: Schemas['CategoryOut']) => (arabic && c.name_ar ? c.name_ar : c.name_en);

  return (
    <section className="quick-entry-card" dir={arabic ? 'rtl' : 'ltr'} aria-label={w('Add material directly', 'إضافة مادة مباشرة')}>
      <div className="quick-entry-header">
        <div>
          <h3>{w('Add materials by description', 'إضافة المواد عبر الوصف مباشرة')}</h3>
          <small>
            {w(
              'Type any material description or code. We suggest Material Master matches and categories automatically—you can edit or add custom items anytime.',
              'اكتب وصف المادة أو رمزها مباشرة. سنقترح المطابقات والفئات تلقائياً ويمكنك تعديلها أو إضافة مواد مخصصة في أي وقت.',
            )}
          </small>
        </div>
        <Button type="button" variant="outline" onClick={onOpenImport}>
          {w('Import from Excel / CSV', 'استيراد من Excel / CSV')}
        </Button>
      </div>

      <div className="quick-entry-grid">
        <div className="quick-entry-desc-wrap">
          <label className="field">
            {w('Material description or code', 'وصف المادة أو الرمز')}
            <input
              type="text"
              dir="auto"
              value={description}
              placeholder={w('e.g. Centrifugal Booster Pump 5kW, Sand Media Water Filter...', 'مثال: مضخة مياه 5 كيلوواط، فلتر مياه رملي...')}
              onFocus={() => setDropdownOpen(true)}
              onBlur={() => setTimeout(() => setDropdownOpen(false), 180)}
              onChange={(e) => {
                setDescription(e.target.value);
                setMaterialId(null);
                setManualCategoryOverride(false);
                setDropdownOpen(true);
                setLocalError('');
              }}
            />
          </label>
          {dropdownOpen && suggestions.length > 0 && (
            <ul className="quick-entry-dropdown" role="listbox" aria-label={w('Material Master suggestions', 'اقتراحات دليل المواد')}>
              {suggestions.map((item) => (
                <li key={item.id}>
                  <button type="button" onMouseDown={() => selectAutocomplete(item)}>
                    <strong>{arabic && item.name_ar ? item.name_ar : item.name_en}</strong>
                    <small>
                      {[item.code, item.unit].filter(Boolean).join(' · ')}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <label className="field">
          {w('Specifications (optional)', 'المواصفات الفنية (اختياري)')}
          <input
            type="text"
            dir="auto"
            maxLength={500}
            value={specifications}
            placeholder={w('Size, pressure, brand, standard...', 'المقاس، الضغط، العلامة التجارية، المواصفة...')}
            onChange={(e) => setSpecifications(e.target.value)}
          />
        </label>

        <label className="field">
          {w('Unit', 'الوحدة')}
          <select value={unit} onChange={(e) => setUnit(e.target.value)}>
            <option value="">{w('Select unit', 'اختر الوحدة')}</option>
            {units.map((u) => (
              <option key={u.code} value={u.code}>
                {u.code} — {u.name_en}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          {w('Quantity', 'الكمية')}
          <input
            type="number"
            min="0.001"
            step="0.001"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
          />
        </label>

        <label className="field">
          {w('Main category (auto-suggested, editable)', 'الفئة الرئيسية (مقترحة تلقائياً وقابلة للتعديل)')}
          <select
            value={mainCategoryId}
            onChange={(e) => {
              setMainCategoryId(e.target.value);
              setSubcategoryId('');
              setMaterialId(null);
              setManualCategoryOverride(true);
              setLocalError('');
            }}
          >
            <option value="">{w('Select main category', 'اختر الفئة الرئيسية')}</option>
            {roots.map((c) => (
              <option key={c.id} value={c.id}>
                {labelFor(c)}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          {w('Subcategory (auto-suggested, editable)', 'الفئة الفرعية (مقترحة تلقائياً وقابلة للتعديل)')}
          <select
            value={subcategoryId}
            disabled={!mainCategoryId || !subcategories.length}
            onChange={(e) => {
              setSubcategoryId(e.target.value);
              setMaterialId(null);
              setManualCategoryOverride(true);
              setLocalError('');
            }}
          >
            <option value="">{w('Select subcategory', 'اختر الفئة الفرعية')}</option>
            {subcategories.map((c) => (
              <option key={c.id} value={c.id}>
                {labelFor(c)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {classifying && (
        <p className="rfq-grouped-summary" role="status">
          {w('Suggesting category from Material Master...', 'جارٍ اقتراح الفئة من دليل المواد...')}
        </p>
      )}

      {classification && !classifying && (
        <div
          className="quick-entry-suggestion"
          data-confirm={classification.needs_confirmation ? 'true' : 'false'}
          role="status"
        >
          <strong>
            {classification.material_id
              ? w('Material Master match', 'مطابقة دليل المواد')
              : classification.needs_confirmation
                ? w('Please confirm category', 'يرجى تأكيد الفئة')
                : w('Suggested category', 'الفئة المقترحة')}
          </strong>
          <span>
            {classification.explanation}
            {classification.confidence > 0 ? ` (${Math.round(classification.confidence * 100)}%)` : ''}
          </span>
          {classification.ai_used && <span className="diag-badge" data-tone="ai">AI</span>}
        </div>
      )}

      {localError && (
        <p className="notice" role="alert">
          {localError}
        </p>
      )}

      <div className="form-actions">
        <Button type="button" onClick={handleAdd}>
          {w('Add material to RFQ', 'إضافة المادة إلى الطلب')}
        </Button>
      </div>
    </section>
  );
}
