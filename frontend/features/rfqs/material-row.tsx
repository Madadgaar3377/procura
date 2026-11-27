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
export type MaterialLine = Schemas['RFQLineIn'] & {
    key: string;
};
export function MaterialRow({ line, index, categories, units, onChange, onRemove, grouped = false, loadMaterialDetails = true, errors = {} }: {
    loadMaterialDetails?: boolean;
    grouped?: boolean;
    errors?: Record<string, string>;
    line: MaterialLine;
    index: number;
    categories: Schemas['CategoryOut'][];
    units: {
        code: string;
        name_en: string;
    }[];
    onChange: (line: MaterialLine) => void;
    onRemove: () => void;
}) {
    const { t, arabic } = useLocale();
    const id = useId();
    const [term, setTerm] = useState(''), [open, setOpen] = useState(false), [active, setActive] = useState(0), [page, setPage] = useState({ scope: '', offset: 0 });
    useEffect(() => { const timer = setTimeout(() => { setTerm(line.description.trim()); setActive(0); }, 250); return () => clearTimeout(timer); }, [line.description]);
    const scope = [line.main_category_id, line.subcategory_id, term].join('|');
    const offset = page.scope === scope ? page.offset : 0;
    const params = new URLSearchParams({ q: term.slice(0, 100), offset: String(offset), limit: '20', autocomplete: 'true', main_category_id: line.main_category_id || '' });
    if (line.subcategory_id)
        params.set('subcategory_id', line.subcategory_id);
    const result = useApi<Schemas['MaterialOut'][]>((open || !line.description) && line.main_category_id && (!categories.some(c => c.parent_id === line.main_category_id) || line.subcategory_id) && term === line.description.trim() ? `taxonomy/materials?${params}` : null);
    const selected = useApi<Schemas['MaterialOut']>(line.material_id && loadMaterialDetails ? `taxonomy/materials/${line.material_id}` : null);
    const suggestions = result.data || [];
    const children = line.main_category_id ? categories.filter(c => c.parent_id === line.main_category_id) : [];
    useEffect(() => { if (open)
        document.getElementById(`${id}-${active}`)?.scrollIntoView({ block: 'nearest' }); }, [active, id, open]);
    const label = (c: Schemas['CategoryOut']) => arabic && c.name_ar ? c.name_ar : c.name_en;
    function choose(m: Schemas['MaterialOut']) { onChange({ ...line, material_id: m.id, description: keepBuyerDescription(line.description, (arabic && (m.arabic_description || m.name_ar)) || m.name_en), unit: m.unit, main_category_id: m.main_category_id || line.main_category_id, subcategory_id: m.subcategory_id || (categories.find(c => c.id === m.category_id)?.parent_id ? m.category_id : null) }); setOpen(false); }
    return <fieldset className="rfq-material-row"><legend>{t('Material')} {index + 1}</legend>{!grouped&&<div className="rfq-row-categories"><SearchableSelect label="Main category" name={`items.${index}.main_category_id`} error={errors[`items.${index}.main_category_id`]} required value={line.main_category_id || ''} options={categories.filter(c => !c.parent_id).map(c => ({ value: c.id, label: label(c) }))} onChange={value => { setOpen(false); setPage({scope:'',offset:0}); onChange({ ...line, main_category_id: value, subcategory_id: null, material_id: null, description: '', unit: '' }); }}/><label className="field">{t('Subcategory')}<select name={`items.${index}.subcategory_id`} aria-invalid={Boolean(errors[`items.${index}.subcategory_id`])} value={line.subcategory_id || ''} required={children.length > 0} disabled={!line.main_category_id || !children.length} onChange={e => { setOpen(false); setPage({scope:'',offset:0}); onChange({ ...line, subcategory_id: e.target.value || null, material_id: null, description: '', unit: '' }); }}><option value="">{t(children.length ? 'Choose subcategory' : 'No subcategory')}</option>{children.map(c => <option key={c.id} value={c.id}>{label(c)}</option>)}</select><FieldError id={`${id}-subcategory-error`} message={errors[`items.${index}.subcategory_id`]}/></label></div>}<div className="rfq-row-values"><div className="field material-autocomplete" onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget))
        setOpen(false); }}><label htmlFor={`${id}-input`}>{t('Description')}</label><input id={`${id}-input`} name={`items.${index}.description`} aria-invalid={Boolean(errors[`items.${index}.description`])} role="combobox" dir={arabic ? 'rtl' : 'ltr'} aria-autocomplete="list" aria-expanded={open} aria-controls={id} aria-activedescendant={open && suggestions[active] ? `${id}-${active}` : undefined} required minLength={3} maxLength={4000} disabled={!line.main_category_id || (children.length > 0 && !line.subcategory_id)} value={line.description} placeholder={t('Search descriptions or short words')} onFocus={() => setOpen(true)} onChange={e => { onChange({ ...line, description: e.target.value }); setPage({scope:'',offset:0}); setOpen(true); setActive(0); }} onKeyDown={e => { if (e.key === 'ArrowDown') {
        e.preventDefault();
        setOpen(true);
        setActive(i => Math.max(0, Math.min(i + 1, suggestions.length - 1)));
    } if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive(i => Math.max(0, i - 1));
    } if (e.key === 'Enter' && open) {
        e.preventDefault();
        if (suggestions[active])
            choose(suggestions[active]);
        else
            setOpen(false);
    } if (e.key === 'PageDown' && open && suggestions.length === 20) {
        e.preventDefault();
        setPage({ scope, offset: offset + 20 });
        setActive(0);
    } if (e.key === 'PageUp' && open && offset > 0) {
        e.preventDefault();
        setPage({ scope, offset: Math.max(0, offset - 20) });
        setActive(0);
    } if (e.key === 'Escape') {
        e.preventDefault();
        setOpen(false);
    } }}/>{open && <div className="select-suggestions"><div id={id} role="listbox">{result.loading || term !== line.description.trim() ? <span role="status" className="suggestion-message">{t('Searching...')}</span> : result.error ? <span role="alert" className="suggestion-message">{t(result.error.message)}<RetryButton onClick={result.reload}/></span> : suggestions.length ? suggestions.map((m, i) => <button type="button" role="option" id={`${id}-${i}`} aria-selected={line.material_id === m.id} className={active === i ? 'is-active' : ''} key={m.id} onMouseDown={e => e.preventDefault()} onMouseEnter={() => setActive(i)} onClick={() => choose(m)}><span>{arabic && (m.arabic_description || m.name_ar) || m.name_en}</span><small>{m.source_material_code || m.code} · {m.unit}</small></button>) : <span className="suggestion-message">{t('No saved match. Your description will be used for this RFQ.')}</span>}</div>{!result.loading && !result.error && term === line.description.trim() && <span className="material-page-controls" onMouseDown={e => e.preventDefault()}><button type="button" disabled={offset === 0} onClick={() => { setPage({ scope, offset: Math.max(0, offset - 20) }); setActive(0); }}>{t('Previous')}</button><span aria-live="polite">{suggestions.length ? offset + 1 : 0}–{offset + suggestions.length}</span><button type="button" disabled={suggestions.length < 20} onClick={() => { setPage({ scope, offset: offset + 20 }); setActive(0); }}>{t('Next')}</button></span>}</div>}{selected.data && <small>{selected.data.source_material_code || selected.data.code}</small>}<FieldError id={`${id}-description-error`} message={errors[`items.${index}.description`]}/></div><label className="field">{t('Unit')}<select name={`items.${index}.unit`} aria-invalid={Boolean(errors[`items.${index}.unit`])} required value={line.unit} onChange={e => onChange({ ...line, unit: e.target.value })}><option value="">{t('Choose unit')}</option>{units.map(u => <option key={u.code} value={u.code}>{u.code}</option>)}</select><FieldError id={`${id}-unit-error`} message={errors[`items.${index}.unit`]}/></label><label className="field">{t('Quantity')}<input name={`items.${index}.quantity`} aria-invalid={Boolean(errors[`items.${index}.quantity`])} required type="number" min="0.001" step="0.001" value={line.quantity} onChange={e => onChange({ ...line, quantity: e.target.value })}/><FieldError id={`${id}-quantity-error`} message={errors[`items.${index}.quantity`]}/></label></div><div className="rfq-row-footer"><small>{t('Description changes apply to this RFQ only.')}</small><Button type="button" variant="ghost" onClick={onRemove}>{t('Remove item')}</Button></div></fieldset>;
}
