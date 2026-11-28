"use client";

import { useEffect, useId, useRef, useState } from 'react';
import type { Schemas } from '@/lib/api/generated/schema';
import { api, json } from '@/lib/api/client';
import { useApi } from '@/lib/api/use-api';
import { useLocale } from '@/lib/locale';
import { SearchableSelect } from '@/features/shared/searchable-select';
import { FieldError } from '@/features/shared/field-error';
import { RetryButton } from '@/features/shared/loading';
import { Button } from '@/components/ui/button';
import { keepBuyerDescription } from './buyer-description';

export type MaterialLine = Schemas['RFQLineIn'] & { key: string; id?: string };

export function MaterialRow({ line, index, categories, units, onChange, onRemove, loadMaterialDetails = true, errors = {} }: {
  line: MaterialLine; index: number; categories: Schemas['CategoryOut'][];
  units: { code: string; name_en: string }[];
  onChange: (line: MaterialLine) => void; onRemove: () => void;
  loadMaterialDetails?: boolean; errors?: Record<string, string>;
}) {
  const { t, arabic } = useLocale();
  const id = useId();
  const [term, setTerm] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [page, setPage] = useState({ scope: '', offset: 0 });
  const [manualCategoryOverride, setManualCategoryOverride] = useState(false);
  const classificationSeq = useRef(0);
  const initialDescription = useRef(line.description.trim());
  const latestLine = useRef(line);
  latestLine.current = line;

  useEffect(() => { const timer = setTimeout(() => { setTerm(line.description.trim()); setActive(0); }, 250); return () => clearTimeout(timer); }, [line.description]);

  // Restore the custom-material classification used by the former quick-entry form.
  useEffect(() => {
    const description = line.description.trim();
    if (description === initialDescription.current || description.length < 3 || line.material_id || manualCategoryOverride || !categories.length) return;
    const sequence = ++classificationSeq.current;
    const timer = setTimeout(async () => {
      try {
        const match = await api<{ main_category_id: string | null; subcategory_id: string | null; suggested_unit: string | null }>('rfqs/classify-material', {
          method: 'POST', body: json({ description, specifications: line.specifications?.trim() || '', unit: line.unit || null, ai_enabled: true }),
        });
        if (sequence !== classificationSeq.current || !match.main_category_id) return;
        const subcategory = match.subcategory_id && categories.some(c => c.id === match.subcategory_id && c.parent_id === match.main_category_id) ? match.subcategory_id : null;
        const current = latestLine.current;
        onChange({ ...current, main_category_id: match.main_category_id, subcategory_id: subcategory,
          unit: current.unit === line.unit && match.suggested_unit && units.some(u => u.code === match.suggested_unit) ? match.suggested_unit : current.unit });
      } catch { /* Manual category selection remains available. */ }
    }, 450);
    return () => { classificationSeq.current++; clearTimeout(timer); };
  // Keep buyer category and unit edits intact until the description changes.
  }, [line.description, line.material_id, manualCategoryOverride, categories, units]);

  const children = categories.filter(c => c.parent_id === line.main_category_id);
  const scope = [line.main_category_id, line.subcategory_id, term].join('|');
  const offset = page.scope === scope ? page.offset : 0;
  const params = new URLSearchParams({ q: term.slice(0, 100), offset: String(offset), limit: '20', autocomplete: 'true' });
  if (line.main_category_id) params.set('main_category_id', line.main_category_id);
  if (line.subcategory_id) params.set('subcategory_id', line.subcategory_id);
  const result = useApi<Schemas['MaterialOut'][]>(open && term.length >= 2 && term === line.description.trim() ? `taxonomy/materials?${params}` : null);
  const selected = useApi<Schemas['MaterialOut']>(line.material_id && loadMaterialDetails && (!line.main_category_id || !line.unit || !line.discipline) ? `taxonomy/materials/${line.material_id}` : null);
  const suggestions = result.data || [];
  useEffect(() => { if (open) document.getElementById(`${id}-${active}`)?.scrollIntoView({ block: 'nearest' }); }, [active, id, open]);
  useEffect(() => {
    const m = selected.data;
    if (!m || !categories.length || (line.main_category_id && line.unit && line.discipline)) return;
    const leaf = categories.find(c => c.id === m.category_id);
    const main = m.main_category_id || leaf?.parent_id || m.category_id;
    const sub = m.subcategory_id || (leaf?.parent_id ? leaf.id : null);
    onChange({ ...line, main_category_id: line.main_category_id || main, subcategory_id: line.subcategory_id || sub, unit: line.unit || m.unit, discipline: line.discipline || m.discipline || '' });
  }, [selected.data, categories, line, onChange]);

  const label = (c: Schemas['CategoryOut']) => arabic && c.name_ar ? c.name_ar : c.name_en;
  const error = (field: string) => errors[`items.${index}.${field}`];
  function choose(m: Schemas['MaterialOut']) {
    classificationSeq.current++;
    const leaf = categories.find(c => c.id === m.category_id);
    onChange({ ...line, material_id: m.id, description: keepBuyerDescription(line.description, (arabic && (m.arabic_description || m.name_ar)) || m.name_en),
      unit: m.unit || line.unit, discipline: m.discipline || line.discipline || '', main_category_id: m.main_category_id || leaf?.parent_id || m.category_id,
      subcategory_id: m.subcategory_id || (leaf?.parent_id ? leaf.id : null) });
    setOpen(false); setManualCategoryOverride(false); setPage({ scope: '', offset: 0 });
  }

  return <fieldset className="rfq-material-row" dir={arabic ? 'rtl' : 'ltr'}>
    <legend>{t('Material')} {index + 1}</legend>
    <Button type="button" variant="ghost" className="rfq-row-remove" onClick={onRemove}>{t('Remove item')}</Button>
    <div className="rfq-row-categories">
      <SearchableSelect label={arabic ? 'الفئة الرئيسية' : 'Main Category'} name={`items.${index}.main_category_id`} error={error('main_category_id')} required value={line.main_category_id || ''} options={categories.filter(c => !c.parent_id).map(c => ({ value: c.id, label: label(c) }))} onChange={value => { classificationSeq.current++; setManualCategoryOverride(true); setPage({ scope: '', offset: 0 }); onChange({ ...line, main_category_id: value, subcategory_id: null, material_id: null }); }}/>
      <label className="field">{arabic ? 'الفئة الفرعية' : 'Sub Category'}<select name={`items.${index}.subcategory_id`} aria-invalid={Boolean(error('subcategory_id'))} value={line.subcategory_id || ''} required={children.length > 0} disabled={!line.main_category_id || !children.length} onChange={e => { classificationSeq.current++; setManualCategoryOverride(true); setPage({ scope: '', offset: 0 }); onChange({ ...line, subcategory_id: e.target.value || null, material_id: null }); }}><option value="">{t(children.length ? 'Choose subcategory' : 'No subcategory')}</option>{children.map(c => <option key={c.id} value={c.id}>{label(c)}</option>)}</select><FieldError id={`${id}-subcategory-error`} message={error('subcategory_id')}/></label>
    </div>
    <div className="field material-autocomplete" onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false); }}>
      <label htmlFor={`${id}-input`}>{t('Description')}</label>
      <input id={`${id}-input`} name={`items.${index}.description`} aria-invalid={Boolean(error('description'))} role="combobox" dir="auto" aria-autocomplete="list" aria-expanded={open} aria-controls={`${id}-list`} aria-activedescendant={open && suggestions[active] ? `${id}-${active}` : undefined} required minLength={3} maxLength={4000} value={line.description} placeholder={t('Search descriptions or short words')} onFocus={() => setOpen(true)} onChange={e => { classificationSeq.current++; setPage({ scope: '', offset: 0 }); onChange({ ...line, material_id: null, description: e.target.value, main_category_id: manualCategoryOverride ? line.main_category_id : null, subcategory_id: manualCategoryOverride ? line.subcategory_id : null }); setOpen(true); setActive(0); }} onKeyDown={e => { if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive(i => Math.min(i + 1, suggestions.length - 1)); } if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(0, i - 1)); } if (e.key === 'Enter' && open) { e.preventDefault(); if (suggestions[active]) choose(suggestions[active]); else setOpen(false); } if (e.key === 'PageDown' && open && suggestions.length === 20) { e.preventDefault(); setPage({ scope, offset: offset + 20 }); setActive(0); } if (e.key === 'PageUp' && open && offset > 0) { e.preventDefault(); setPage({ scope, offset: Math.max(0, offset - 20) }); setActive(0); } if (e.key === 'Escape') setOpen(false); }}/>
      {open && <div className="select-suggestions" id={`${id}-list`} role="listbox">{term.length < 2 ? <span className="suggestion-message">{t('Type at least two characters to search Material Master.')}</span> : result.loading || term !== line.description.trim() ? <span className="suggestion-message">{t('Searching...')}</span> : result.error ? <span className="suggestion-message">{t(result.error.message)}<RetryButton onClick={result.reload}/></span> : suggestions.length ? suggestions.map((m, i) => <button type="button" role="option" id={`${id}-${i}`} aria-selected={line.material_id === m.id} className={active === i ? 'is-active' : ''} key={m.id} onMouseDown={e => e.preventDefault()} onMouseEnter={() => setActive(i)} onClick={() => choose(m)}><span>{(arabic && (m.arabic_description || m.name_ar)) || m.name_en}</span><small>{m.source_material_code || m.code} · {m.unit}</small></button>) : <span className="suggestion-message">{t('No saved match. Your description will be used for this RFQ.')}</span>}{term.length >= 2 && !result.loading && !result.error && term === line.description.trim() && <span className="material-page-controls" onMouseDown={e => e.preventDefault()}><button type="button" disabled={offset === 0} onClick={() => { setPage({ scope, offset: Math.max(0, offset - 20) }); setActive(0); }}>{t('Previous')}</button><span aria-live="polite">{suggestions.length ? offset + 1 : 0}–{offset + suggestions.length}</span><button type="button" disabled={suggestions.length < 20} onClick={() => { setPage({ scope, offset: offset + 20 }); setActive(0); }}>{t('Next')}</button></span>}</div>}
      <FieldError id={`${id}-description-error`} message={error('description')}/>
    </div>
    <label className="field">{arabic ? 'المواصفات' : 'Specification'}<textarea name={`items.${index}.specifications`} aria-invalid={Boolean(error('specifications'))} maxLength={10000} rows={2} dir="auto" value={line.specifications || ''} onChange={e => onChange({ ...line, specifications: e.target.value })}/><FieldError id={`${id}-specifications-error`} message={error('specifications')}/></label>
    <div className="rfq-row-values">
      <label className="field">{t('Unit')}<select name={`items.${index}.unit`} aria-invalid={Boolean(error('unit'))} required value={line.unit || ''} onChange={e => onChange({ ...line, unit: e.target.value })}><option value="">{t('Choose unit')}</option>{units.map(u => <option key={u.code} value={u.code}>{u.code}</option>)}{line.unit && !units.some(u => u.code === line.unit) && <option value={line.unit}>{line.unit}</option>}</select><FieldError id={`${id}-unit-error`} message={error('unit')}/></label>
      <label className="field">{t('Quantity')}<input name={`items.${index}.quantity`} aria-invalid={Boolean(error('quantity'))} required type="number" min="0.001" step="0.001" value={line.quantity} onChange={e => onChange({ ...line, quantity: e.target.value })}/><FieldError id={`${id}-quantity-error`} message={error('quantity')}/></label>
    </div>
    <div className="rfq-row-values"><label className="field">{t('Discipline')}<select name={`items.${index}.discipline`} aria-invalid={Boolean(error('discipline'))} value={line.discipline||''} onChange={e=>onChange({...line,discipline:e.target.value})}><option value="">{t('Unspecified')}</option>{['Civil','Electrical','Mechanical'].map(value=><option key={value} value={value}>{t(value)}</option>)}{line.discipline&&!['Civil','Electrical','Mechanical'].includes(line.discipline)&&<option value={line.discipline}>{line.discipline}</option>}</select><FieldError id={`${id}-discipline-error`} message={error('discipline')}/></label><label className="field">{t('Required brand')}<input name={`items.${index}.required_brand`} maxLength={150} value={line.required_brand||''} onChange={e=>onChange({...line,required_brand:e.target.value})}/></label></div>
  </fieldset>;
}
