"use client";

import {useEffect, useMemo, useState, type ReactNode} from 'react';
import {IconChevronLeft, IconChevronRight, IconPencil, IconPlus, IconSearch, IconTrash, IconX} from '@tabler/icons-react';
import {AdminModal} from '@/features/shared/admin-modal';
import {adminConfirm, adminPrompt} from '@/features/shared/admin-confirm';
import {useToast} from '@/features/shared/toast';
import {State} from '@/features/shared/ui';
import {Button} from '@/components/ui/button';
import {api, json} from '@/lib/api/client';
import {useApi} from '@/lib/api/use-api';
import {useLocale} from '@/lib/locale';

/* ------------------------------------------------------------------ API shapes */

type Channel = {id: string | null; value: string; label?: string; line_type?: string; extension?: string | null; primary?: boolean};
type ContactRow = {
  id: string; name?: string | null; role?: string | null; email?: string | null; is_primary: boolean; needs_review: boolean;
  emails?: Channel[]; phones?: Channel[]; faxes?: Channel[];
  assigned_capabilities?: {capability_id: string; category_id: string; category: string; materials: string[]; is_primary: boolean}[];
  source_responsibilities?: {id: string; material: string; brands?: string; category: string; mapping_status: string}[];
};
type SourceProduct = {id: string; path: string; raw_trade: string; raw_products: string; raw_brands: string; location: string; mapping_status: string; contact_ids: string[]};
type Summary = {disciplines: number; main_categories: number; subcategories: number; products: number; brands: number; approved_products: number; pending_products: number; approved_groups: number; pending_groups: number; capability_groups: number};
export type SupplierDetail = {
  company: Record<string, string | null | undefined>;
  status: {profile_status: string; verification_status: string; source: string; last_updated_at?: string};
  contacts: ContactRow[];
  documents?: {id: string; name: string; status: string}[];
  profile?: {
    extension_ready: boolean; business_types: string[]; suggested_business_types: string[]; business_services: string[];
    imported_scope_text?: string | null; description?: string | null;
    description_review: {flagged: boolean; reasons: string[]; scope_text?: string | null; notes: string[]; raw_description?: string | null};
  };
  locations?: {id: string; kind: string; city?: string | null; address?: string | null; notes?: string | null}[];
  coverage?: {office_cities: string[]; delivery_cities: {city: string; capability_groups: number; approved_groups: number}[]; nationwide: boolean; unspecified_groups: number; source_locations: string[]; legacy_joined_city_rows: number};
  summary?: Summary;
  source_products?: SourceProduct[];
};
type GroupContact = {id: string; name: string; all_products: boolean; material_ids: string[]; is_primary: boolean};
type TreeSub = {name: string | null; key: string; category_id: string | null; capability_ids: string[]; cities: string[]; brands: string[]; contacts: GroupContact[]; status: string | null; product_count: number; approved_products: number; pending_products: number; sample_products: string[]; trade_keywords?: string[]; material_ids?: string[]};
type TreeMain = {name: string; subcategories: TreeSub[]; subcategory_count: number; product_count: number; pending: number};
type Tree = {summary: Summary; disciplines: {name: string; main_categories: TreeMain[]; main_category_count: number; subcategory_count: number; product_count: number}[]};
type ProductRow = {id: string; material_id: string | null; trade_id?: string; description: string; description_ar?: string | null; discipline: string | null; main_category: string | null; subcategory: string | null; category_id: string | null; brands: string[]; contacts: {id: string; name: string}[]; status: string; source: string; unit?: string | null};
type ProductPage = {total: number; offset: number; limit: number; items: ProductRow[]; disciplines: string[]};
type CategoryOption = {id: string; name_en: string; name_ar?: string; parent_id?: string | null; active: boolean};
type MaterialOption = {id: string; name_en: string; unit?: string};

const TABS = [['overview', 'Overview'], ['capabilities', 'Categories & Capabilities'], ['products', 'Products & Brands'], ['contacts', 'Contacts'], ['locations', 'Locations & Services']] as const;
type Tab = typeof TABS[number][0];
const BUSINESS_TYPES = ['manufacturer', 'distributor', 'supplier', 'contractor', 'trader', 'service_provider', 'authorized_agent', 'importer'];
const BUSINESS_SERVICES = ['supply', 'delivery', 'installation', 'testing_commissioning', 'maintenance', 'fabrication', 'design', 'rental', 'after_sales_support'];
const LOCATION_KINDS = ['head_office', 'office', 'branch', 'factory', 'warehouse', 'showroom'];
const COMPANY_FIELDS: [string, string, string?][] = [
  ['name', 'Company name'], ['country', 'Country'], ['city', 'City'], ['address', 'Address'], ['cr_number', 'CR number'],
  ['vat_number', 'VAT number'], ['website', 'Website', 'url'], ['business_email', 'Business email', 'email'],
  ['business_phone', 'Business phone', 'tel'], ['contact_name', 'General contact person'], ['designation', 'General contact role'],
];
const humanize = (value: string) => value.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
const splitList = (value: string) => [...new Map(value.split(/[,;\n|]/).map(v => v.trim()).filter(Boolean).map(v => [v.toLocaleLowerCase(), v])).values()];
const errorText = (error: unknown, fallback: string) => error instanceof Error && error.message ? error.message : fallback;
const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every(v => b.includes(v));

/* ------------------------------------------------------------------ drafts */

type GroupDraft = {brands?: string; trade_keywords?: string; status?: string; remove_material_ids?: string[]; add_materials?: MaterialOption[]};
type ContactDraft = {name?: string; role?: string; assignments?: Record<string, {material_ids: string[]; is_primary: boolean}>};
type Drafts = {
  company: Record<string, string>; businessTypes?: string[]; services?: string[];
  locations?: {kind: string; city: string; address: string; notes: string}[]; coverage?: string[];
  groups: Record<string, GroupDraft>; sources: Record<string, {raw_products?: string; raw_trade?: string; raw_brands?: string; contact_ids?: string[]}>;
  contacts: Record<string, ContactDraft>;
};
const emptyDrafts = (): Drafts => ({company: {}, groups: {}, sources: {}, contacts: {}});
const hasChanges = (d: Drafts) => Boolean(Object.keys(d.company).length || d.businessTypes || d.services || d.locations || d.coverage
  || Object.keys(d.groups).length || Object.keys(d.sources).length || Object.keys(d.contacts).length);

/* ------------------------------------------------------------------ small UI pieces */

function Badge({tone = 'neutral', children}: {tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info'; children: ReactNode}) {
  return <span className={`sd-badge sd-badge-${tone}`}>{children}</span>;
}
function statusTone(status?: string | null) {
  return status === 'approved' || status === 'verified' ? 'success' : status === 'pending' || status === 'pending_verification' ? 'warning'
    : status === 'rejected' || status === 'revoked' || status === 'suspended' ? 'danger' : 'neutral';
}
function Chips({items, empty}: {items: string[]; empty: string}) {
  return items.length ? <ul className="sd-chips">{items.map(item => <li key={item}>{item}</li>)}</ul> : <span className="sd-muted">{empty}</span>;
}
function Empty({title, hint}: {title: string; hint?: string}) {
  return <div className="sd-empty"><strong>{title}</strong>{hint && <p>{hint}</p>}</div>;
}
function Section({title, action, children}: {title: string; action?: ReactNode; children: ReactNode}) {
  return <section className="sd-section"><header><h3>{title}</h3>{action}</header>{children}</section>;
}

function PublicContactVisibility({supplierId}: {supplierId: string}) {
  const {t} = useLocale();
  const path = `organizations/admin/suppliers/${encodeURIComponent(supplierId)}/public-profile`;
  const preferences = useApi<{fields: string[]}>(path);
  const [fields, setFields] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => { if (preferences.data) setFields(preferences.data.fields || []); }, [preferences.data]);
  const choices = [['business_email', 'Business email'], ['business_phone', 'Business phone']] as const;
  return <Section title={t('Public business contact visibility')}>
    <p className="sd-muted">{t('Publish only the saved company email and phone you have reviewed. Employee and category contacts stay private.')}</p>
    {preferences.error ? <p role="alert">{t('Unable to load public visibility settings.')}</p> : preferences.loading ? <p role="status">{t('Loading preferences…')}</p> : <>
      <div className="sd-checks">{choices.map(([field, label]) => <label key={field}><input type="checkbox" checked={fields.includes(field)} onChange={event => {setMessage(''); setFields(current => event.target.checked ? [...current, field] : current.filter(value => value !== field));}}/>{t(label)}</label>)}</div>
      <Button type="button" variant="outline" size="sm" disabled={saving || sameSet(fields, preferences.data?.fields || [])} onClick={async () => {
        setSaving(true); setMessage('');
        try {
          const preserved = (preferences.data?.fields || []).filter(field => !choices.some(([choice]) => choice === field));
          await api(path, {method: 'PATCH', body: json({fields: [...preserved, ...fields.filter(field => choices.some(([choice]) => choice === field))]})});
          preferences.reload(); setMessage(t('Public contact visibility saved.'));
        } catch { setMessage(t('Unable to save public visibility settings.')); }
        finally { setSaving(false); }
      }}>{t('Save public contact visibility')}</Button>
      {message && <p role="status">{message}</p>}
    </>}
  </Section>;
}

/* ------------------------------------------------------------------ main component */

export function SupplierDetailsModal({supplierId, onClose, onChanged, onInspect, onVerify}: {
  supplierId: string; onClose: () => void; onChanged: () => void; onInspect: () => void; onVerify?: () => void;
}) {
  const {t, arabic} = useLocale();
  const toast = useToast();
  const missing = t('Not provided');
  const [tab, setTab] = useState<Tab>('overview');
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [drafts, setDrafts] = useState<Drafts>(emptyDrafts);
  const base = `organizations/admin/suppliers/${supplierId}`;
  const detail = useApi<SupplierDetail>(base);
  const needsTree = tab === 'capabilities' || (editing && tab === 'contacts');
  const tree = useApi<Tree>(needsTree ? `${base}/capability-tree` : null);
  const categories = useApi<CategoryOption[]>(editing && tab === 'capabilities' ? 'taxonomy/categories?limit=250' : null);
  const data = detail.data;
  const company = data?.company || {};
  const dirty = hasChanges(drafts);

  function startEdit() { setDrafts(emptyDrafts()); setError(''); setEditing(true); }
  async function cancelEdit() {
    if (dirty && !(await adminConfirm(t('Discard unsaved supplier changes?')))) return;
    setDrafts(emptyDrafts()); setError(''); setEditing(false);
  }
  async function requestClose() {
    if (editing && dirty && !(await adminConfirm(t('Discard unsaved supplier changes?')))) return;
    onClose();
  }
  function reloadAll() { detail.reload(); tree.reload(); window.dispatchEvent(new Event('procura:data-changed')); onChanged(); }

  async function saveAll() {
    if (!data) return;
    const companyChanges = Object.fromEntries(Object.entries(drafts.company).filter(([key, value]) => (company[key] || '') !== value));
    if ('name' in companyChanges && !companyChanges.name.trim()) { setError(t('Company name is required')); setTab('overview'); return; }
    const email = companyChanges.business_email;
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) { setError(t('Enter a valid business email')); setTab('overview'); return; }
    if (drafts.coverage && !drafts.coverage.length) { setError(t('Enter at least one delivery city')); setTab('locations'); return; }
    // Clearing a stored value must be an explicit decision, never an accident of an empty input.
    const cleared = Object.entries(companyChanges).filter(([key, value]) => !value.trim() && company[key]).map(([key]) => t(COMPANY_FIELDS.find(f => f[0] === key)?.[1] || key));
    if (cleared.length && !(await adminConfirm(`${t('These fields will be cleared')}: ${cleared.join(', ')}`))) return;
    setBusy(true); setError('');
    const steps: [string, () => Promise<unknown>][] = [];
    if (Object.keys(companyChanges).length) steps.push(['Company information', () => api(base, {method: 'PATCH', body: json(companyChanges)})]);
    const profile = data.profile;
    if (profile && (drafts.businessTypes || drafts.services)) {
      const body: Record<string, unknown> = {};
      if (drafts.businessTypes && !sameSet(drafts.businessTypes, profile.business_types)) body.business_types = drafts.businessTypes;
      if (drafts.services && !sameSet(drafts.services, profile.business_services)) body.business_services = drafts.services;
      if (Object.keys(body).length) steps.push(['Business type and services', () => api(`${base}/profile-details`, {method: 'PUT', body: json(body)})]);
    }
    if (drafts.locations) {
      const locations = drafts.locations.filter(l => l.city.trim() || l.address.trim());
      steps.push(['Company locations', () => api(`${base}/locations`, {method: 'PUT', body: json({locations, confirm_clear: !locations.length})})]);
    }
    if (drafts.coverage) steps.push(['Delivery coverage', () => api(`${base}/coverage`, {method: 'PUT', body: json({cities: drafts.coverage})})]);
    for (const [categoryId, group] of Object.entries(drafts.groups)) {
      const body: Record<string, unknown> = {};
      if (group.brands !== undefined) body.brands = splitList(group.brands);
      if (group.trade_keywords !== undefined) body.trade_keywords = splitList(group.trade_keywords).join(' | ');
      if (group.status) body.status = group.status;
      if (group.remove_material_ids?.length) body.remove_material_ids = group.remove_material_ids;
      if (group.add_materials?.length) body.add_material_ids = group.add_materials.map(m => m.id);
      if (Object.keys(body).length) steps.push(['Capability', () => api(`${base}/capability-groups/${categoryId}`, {method: 'PATCH', body: json(body)})]);
    }
    for (const [tradeId, source] of Object.entries(drafts.sources)) {
      steps.push(['Product source', () => api(`${base}/trades/${tradeId}`, {method: 'PATCH', body: json(source)})]);
    }
    for (const [contactId, contact] of Object.entries(drafts.contacts)) {
      const body: Record<string, unknown> = {};
      if (contact.name !== undefined) body.name = contact.name;
      if (contact.role !== undefined) body.role = contact.role;
      if (Object.keys(body).length) steps.push(['Contact', () => api(`${base}/contacts/${contactId}`, {method: 'PATCH', body: json(body)})]);
      if (contact.assignments) {
        const assignments = Object.entries(contact.assignments).map(([category_id, value]) => ({category_id, ...value}));
        steps.push(['Employee assignments', () => api(`${base}/contacts/${contactId}/assignments`, {method: 'PUT', body: json({assignments})})]);
      }
    }
    let saved = 0;
    try {
      for (const [label, run] of steps) {
        try { await run(); saved++; } catch (e) { throw new Error(`${t(label)}: ${errorText(e, t('Could not save'))}`); }
      }
      setDrafts(emptyDrafts()); setEditing(false);
      toast.success(t('Supplier updated'), steps.length ? t('Changes saved and used by the next RFQ matching run.') : t('No changes to save.'));
    } catch (e) {
      setError(`${errorText(e, t('Could not save supplier changes'))}${saved ? ` (${saved} ${t('earlier changes were saved')})` : ''}`);
    } finally {
      setBusy(false);
      if (saved) reloadAll();
    }
  }

  async function immediate(label: string, run: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await run(); reloadAll(); toast.success(t(label)); }
    catch (e) { setError(errorText(e, t('Could not save supplier changes'))); }
    finally { setBusy(false); }
  }

  const verification = data?.status.verification_status || 'unverified';
  return <AdminModal open normalize={false} size="xl" busy={busy} preventDismiss={editing && dirty}
    className="live-dialog supplier-detail-dialog supplier-details-v2" aria-labelledby="supplier-details-title" onRequestClose={() => void requestClose()}>
    <header className="sd-header">
      <div className="sd-title">
        <h2 id="supplier-details-title">{String(company.name || t('Supplier details'))}</h2>
        <div className="sd-title-meta">
          <Badge tone={statusTone(verification)}>{t(humanize(verification))}</Badge>
          {data && <Badge tone={data.status.profile_status === 'complete' ? 'success' : 'warning'}>{t(data.status.profile_status === 'complete' ? 'Profile complete' : 'Profile incomplete')}</Badge>}
          {data?.status.source === 'imported' && <Badge tone="info">{t('Imported')}</Badge>}
        </div>
      </div>
      <div className="sd-header-actions">
        {editing ? <>
          <Button type="button" variant="outline" disabled={busy} onClick={() => void cancelEdit()}>{t('Cancel')}</Button>
          <Button type="button" disabled={busy || !dirty} onClick={() => void saveAll()}>{busy ? t('Saving...') : t('Save Changes')}</Button>
        </> : <>
          {verification !== 'verified' && onVerify && <Button type="button" variant="outline" disabled={!data} onClick={onVerify}>{t('Verify supplier')}</Button>}
          <Button type="button" disabled={!data} onClick={startEdit}><IconPencil size={16}/>{t('Edit Supplier')}</Button>
        </>}
        <Button type="button" variant="ghost" size="icon" className="admin-modal-close" aria-label={arabic ? 'إغلاق النافذة' : 'Close dialog'} disabled={busy} onClick={() => void requestClose()}><IconX size={20}/></Button>
      </div>
      <nav className="sd-tabs" role="tablist" aria-label={t('Supplier details sections')}>
        {TABS.map(([key, label]) => <button key={key} type="button" role="tab" id={`sd-tab-${key}`} aria-selected={tab === key} aria-controls="sd-panel" tabIndex={tab === key ? 0 : -1}
          className={tab === key ? 'active' : ''} onClick={() => setTab(key)}
          onKeyDown={event => { if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return; const index = TABS.findIndex(([k]) => k === tab); const step = (event.key === 'ArrowRight') !== arabic ? 1 : -1; const next = TABS[(index + step + TABS.length) % TABS.length][0]; setTab(next); document.getElementById(`sd-tab-${next}`)?.focus(); }}>
          {t(label)}
        </button>)}
      </nav>
    </header>
    <div className="admin-modal-body sd-body" id="sd-panel" role="tabpanel" aria-labelledby={`sd-tab-${tab}`}>
      {error && <p className="notice warn" role="alert">{error}</p>}
      {editing && tab !== 'products' && tab !== 'capabilities' && data?.profile && !data.profile.extension_ready && (tab === 'locations' || tab === 'overview') &&
        <p className="notice warn">{t('Business type, services and company locations need database migration zm56c3d4e5f6 before they can be saved.')}</p>}
      <State loading={detail.loading && !data} error={detail.error} retry={detail.reload}/>
      {data && tab === 'overview' && <><OverviewTab data={data} editing={editing} drafts={drafts} setDrafts={setDrafts} missing={missing}/><PublicContactVisibility supplierId={supplierId}/></>}
      {data && tab === 'capabilities' && <CapabilitiesTab base={base} tree={tree} editing={editing} drafts={drafts} setDrafts={setDrafts} categories={categories.data || []} busy={busy} immediate={immediate} onInspect={onInspect}/>}
      {data && tab === 'products' && <ProductsTab base={base} data={data} editing={editing} drafts={drafts} setDrafts={setDrafts}/>}
      {data && tab === 'contacts' && <ContactsTab base={base} data={data} tree={tree.data} editing={editing} drafts={drafts} setDrafts={setDrafts} busy={busy} immediate={immediate} missing={missing}/>}
      {data && tab === 'locations' && <LocationsTab data={data} editing={editing} drafts={drafts} setDrafts={setDrafts} missing={missing}/>}
    </div>
  </AdminModal>;
}

type TabProps = {editing: boolean; drafts: Drafts; setDrafts: React.Dispatch<React.SetStateAction<Drafts>>};

/* ------------------------------------------------------------------ Overview */

function OverviewTab({data, editing, drafts, setDrafts, missing}: TabProps & {data: SupplierDetail; missing: string}) {
  const {t} = useLocale();
  const company = data.company, profile = data.profile, summary = data.summary;
  const value = (key: string) => drafts.company[key] ?? String(company[key] ?? '');
  const types = drafts.businessTypes ?? profile?.business_types ?? [];
  const suggested = profile?.suggested_business_types || [];
  const review = profile?.description_review;
  const description = drafts.company.description ?? (review?.flagged ? (profile?.description || '') : String(company.description || ''));
  const set = (key: string, next: string) => setDrafts(d => ({...d, company: {...d.company, [key]: next}}));
  const shownTypes = (types.length ? types : suggested).map(type => t(humanize(type)));
  return <>
    {summary && <div className="sd-stats" aria-label={t('Capability summary')}>
      {([['Disciplines', summary.disciplines], ['Main categories', summary.main_categories], ['Subcategories', summary.subcategories], ['Products', summary.products], ['Brands', summary.brands], ['Contacts', data.contacts.length]] as [string, number][]).map(([label, count]) =>
        <div key={label}><strong>{count}</strong><span>{t(label)}</span></div>)}
    </div>}
    <Section title={t('Company information')}>
      {editing ? <div className="sd-form-grid">
        {COMPANY_FIELDS.map(([key, label, type]) => <label className="field" key={key}>{t(label)}{key === 'name' && <span aria-hidden> *</span>}
          <input type={type || 'text'} value={value(key)} required={key === 'name'} maxLength={key === 'business_phone' ? 30 : key === 'address' || key === 'website' ? 300 : 220} onChange={e => set(key, e.target.value)}/></label>)}
      </div> : <dl className="sd-fields">
        {COMPANY_FIELDS.slice(0, 9).map(([key, label]) => <div key={key}><dt>{t(label)}</dt><dd>{key === 'website' && company[key] ? <a href={/^https?:/.test(String(company[key])) ? String(company[key]) : `https://${company[key]}`} target="_blank" rel="noreferrer">{company[key]}</a> : String(company[key] || missing)}</dd></div>)}
        <div><dt>{t('Business type')}</dt><dd>{shownTypes.length ? shownTypes.join(', ') : missing}{!types.length && suggested.length > 0 && <small className="sd-muted"> · {t('detected from import, not yet confirmed')}</small>}</dd></div>
        <div><dt>{t('Verification status')}</dt><dd><Badge tone={statusTone(data.status.verification_status)}>{t(humanize(data.status.verification_status || 'unverified'))}</Badge></dd></div>
      </dl>}
    </Section>
    {editing && <Section title={t('Business type')}>
      <fieldset className="sd-checks" disabled={!profile?.extension_ready}><legend className="sr-only">{t('Business type')}</legend>
        {BUSINESS_TYPES.map(type => <label key={type}><input type="checkbox" checked={types.includes(type)} onChange={e => setDrafts(d => ({...d, businessTypes: e.target.checked ? [...types, type] : types.filter(x => x !== type)}))}/>{t(humanize(type))}</label>)}
      </fieldset>
      {!profile?.business_types.length && suggested.length > 0 && !drafts.businessTypes && <Button type="button" variant="quiet" size="sm" onClick={() => setDrafts(d => ({...d, businessTypes: suggested}))}>{t('Use detected type')}: {suggested.map(s => t(humanize(s))).join(', ')}</Button>}
    </Section>}
    <Section title={t('Company description')}>
      {editing ? <label className="field">{t('Describe the company only. Products, brands and categories belong in their own tabs.')}
        <textarea rows={5} maxLength={5000} value={description} onChange={e => set('description', e.target.value)}/></label>
        : description ? <p className="sd-description">{description}</p>
        : <Empty title={t('No company description yet')} hint={t('Add a short description of the company from Edit Supplier.')}/>}
      {review?.flagged && !drafts.company.description && <div className="notice sd-review">
        <strong>{t('Imported text was not shown as the company description')}</strong>
        <p>{t('It lists products, categories or import notes. It is kept unchanged in the database and listed under Products & Brands until the reviewed repair moves it.')}</p>
        {review.scope_text && <p className="sd-muted">{review.scope_text.length > 280 ? `${review.scope_text.slice(0, 280)}…` : review.scope_text}</p>}
        {review.notes.length > 0 && <p className="sd-muted">{review.notes.join(' · ')}</p>}
      </div>}
    </Section>
  </>;
}

/* ------------------------------------------------------------------ Categories & Capabilities */

function CapabilitiesTab({base, tree, editing, drafts, setDrafts, categories, busy, immediate, onInspect}: TabProps & {
  base: string; tree: {data?: Tree; loading: boolean; error?: Error; reload: () => void}; categories: CategoryOption[]; busy: boolean;
  immediate: (label: string, run: () => Promise<unknown>) => Promise<void>; onInspect: () => void;
}) {
  const {t} = useLocale();
  const [filter, setFilter] = useState('');
  const [status, setStatus] = useState('');
  const [newCategory, setNewCategory] = useState('');
  const data = tree.data;
  const needle = filter.trim().toLocaleLowerCase();
  const existing = new Set(data?.disciplines.flatMap(d => d.main_categories.flatMap(m => m.subcategories.map(s => s.category_id))).filter(Boolean));
  const parentName = (id?: string | null) => categories.find(c => c.id === id)?.name_en;
  const options = categories.filter(c => c.active && !existing.has(c.id)).map(c => ({id: c.id, label: c.parent_id ? `${parentName(c.parent_id) || ''} / ${c.name_en}` : c.name_en})).sort((a, b) => a.label.localeCompare(b.label));
  const visible = (sub: TreeSub, main: string, discipline: string) => (!status || sub.status === status)
    && (!needle || [discipline, main, sub.name, ...sub.brands, ...sub.sample_products, ...sub.contacts.map(c => c.name)].join(' ').toLocaleLowerCase().includes(needle));
  return <>
    <div className="sd-toolbar">
      <label className="sd-search"><IconSearch size={16} aria-hidden/><input value={filter} onChange={e => setFilter(e.target.value)} placeholder={t('Filter categories, brands or employees')} aria-label={t('Filter capabilities')}/></label>
      <select value={status} onChange={e => setStatus(e.target.value)} aria-label={t('Capability status')}>
        <option value="">{t('All statuses')}</option><option value="approved">{t('Approved')}</option><option value="pending">{t('Pending')}</option><option value="suspended">{t('Suspended')}</option><option value="rejected">{t('Rejected')}</option>
      </select>
      <Button type="button" variant="outline" onClick={onInspect}>{t('Inspect capability')}</Button>
    </div>
    {data && <p className="sd-muted">{data.summary.disciplines} {t('disciplines')} · {data.summary.main_categories} {t('main categories')} · {data.summary.subcategories} {t('subcategories')} · {data.summary.products} {t('products')} · <Badge tone="success">{data.summary.approved_groups} {t('approved')}</Badge> <Badge tone="warning">{data.summary.pending_groups} {t('pending')}</Badge></p>}
    {editing && <div className="sd-add-row">
      <select value={newCategory} onChange={e => setNewCategory(e.target.value)} aria-label={t('Add capability category')}><option value="">{t('Add a capability category…')}</option>{options.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select>
      <Button type="button" variant="outline" disabled={busy || !newCategory} onClick={() => void immediate('Capability added', async () => { await api(`${base}/capability-groups`, {method: 'POST', body: json({category_id: newCategory})}); setNewCategory(''); })}><IconPlus size={16}/>{t('Add capability')}</Button>
    </div>}
    <State loading={tree.loading && !data} error={tree.error} retry={tree.reload} empty={Boolean(data && !data.disciplines.length)}/>
    {data?.disciplines.map(discipline => {
      const mains = discipline.main_categories.map(main => ({...main, subcategories: main.subcategories.filter(sub => visible(sub, main.name, discipline.name))})).filter(main => main.subcategories.length);
      if (!mains.length) return null;
      return <details className="sd-tree sd-tree-discipline" key={discipline.name} open>
        <summary><span>{t(discipline.name)}</span><small>{discipline.main_category_count} {t('main categories')} · {discipline.subcategory_count} {t('subcategories')} · {discipline.product_count} {t('products')}</small></summary>
        {mains.map(main => <details className="sd-tree sd-tree-main" key={main.name} open={Boolean(needle || status) || undefined}>
          <summary><span>{main.name}</span><small>{main.subcategory_count} {t('subcategories')} · {main.product_count} {t('products')}{main.pending ? ` · ${main.pending} ${t('pending')}` : ''}</small></summary>
          {main.subcategories.map(sub => <SubcategoryNode key={sub.key} base={base} sub={sub} editing={editing} drafts={drafts} setDrafts={setDrafts} busy={busy} immediate={immediate}/>)}
        </details>)}
      </details>;
    })}
  </>;
}

function SubcategoryNode({base, sub, editing, drafts, setDrafts, busy, immediate}: TabProps & {base: string; sub: TreeSub; busy: boolean; immediate: (label: string, run: () => Promise<unknown>) => Promise<void>}) {
  const {t} = useLocale();
  const [open, setOpen] = useState(false);
  const products = useApi<ProductPage>(open ? `${base}/products?node=${encodeURIComponent(sub.key)}&limit=100` : null);
  const id = sub.category_id;
  const draft = (id && drafts.groups[id]) || {};
  const patch = (next: Partial<GroupDraft>) => id && setDrafts(d => ({...d, groups: {...d.groups, [id]: {...d.groups[id], ...next}}}));
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<MaterialOption[]>([]);
  async function findMaterials() {
    if (!id || search.trim().length < 2) return;
    try { setResults(await api<MaterialOption[]>(`taxonomy/materials?category_id=${id}&q=${encodeURIComponent(search.trim())}&limit=20&autocomplete=true`)); } catch { setResults([]); }
  }
  const removed = new Set(draft.remove_material_ids || []);
  return <details className="sd-tree sd-tree-sub" onToggle={e => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
    <summary>
      <span>{sub.name || t('Main category capability')}</span>
      <span className="sd-sub-meta">
        {sub.status && <Badge tone={statusTone(draft.status || sub.status)}>{t(humanize(draft.status || sub.status))}</Badge>}
        <small>{sub.product_count} {t('products')}{sub.pending_products ? ` · ${sub.pending_products} ${t('pending review')}` : ''}</small>
      </span>
    </summary>
    <div className="sd-sub-body">
      <dl className="sd-fields sd-fields-compact">
        <div><dt>{t('Brands')}</dt><dd>{editing && id ? <input value={draft.brands ?? sub.brands.join(', ')} onChange={e => patch({brands: e.target.value})} aria-label={t('Brands')} placeholder={t('Comma separated')}/> : <Chips items={sub.brands} empty={t('No brands recorded')}/>}</dd></div>
        <div><dt>{t('Delivery cities')}</dt><dd><Chips items={sub.cities} empty={t('Not specified')}/></dd></div>
        <div><dt>{t('Responsible employees')}</dt><dd><Chips items={sub.contacts.map(c => c.name + (c.is_primary ? ` (${t('primary')})` : ''))} empty={t('Company contacts')}/></dd></div>
        {editing && id && <div><dt>{t('Status')}</dt><dd><select value={draft.status || sub.status || ''} onChange={e => patch({status: e.target.value})} aria-label={t('Capability status')}><option value="approved">{t('Approved')}</option><option value="pending">{t('Pending')}</option><option value="suspended">{t('Suspended')}</option></select></dd></div>}
        {editing && id && <div className="sd-span"><dt>{t('Matching keywords (fuzzy product words)')}</dt><dd><input value={draft.trade_keywords ?? (sub.trade_keywords || []).join(', ')} onChange={e => patch({trade_keywords: e.target.value})} aria-label={t('Matching keywords')}/></dd></div>}
      </dl>
      <State loading={products.loading && !products.data} error={products.error} retry={products.reload}/>
      {products.data && (products.data.items.length ? <ul className="sd-product-list">{products.data.items.map(row => <li key={row.id} className={row.material_id && removed.has(row.material_id) ? 'removed' : ''}>
        <span>{row.description}{row.unit ? <small className="sd-muted"> · {row.unit}</small> : null}</span>
        <span className="sd-row-actions"><Badge tone={statusTone(row.status)}>{t(row.source === 'imported_source' ? 'Pending catalog link' : humanize(row.status))}</Badge>
          {editing && row.material_id && <Button type="button" size="sm" variant="ghost" aria-label={`${t('Remove')} ${row.description}`} onClick={() => patch({remove_material_ids: removed.has(row.material_id!) ? [...removed].filter(x => x !== row.material_id) : [...removed, row.material_id!]})}>{removed.has(row.material_id) ? t('Undo') : <IconTrash size={15}/>}</Button>}</span>
      </li>)}{products.data.total > products.data.items.length && <li className="sd-muted">{products.data.total - products.data.items.length} {t('more in Products & Brands')}</li>}</ul> : <p className="sd-muted">{t('No products recorded for this subcategory')}</p>)}
      {editing && id && <div className="sd-material-add">
        <div className="sd-add-row"><input value={search} onChange={e => setSearch(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void findMaterials(); } }} placeholder={t('Search Material Master to add products')} aria-label={t('Search Material Master')}/><Button type="button" variant="outline" size="sm" onClick={() => void findMaterials()}><IconSearch size={15}/>{t('Search')}</Button></div>
        {results.length > 0 && <ul className="sd-product-list">{results.map(m => { const added = draft.add_materials?.some(x => x.id === m.id) || sub.material_ids?.includes(m.id); return <li key={m.id}><span>{m.name_en}</span><Button type="button" size="sm" variant="quiet" disabled={added} onClick={() => patch({add_materials: [...(draft.add_materials || []), m]})}>{added ? t('Added') : t('Add')}</Button></li>; })}</ul>}
        {draft.add_materials?.length ? <p className="sd-muted">{t('To be added')}: {draft.add_materials.map(m => m.name_en).join(', ')}</p> : null}
        <Button type="button" variant="ghost" size="sm" className="sd-danger" disabled={busy} onClick={() => void (async () => { if (await adminConfirm(t('Remove this capability, its products and employee assignments from the supplier? RFQs will no longer match it.'))) await immediate('Capability removed', () => api(`${base}/capability-groups/${id}?confirm=true`, {method: 'DELETE'})); })()}><IconTrash size={15}/>{t('Remove capability')}</Button>
      </div>}
    </div>
  </details>;
}

/* ------------------------------------------------------------------ Products & Brands */

function ProductsTab({base, data, editing, drafts, setDrafts}: TabProps & {base: string; data: SupplierDetail}) {
  const {t, arabic} = useLocale();
  const [query, setQuery] = useState(''), [debounced, setDebounced] = useState('');
  const [discipline, setDiscipline] = useState(''), [status, setStatus] = useState(''), [offset, setOffset] = useState(0);
  useEffect(() => { const timer = window.setTimeout(() => { setDebounced(query.trim()); setOffset(0); }, 300); return () => window.clearTimeout(timer); }, [query]);
  const limit = 25;
  const params = new URLSearchParams({limit: String(limit), offset: String(offset)});
  if (debounced) params.set('q', debounced); if (discipline) params.set('discipline', discipline); if (status) params.set('status', status);
  const page = useApi<ProductPage>(`${base}/products?${params}`, {keepPreviousData: true});
  const rows = page.data?.items || [];
  const total = page.data?.total || 0;
  const scope = data.profile?.imported_scope_text || data.profile?.description_review.scope_text;
  const contacts = data.contacts;
  const Prev = arabic ? IconChevronRight : IconChevronLeft, Next = arabic ? IconChevronLeft : IconChevronRight;
  return <>
    <div className="sd-toolbar">
      <label className="sd-search"><IconSearch size={16} aria-hidden/><input value={query} onChange={e => setQuery(e.target.value)} placeholder={t('Search products, brands, categories or contacts')} aria-label={t('Search products')}/></label>
      <select value={discipline} onChange={e => { setDiscipline(e.target.value); setOffset(0); }} aria-label={t('Discipline')}><option value="">{t('All disciplines')}</option>{(page.data?.disciplines || []).map(d => <option key={d} value={d}>{t(d)}</option>)}</select>
      <select value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }} aria-label={t('Capability status')}><option value="">{t('All statuses')}</option><option value="approved">{t('Approved')}</option><option value="pending">{t('Pending')}</option></select>
    </div>
    <State loading={page.loading && !page.data} error={page.error} retry={page.reload}/>
    {page.data && (rows.length ? <div className="sd-table" aria-busy={page.loading}>
      <table><thead><tr>{['Product description', 'Discipline', 'Main category', 'Subcategory', 'Brand', 'Responsible contact', 'Capability status'].map(h => <th key={h} scope="col">{t(h)}</th>)}</tr></thead>
        <tbody>{rows.map(row => <tr key={row.id}>
          <td>{arabic && row.description_ar ? row.description_ar : row.description}{row.unit && <small className="sd-muted"> · {row.unit}</small>}</td>
          <td>{row.discipline ? t(row.discipline) : '—'}</td><td>{row.main_category || '—'}</td><td>{row.subcategory || '—'}</td>
          <td>{row.brands.length ? row.brands.join(', ') : '—'}</td>
          <td>{row.contacts.length ? row.contacts.map(c => c.name).join(', ') : <span className="sd-muted">{t('Company contacts')}</span>}</td>
          <td><Badge tone={statusTone(row.status)}>{t(row.source === 'imported_source' ? 'Pending catalog link' : humanize(row.status))}</Badge></td>
        </tr>)}</tbody></table>
    </div> : <Empty title={debounced || discipline || status ? t('No products match these filters') : t('No products recorded')}/>)}
    {total > limit && <div className="sd-pager">
      <span>{offset + 1}–{Math.min(offset + limit, total)} {t('of')} {total}</span>
      <Button type="button" variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}><Prev size={15}/>{t('Previous')}</Button>
      <Button type="button" variant="outline" size="sm" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>{t('Next')}<Next size={15}/></Button>
    </div>}
    {scope && <Section title={t('Imported scope of supply (source text)')}><p className="sd-muted sd-description">{scope}</p></Section>}
    {editing && <Section title={t('Imported product sources')}>
      <p className="sd-muted">{t('Source wording is used for fuzzy RFQ matching. Separate products with semicolons. Empty values are rejected.')}</p>
      {(data.source_products || []).length ? (data.source_products || []).map(source => {
        const draft = drafts.sources[source.id] || {};
        const patch = (next: Record<string, unknown>) => setDrafts(d => ({...d, sources: {...d.sources, [source.id]: {...d.sources[source.id], ...next}}}));
        const assigned = draft.contact_ids ?? source.contact_ids;
        const field = source.raw_products ? 'raw_products' as const : 'raw_trade' as const;
        return <fieldset className="sd-source" key={source.id}><legend>{source.path || t('Uncategorized')} <Badge tone={source.mapping_status === 'mapped' || source.mapping_status === 'approved' ? 'success' : 'warning'}>{t(humanize(source.mapping_status))}</Badge></legend>
          <label className="field">{t('Products')}<textarea rows={2} value={draft[field] ?? source[field]} onChange={e => patch({[field]: e.target.value})}/></label>
          <label className="field">{t('Brands')}<input value={draft.raw_brands ?? source.raw_brands} onChange={e => patch({raw_brands: e.target.value})}/></label>
          {contacts.length > 1 && <fieldset className="sd-checks"><legend>{t('Responsible contacts')}</legend>{contacts.map(c => <label key={c.id}><input type="checkbox" checked={assigned.includes(c.id)} onChange={e => patch({contact_ids: e.target.checked ? [...assigned, c.id] : assigned.filter(x => x !== c.id)})}/>{c.name || c.email || t('Unnamed contact')}</label>)}</fieldset>}
        </fieldset>;
      }) : <p className="sd-muted">{t('No imported product sources')}</p>}
    </Section>}
  </>;
}

/* ------------------------------------------------------------------ Contacts */

function channelList(items: Channel[] | undefined) {
  return (items || []).map(item => item.value + (item.extension ? ` ext. ${item.extension}` : ''));
}

function ContactsTab({base, data, tree, editing, drafts, setDrafts, busy, immediate, missing}: TabProps & {
  base: string; data: SupplierDetail; tree?: Tree; busy: boolean; immediate: (label: string, run: () => Promise<unknown>) => Promise<void>; missing: string;
}) {
  const {t} = useLocale();
  const [newContact, setNewContact] = useState({name: '', role: '', email: '', phone: ''});
  const groups = useMemo(() => (tree?.disciplines || []).flatMap(d => d.main_categories.flatMap(m => m.subcategories.filter(s => s.category_id).map(s => ({...s, label: `${m.name}${s.name ? ` / ${s.name}` : ''}`})))), [tree]);
  const company = data.company;
  const responsibleFor = (contact: ContactRow) => {
    const seen = new Map<string, {category: string; materials: string[]}>();
    for (const item of contact.assigned_capabilities || []) {
      const entry = seen.get(item.category_id) || {category: item.category, materials: []};
      entry.materials = [...new Set([...entry.materials, ...item.materials])];
      seen.set(item.category_id, entry);
    }
    return [...seen.values()];
  };
  const specific = data.contacts.filter(c => responsibleFor(c).length || (c.source_responsibilities || []).length);
  const general = data.contacts.filter(c => !specific.includes(c));
  const cities = (contact: ContactRow) => [...new Set(groups.filter(g => g.contacts.some(x => x.id === contact.id)).flatMap(g => g.cities))];

  const renderContact = (contact: ContactRow) => {
    const draft = drafts.contacts[contact.id] || {};
    const patch = (next: ContactDraft) => setDrafts(d => ({...d, contacts: {...d.contacts, [contact.id]: {...d.contacts[contact.id], ...next}}}));
    const mobiles = (contact.phones || []).filter(p => p.line_type === 'mobile');
    const phones = (contact.phones || []).filter(p => p.line_type !== 'mobile');
    const responsibilities = responsibleFor(contact);
    const current = draft.assignments ?? Object.fromEntries(groups.filter(g => g.contacts.some(x => x.id === contact.id)).map(g => { const c = g.contacts.find(x => x.id === contact.id)!; return [g.category_id!, {material_ids: c.all_products ? [] : c.material_ids, is_primary: c.is_primary}]; }));
    const serviceArea = cities(contact);
    return <article className="sd-contact" key={contact.id}>
      <header>
        {editing ? <div className="sd-form-grid"><label className="field">{t('Contact name')}<input value={draft.name ?? contact.name ?? ''} onChange={e => patch({name: e.target.value})}/></label><label className="field">{t('Role')}<input value={draft.role ?? contact.role ?? ''} onChange={e => patch({role: e.target.value})}/></label></div>
          : <div><h4>{contact.name || t('Unnamed contact')}</h4><p className="sd-muted">{contact.role || t('Role not provided')}</p></div>}
        <div className="sd-row-actions">{contact.is_primary && <Badge tone="info">{t('Primary')}</Badge>}{contact.needs_review && <Badge tone="warning">{t('Needs review')}</Badge>}</div>
      </header>
      <dl className="sd-fields sd-fields-compact">
        <div><dt>{t('Email')}</dt><dd>{channelList(contact.emails).join(', ') || missing}</dd></div>
        <div><dt>{t('Mobile')}</dt><dd>{channelList(mobiles).join(', ') || missing}</dd></div>
        <div><dt>{t('Telephone')}</dt><dd>{channelList(phones).join(', ') || missing}</dd></div>
        <div><dt>{t('Fax')}</dt><dd>{channelList(contact.faxes).join(', ') || missing}</dd></div>
        {responsibilities.length > 0 && <div className="sd-span"><dt>{t('Assigned categories')}</dt><dd><Chips items={responsibilities.map(r => r.category)} empty={missing}/></dd></div>}
        {responsibilities.some(r => r.materials.length) && <div className="sd-span"><dt>{t('Assigned products')}</dt><dd>{responsibilities.flatMap(r => r.materials).slice(0, 12).join(', ')}</dd></div>}
        {(contact.source_responsibilities || []).length > 0 && <div className="sd-span"><dt>{t('Imported product responsibilities')}</dt><dd>{(contact.source_responsibilities || []).slice(0, 6).map(r => `${r.category}${r.brands ? ` (${r.brands})` : ''}`).join(' · ')}{(contact.source_responsibilities || []).length > 6 ? ` +${(contact.source_responsibilities || []).length - 6}` : ''}</dd></div>}
        {serviceArea.length > 0 && <div className="sd-span"><dt>{t('Responsible service area')}</dt><dd><Chips items={serviceArea} empty={missing}/></dd></div>}
      </dl>
      {editing && <div className="sd-contact-edit">
        <div className="sd-row-actions">
          {(['email', 'phone', 'fax'] as const).map(kind => <Button key={kind} type="button" size="sm" variant="outline" disabled={busy} onClick={() => void (async () => { const value = (await adminPrompt(t(kind === 'email' ? 'Add email' : kind === 'phone' ? 'Add mobile or telephone number' : 'Add fax number')))?.trim(); if (value) await immediate('Contact detail added', () => api(`${base}/contacts/${contact.id}/channels`, {method: 'POST', body: json({kind, value, label: kind === 'fax' ? 'fax' : kind === 'phone' ? (/^(\+?9665|05)/.test(value.replace(/[\s-]/g, '')) ? 'mobile' : 'office') : 'other'})})); })()}><IconPlus size={14}/>{t(kind === 'email' ? 'Email' : kind === 'phone' ? 'Phone' : 'Fax')}</Button>)}
          <Button type="button" size="sm" variant="ghost" className="sd-danger" disabled={busy} onClick={() => void (async () => { if (await adminConfirm(t('Delete this contact? This action will be recorded.'))) await immediate('Contact deleted', () => api(`${base}/contacts/${contact.id}`, {method: 'DELETE', body: json({confirm: true})})); })()}><IconTrash size={14}/>{t('Delete contact')}</Button>
        </div>
        {[...(contact.emails || []), ...(contact.phones || []), ...(contact.faxes || [])].filter(c => c.id).length > 1 && <ul className="sd-chips sd-chips-removable">{[...(contact.emails || []), ...(contact.phones || []), ...(contact.faxes || [])].filter(c => c.id).map(c => <li key={c.id}>{c.value}<button type="button" aria-label={`${t('Remove')} ${c.value}`} disabled={busy} onClick={() => void (async () => { if (await adminConfirm(t('Remove this contact detail?'))) await immediate('Contact detail removed', () => api(`${base}/contacts/${contact.id}/channels/${c.id}`, {method: 'DELETE', body: json({confirm: true})})); })()}><IconX size={12}/></button></li>)}</ul>}
        <fieldset className="sd-assign"><legend>{t('RFQ routing: categories and products this employee handles')}</legend>
          {!tree ? <p className="sd-muted">{t('Loading capabilities…')}</p> : groups.length ? groups.map(group => {
            const assignment = current[group.category_id!];
            const set = (next?: {material_ids: string[]; is_primary: boolean}) => { const copy = {...current}; if (next) copy[group.category_id!] = next; else delete copy[group.category_id!]; patch({assignments: copy}); };
            return <div key={group.key} className="sd-assign-row">
              <label><input type="checkbox" checked={Boolean(assignment)} onChange={e => set(e.target.checked ? {material_ids: [], is_primary: false} : undefined)}/>{group.label}</label>
              {assignment && <label className="sd-inline"><input type="checkbox" checked={assignment.is_primary} onChange={e => set({...assignment, is_primary: e.target.checked})}/>{t('Primary for this category')}</label>}
            </div>;
          }) : <p className="sd-muted">{t('Add capabilities first, then assign employees.')}</p>}
          <p className="sd-muted">{t('Assigned employees receive RFQ invitations for these categories; unassigned RFQs go to the primary company contact.')}</p>
        </fieldset>
      </div>}
    </article>;
  };

  return <>
    <Section title={t('General company contact')}>
      <dl className="sd-fields">
        <div><dt>{t('Contact person')}</dt><dd>{company.contact_name || missing}</dd></div>
        <div><dt>{t('Role')}</dt><dd>{company.designation || missing}</dd></div>
        <div><dt>{t('Business email')}</dt><dd>{company.business_email || missing}</dd></div>
        <div><dt>{t('Business phone')}</dt><dd>{company.business_phone || missing}</dd></div>
      </dl>
      {general.map(renderContact)}
    </Section>
    <Section title={`${t('Category-specific contacts')} (${specific.length})`}>
      {specific.length ? specific.map(renderContact) : <Empty title={t('No category-specific contacts')} hint={editing ? t('Tick categories under a contact to route RFQs to that employee.') : undefined}/>}
    </Section>
    {editing && <Section title={t('Add employee')}>
      <form className="sd-form-grid" onSubmit={event => { event.preventDefault(); if (!newContact.name.trim() && !newContact.email.trim() && !newContact.phone.trim()) return; void immediate('Contact added', async () => { await api(`${base}/contacts`, {method: 'POST', body: json(Object.fromEntries(Object.entries(newContact).filter(([, v]) => v.trim())))}); setNewContact({name: '', role: '', email: '', phone: ''}); }); }}>
        {(['name', 'role', 'email', 'phone'] as const).map(key => <label className="field" key={key}>{t(key === 'name' ? 'Contact name' : key === 'role' ? 'Role' : key === 'email' ? 'Email' : 'Phone')}<input type={key === 'email' ? 'email' : key === 'phone' ? 'tel' : 'text'} value={newContact[key]} onChange={e => setNewContact({...newContact, [key]: e.target.value})}/></label>)}
        <div className="sd-span"><Button type="submit" variant="outline" disabled={busy}><IconPlus size={16}/>{t('Add contact')}</Button></div>
      </form>
    </Section>}
  </>;
}

/* ------------------------------------------------------------------ Locations & Services */

function LocationsTab({data, editing, drafts, setDrafts, missing}: TabProps & {data: SupplierDetail; missing: string}) {
  const {t} = useLocale();
  const coverage = data.coverage;
  const profile = data.profile;
  const locations = drafts.locations ?? (data.locations || []).map(l => ({kind: l.kind, city: l.city || '', address: l.address || '', notes: l.notes || ''}));
  const delivery = drafts.coverage ?? (coverage?.delivery_cities || []).map(c => c.city);
  const services = drafts.services ?? profile?.business_services ?? [];
  const types = profile?.business_types.length ? profile.business_types : profile?.suggested_business_types || [];
  const [cityInput, setCityInput] = useState('');
  const setLocations = (next: typeof locations) => setDrafts(d => ({...d, locations: next}));
  const addCities = () => { const cities = splitList(cityInput); if (!cities.length) return; setDrafts(d => ({...d, coverage: [...new Map([...delivery, ...cities].map(c => [c.toLocaleLowerCase(), c])).values()]})); setCityInput(''); };
  const kinds = (kind: string[]) => (data.locations || []).filter(l => kind.includes(l.kind));
  return <>
    <Section title={t('Company locations')}>
      {editing ? <>
        {locations.map((location, index) => <div className="sd-location-row" key={index}>
          <select value={location.kind} aria-label={t('Location type')} onChange={e => setLocations(locations.map((l, i) => i === index ? {...l, kind: e.target.value} : l))}>{LOCATION_KINDS.map(kind => <option key={kind} value={kind}>{t(humanize(kind))}</option>)}</select>
          <input value={location.city} placeholder={t('City (one per location)')} aria-label={t('City')} onChange={e => setLocations(locations.map((l, i) => i === index ? {...l, city: e.target.value} : l))}/>
          <input value={location.address} placeholder={t('Address')} aria-label={t('Address')} onChange={e => setLocations(locations.map((l, i) => i === index ? {...l, address: e.target.value} : l))}/>
          <Button type="button" variant="ghost" size="icon" aria-label={t('Remove location')} onClick={() => setLocations(locations.filter((_, i) => i !== index))}><IconTrash size={15}/></Button>
        </div>)}
        <Button type="button" variant="outline" size="sm" disabled={!profile?.extension_ready} onClick={() => setLocations([...locations, {kind: locations.length ? 'office' : 'head_office', city: '', address: '', notes: ''}])}><IconPlus size={15}/>{t('Add location')}</Button>
      </> : <dl className="sd-fields">
        <div><dt>{t('Company location')}</dt><dd>{[...kinds(['head_office']).map(l => [l.city, l.address].filter(Boolean).join(' — ')), ...(kinds(['head_office']).length ? [] : coverage?.office_cities || [])].join(', ') || missing}</dd></div>
        <div><dt>{t('Factory locations')}</dt><dd>{kinds(['factory']).map(l => [l.city, l.address].filter(Boolean).join(' — ')).join(', ') || missing}</dd></div>
        <div><dt>{t('Office locations')}</dt><dd>{kinds(['office', 'branch', 'showroom']).map(l => `${[l.city, l.address].filter(Boolean).join(' — ')} (${t(humanize(l.kind))})`).join(', ') || missing}</dd></div>
        <div><dt>{t('Warehouses')}</dt><dd>{kinds(['warehouse']).map(l => [l.city, l.address].filter(Boolean).join(' — ')).join(', ') || missing}</dd></div>
      </dl>}
    </Section>
    <Section title={t('Delivery coverage')}>
      <p className="sd-muted">{t('Each city is matched separately against the RFQ delivery city. This applies to every capability category of the supplier.')}</p>
      {editing ? <>
        <ul className="sd-chips sd-chips-removable">{delivery.map(city => <li key={city}>{city}<button type="button" aria-label={`${t('Remove')} ${city}`} onClick={() => setDrafts(d => ({...d, coverage: delivery.filter(c => c !== city)}))}><IconX size={12}/></button></li>)}</ul>
        <div className="sd-add-row"><input value={cityInput} onChange={e => setCityInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCities(); } }} placeholder={t('Add cities, separated by commas')} aria-label={t('Add delivery city')}/><Button type="button" variant="outline" size="sm" onClick={addCities}><IconPlus size={15}/>{t('Add')}</Button></div>
        {drafts.coverage && !drafts.coverage.length && <p className="notice warn" role="alert">{t('Enter at least one delivery city')}</p>}
      </> : <>
        <Chips items={(coverage?.delivery_cities || []).map(c => c.city)} empty={t('No delivery coverage recorded')}/>
        {coverage?.nationwide && <p><Badge tone="info">{t('Nationwide coverage')}</Badge></p>}
        {Boolean(coverage?.unspecified_groups) && <p className="sd-muted">{coverage!.unspecified_groups} {t('capability categories have no delivery city')}</p>}
        {Boolean(coverage?.legacy_joined_city_rows) && <p className="sd-muted">{t('Some imported capabilities store several cities in one value. Matching already reads each city separately; saving coverage stores one city per capability.')}</p>}
      </>}
    </Section>
    <Section title={t('Business services')}>
      {editing ? <fieldset className="sd-checks" disabled={!profile?.extension_ready}><legend className="sr-only">{t('Business services')}</legend>{BUSINESS_SERVICES.map(service => <label key={service}><input type="checkbox" checked={services.includes(service)} onChange={e => setDrafts(d => ({...d, services: e.target.checked ? [...services, service] : services.filter(s => s !== service)}))}/>{t(humanize(service))}</label>)}</fieldset>
        : <Chips items={services.map(s => t(humanize(s)))} empty={t('No business services recorded')}/>}
    </Section>
    <Section title={t('Supplier type')}>
      <Chips items={types.map(type => t(humanize(type)))} empty={t('Not provided')}/>
      {!profile?.business_types.length && types.length > 0 && <p className="sd-muted">{t('Detected from imported data. Confirm it in Overview while editing.')}</p>}
    </Section>
  </>;
}
