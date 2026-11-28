"use client";
import { useEffect, useId, useState } from 'react';
import type { Schemas } from '@/lib/api/generated/schema';
import { useApi } from '@/lib/api/use-api';
import { useLocale } from '@/lib/locale';
import { SearchableSelect } from '@/features/shared/searchable-select';
import { FieldError } from '@/features/shared/field-error';
import { RetryButton } from '@/features/shared/loading';
import { Button } from '@/components/ui/button';
import { keepBuyerDescription } from './buyer-description';

export type MaterialLine = Schemas['RFQLineIn'] & { key: string; id?: string };

export function MaterialRow({ line, index, categories, units, onChange, onRemove, errors = {} }: {
  line: MaterialLine; index: number; categories: Schemas['CategoryOut'][];
  units: { code: string; name_en: string }[];
  onChange: (line: MaterialLine) => void; onRemove: () => void; errors?: Record<string, string>;
}) {
  const { t, arabic } = useLocale();
  const id = useId();
  const [term, setTerm] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  useEffect(() => { const timer = setTimeout(() => setTerm(line.description.trim()), 250); return () => clearTimeout(timer); }, [line.description]);
  const children = categories.filter(c => c.parent_id === line.main_category_id);
  const params = new URLSearchParams({ q: term.slice(0, 100), limit: '20', autocomplete: 'true', main_category_id: line.main_category_id || '' });
  if (line.subcategory_id) params.set('subcategory_id', line.subcategory_id);
  const result = useApi<Schemas['MaterialOut'][]>(open && line.main_category_id && (!children.length || line.subcategory_id) && term === line.description.trim() ? `taxonomy/materials?${params}` : null);
  const selected = useApi<Schemas['MaterialOut']>(line.material_id && (!line.main_category_id || !line.unit) ? `taxonomy/materials/${line.material_id}` : null);
  const suggestions = result.data || [];
  useEffect(() => {
    const m = selected.data;
    if (!m || !categories.length || (line.main_category_id && line.unit)) return;
    const leaf = categories.find(c => c.id === m.category_id);
    const main = m.main_category_id || leaf?.parent_id || m.category_id;
    const sub = m.subcategory_id || (leaf?.parent_id ? leaf.id : null);
    onChange({ ...line, main_category_id: line.main_category_id || main, subcategory_id: line.subcategory_id || sub, unit: line.unit || m.unit });
  }, [selected.data, categories, line, onChange]);
  const label = (c: Schemas['CategoryOut']) => arabic && c.name_ar ? c.name_ar : c.name_en;
  const error = (field: string) => errors[`items.${index}.${field}`];
  function choose(m: Schemas['MaterialOut']) {
    const leaf = categories.find(c => c.id === m.category_id);
    onChange({ ...line, material_id: m.id, description: keepBuyerDescription(line.description, (arabic && (m.arabic_description || m.name_ar)) || m.name_en),
      unit: m.unit || line.unit, main_category_id: m.main_category_id || leaf?.parent_id || m.category_id,
      subcategory_id: m.subcategory_id || (leaf?.parent_id ? leaf.id : null) });
    setOpen(false);
  }
  return <fieldset className="rfq-material-row" dir={arabic ? 'rtl' : 'ltr'}>
    <legend>{t('Material')} {index + 1}</legend>
    <Button type="button" variant="ghost" className="rfq-row-remove" onClick={onRemove}>{arabic ? 'حذف المادة' : t('Remove item')}</Button>
    <div className="rfq-row-categories">
      <SearchableSelect label={arabic ? 'الفئة الرئيسية' : 'Main Category'} name={`items.${index}.main_category_id`} error={error('main_category_id')} required value={line.main_category_id || ''} options={categories.filter(c => !c.parent_id).map(c => ({ value: c.id, label: label(c) }))} onChange={value => { setOpen(false); onChange({ ...line, main_category_id: value, subcategory_id: null, material_id: null }); }}/>
      <label className="field">{arabic ? 'الفئة الفرعية' : 'Sub Category'}<select name={`items.${index}.subcategory_id`} aria-invalid={Boolean(error('subcategory_id'))} value={line.subcategory_id || ''} required={children.length > 0} disabled={!line.main_category_id || !children.length} onChange={e => { setOpen(false); onChange({ ...line, subcategory_id: e.target.value || null, material_id: null }); }}><option value="">{t(children.length ? 'Choose subcategory' : 'No subcategory')}</option>{children.map(c => <option key={c.id} value={c.id}>{label(c)}</option>)}</select><FieldError id={`${id}-subcategory-error`} message={error('subcategory_id')}/></label>
    </div>
    <div className="field material-autocomplete" onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false); }}>
      <label htmlFor={`${id}-input`}>{t('Description')}</label>
      <input id={`${id}-input`} name={`items.${index}.description`} aria-invalid={Boolean(error('description'))} role="combobox" dir="auto" aria-autocomplete="list" aria-expanded={open} aria-controls={`${id}-list`} aria-activedescendant={open && suggestions[active] ? `${id}-${active}` : undefined} required minLength={3} maxLength={4000} value={line.description} onFocus={() => setOpen(true)} onChange={e => { onChange({ ...line, material_id: null, description: e.target.value }); setOpen(true); setActive(0); }} onKeyDown={e => { if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive(i => Math.min(i + 1, suggestions.length - 1)); } if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(0, i - 1)); } if (e.key === 'Enter' && open) { e.preventDefault(); if (suggestions[active]) choose(suggestions[active]); else setOpen(false); } if (e.key === 'Escape') setOpen(false); }}/>
      {open && line.main_category_id && <div className="select-suggestions" id={`${id}-list`} role="listbox">{result.loading || term !== line.description.trim() ? <span className="suggestion-message">{t('Searching...')}</span> : result.error ? <span className="suggestion-message">{t(result.error.message)}<RetryButton onClick={result.reload}/></span> : suggestions.length ? suggestions.map((m, i) => <button type="button" role="option" id={`${id}-${i}`} aria-selected={line.material_id === m.id} className={active === i ? 'is-active' : ''} key={m.id} onMouseDown={e => e.preventDefault()} onMouseEnter={() => setActive(i)} onClick={() => choose(m)}><span>{(arabic && (m.arabic_description || m.name_ar)) || m.name_en}</span><small>{m.source_material_code || m.code} · {m.unit}</small></button>) : <span className="suggestion-message">{t('No saved match. Your description will be used for this RFQ.')}</span>}</div>}
      <FieldError id={`${id}-description-error`} message={error('description')}/>
    </div>
    <label className="field">{arabic ? 'المواصفات' : 'Specification'}<textarea name={`items.${index}.specifications`} aria-invalid={Boolean(error('specifications'))} maxLength={10000} rows={3} dir="auto" value={line.specifications || ''} onChange={e => onChange({ ...line, specifications: e.target.value })}/><FieldError id={`${id}-specifications-error`} message={error('specifications')}/></label>
    <div className="rfq-row-values">
      <label className="field">{t('Unit')}<select name={`items.${index}.unit`} aria-invalid={Boolean(error('unit'))} required value={line.unit || ''} onChange={e => onChange({ ...line, unit: e.target.value })}><option value="">{t('Choose unit')}</option>{units.map(u => <option key={u.code} value={u.code}>{u.code}</option>)}{line.unit && !units.some(u => u.code === line.unit) && <option value={line.unit}>{line.unit}</option>}</select><FieldError id={`${id}-unit-error`} message={error('unit')}/></label>
      <label className="field">{t('Quantity')}<input name={`items.${index}.quantity`} aria-invalid={Boolean(error('quantity'))} required type="number" min="0.001" step="0.001" value={line.quantity} onChange={e => onChange({ ...line, quantity: e.target.value })}/><FieldError id={`${id}-quantity-error`} message={error('quantity')}/></label>
    </div>
  </fieldset>;
}
