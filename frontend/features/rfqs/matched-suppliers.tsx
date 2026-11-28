"use client";

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  IconAdjustmentsHorizontal, IconArrowUpRight, IconChevronDown, IconCircleCheck, IconMapPin, IconRefresh, IconSearch,
  IconStar, IconStarFilled, IconX,
} from '@tabler/icons-react';
import { api, json } from '@/lib/api/client';
import { useApi } from '@/lib/api/use-api';
import { useServerList } from '@/features/shared/server-list';
import type { Schemas } from '@/lib/api/generated/schema';
import { useSession } from '@/features/auth/session';
import { State } from '@/features/shared/ui';
import { Button } from '@/components/ui/button';
import { useLocale } from '@/lib/locale';
import { useToast } from '@/features/shared/toast';
import {
  activeFilterCount, deliveryLabel, eligibilityReason, emptySupplierQuery, initialViews, invitationResultsPath, isRecommended, isSubmitted,
  isTab, matchEvidence, matchingState, notificationsPath, supplierResultsPath, TABS,
  type Match, type SupplierQuery, type Tab, type ViewState,
} from './matching-model';
import styles from './matched-suppliers.module.css';
import { matchingArabic } from './matching-copy';
import { AdminMatchingDiagnostics } from './admin-matching-diagnostics';
import {
  EmptyRow, ErrorRow, InvitationsPanel, NotificationsPanel, Pager, QuotationsPanel, SkeletonRows,
  type NotificationRecord, type NotificationRecords, type QuotationsSummary, type Translate,
} from './matching-panels';

type Favorite = { supplier_org_id: string; name: string; profile_url?: string | null };
type MatchResults = Schemas['MatchResults'];

const TAB_LABELS: Record<Tab, string> = {
  filtered: 'Filtered Suppliers', total: 'Total Matches', invitations: 'Invitations', quotations: 'Quotations', notifications: 'Notifications',
};
const MATCH_TYPES: Record<string, string> = { exact: 'Exact material', product: 'Product match', capability: 'Subcategory capability', broad: 'Broad category' };
const SORTS: Record<string, string> = { relevance: 'Best match', score: 'Match score', name: 'Name A–Z', name_desc: 'Name Z–A' };

function reasonText(match: Match, tr: Translate) {
  const reasons: Record<string, string> = {
    location: tr('Delivery location not covered'), verification: tr('Company verification required'),
    approval: tr('Capability approval pending'), inactive: tr('Supplier is currently inactive'),
    capability: tr('Required material capability not found'), coverage: tr('Delivery coverage unconfirmed'),
    brand: tr('Mandatory brand not confirmed in saved supplier data'), broad_category: tr('Category capability only — product evidence required'),
    qualifications: tr('Required project qualifications missing'), rejected: tr('Capability was not approved'),
    requirements: tr('Does not meet all RFQ requirements'),
  };
  return reasons[eligibilityReason(match)] || tr('Does not meet all RFQ requirements');
}

function SupplierRow({ match, city, favorite, toggle, expanded, onExpand, tr }: {
  match: Match; city?: string; favorite: boolean; toggle: (id: string, saved: boolean) => Promise<void>;
  expanded: boolean; onExpand: () => void; tr: Translate;
}) {
  const [busy, setBusy] = useState(false);
  const recommended = isRecommended(match);
  const evidence = matchEvidence(match);
  const items = match.matched_items || [];
  // Only recorded product evidence is shown as a product; category-only matches show the category.
  const firstProduct = items[0]?.description || match.matched_categories?.[0] || '';
  const productCount = match.matched_item_count || items.length || match.covered_items?.length || 0;
  const strength = recommended ? match.match_strength || (evidence === 'material' ? 'exact' : evidence === 'description' ? 'product' : evidence === 'category' ? 'broad' : 'capability') : null;
  const coverage = match.service_coverage_status === 'not covered' ? { tone: 'negative', text: tr('Not covered') }
    : !city ? { tone: 'neutral', text: match.service_locations?.[0] || tr('Not specified') }
    : match.service_coverage_status === 'covered' ? { tone: 'positive', text: city }
    : { tone: 'warning', text: tr('Unconfirmed') };
  const quotation = isSubmitted(match.quotation_status) ? { tone: 'positive', text: tr('Quotation received') }
    : match.saved_price_available ? { tone: 'info', text: tr('Saved price') } : { tone: 'neutral', text: tr('Manual pricing') };
  const invitation = match.invitation_status === 'accepted' ? tr('Accepted') : match.invitation_status === 'declined' ? tr('Declined')
    : match.invitation_status ? tr('Invited') : tr('Not invited');
  const itemLevel: Record<string, string> = {
    'exact material': tr('Exact material'), 'product alias': tr('Same product'), 'product family': tr('Same product family'),
    subcategory: tr('Subcategory capability'), 'main category': tr('Broad category'), 'AI-assisted semantic match': tr('AI-assisted'),
  };
  const detailsId = `match-details-${match.id}`;
  const name = match.supplier_name || tr('Supplier profile');
  return <>
    <tr className={styles.supplierRow} data-expanded={expanded} data-recommended={recommended}>
      <td className={styles.expandCol}><button type="button" className={styles.expand} aria-expanded={expanded} aria-controls={detailsId}
        aria-label={`${tr('Matching details')}: ${name}`} onClick={onExpand}><IconChevronDown size={16}/></button></td>
      <td><span className={styles.supplierName}>
        {match.profile_url ? <Link href={match.profile_url}>{name}</Link> : <span>{name}</span>}
        {match.verification_status === 'verified'
          ? <span className={styles.verified} title={tr('Verified company')}><IconCircleCheck size={13}/><span>{tr('Verified')}</span></span>
          : <span className={styles.unverified}>{tr('Verification pending')}</span>}
      </span></td>
      <td className={styles.hideSm}><span className={styles.product} title={firstProduct}>{firstProduct || '—'}</span>
        {productCount > 1 && <small className={styles.muted}>+{productCount - 1} {tr('more')}</small>}</td>
      <td>{strength ? <span className={styles.tag} data-strength={strength}>{tr(MATCH_TYPES[strength] || 'Approved capability')}</span>
        : <span className={styles.tag} data-strength="ineligible" title={reasonText(match, tr)}>{tr('Not eligible')}</span>}</td>
      <td className={styles.hideSm}><span className={styles.coverage} data-tone={coverage.tone}><IconMapPin size={13}/>{coverage.text}</span></td>
      <td className={styles.hideMd}><span className={styles.dotLabel} data-tone={quotation.tone}>{quotation.text}</span></td>
      <td className={styles.hideMd}>{invitation}</td>
      <td className={styles.actionCol}><div className={styles.rowActions}>
        {match.profile_url && <Link className={styles.textLink} href={match.profile_url} aria-label={`${tr('View profile')}: ${name}`}>{tr('Profile')}<IconArrowUpRight size={13} className={styles.flip}/></Link>}
        <button type="button" className={styles.favorite} disabled={busy}
          aria-label={`${favorite ? tr('Remove from favorites') : tr('Save as favorite')}: ${match.supplier_name}`}
          aria-pressed={favorite} onClick={async () => { setBusy(true); try { await toggle(match.supplier_org_id, favorite); } finally { setBusy(false); } }}>
          {favorite ? <IconStarFilled size={16}/> : <IconStar size={16}/>}</button>
      </div></td>
    </tr>
    {expanded && <tr className={styles.detailsRow} id={detailsId}><td colSpan={8}><div className={styles.details}>
      <div>
        <h4>{recommended ? tr('Why this supplier matched') : tr('Why this supplier is not eligible')}</h4>
        {recommended ? items.length ? <ul className={styles.matchedItems} aria-label={tr('Matched materials')}>
          {items.map(item => <li key={item.item_id}><span>{item.description}</span>
            <small>{[item.match_level && itemLevel[item.match_level], item.discipline, item.main_category !== item.discipline ? item.main_category : null, item.subcategory].filter(Boolean).join(' › ')}</small></li>)}
        </ul> : <p>{(match.matched_categories || []).filter(Boolean).join(' · ') || tr('Review capabilities in the supplier profile')}</p>
          : <p className={styles.reason}>{reasonText(match, tr)}</p>}
        {(evidence === 'category' || evidence === 'subcategory') && recommended && <p className={styles.muted}>{tr('Category capability: this RFQ item does not name a specific product.')}</p>}
        {recommended && evidence !== 'material' && <p className={styles.muted}>{tr('Technical compliance is not confirmed by this match; review specifications in the quotation.')}</p>}
        {match.invitation_review_required && <p className={styles.muted}>{tr('Earlier invitation retained; current eligibility needs review.')}</p>}
      </div>
      <dl className={styles.facts}>
        <div><dt>{tr('Matched RFQ items')}</dt><dd>{productCount}</dd></div>
        <div><dt>{tr('Pricing')}</dt><dd>{match.saved_price_available ? tr('Saved price available') : tr('Manual pricing required')}</dd></div>
        <div><dt>{tr('Service locations')}</dt><dd>{match.service_locations?.length ? match.service_locations.join(', ') : '—'}</dd></div>
        <div><dt>{tr('Brand')}</dt><dd>{tr(match.brand_requirement_status?.startsWith('saved') ? 'Matches saved product data' : 'Confirm in quotation')}</dd></div>
        {match.invitation_status && <div><dt>{tr('Delivery')}</dt><dd>{tr('Email')}: {tr(deliveryLabel(match.email_status))} · {tr('SMS')}: {tr(deliveryLabel(match.sms_status))}</dd></div>}
        <div><dt>{tr('Quotation')}</dt><dd>{match.quotation_status ? tr(match.quotation_status) : tr('Not submitted')}</dd></div>
      </dl>
    </div></td></tr>}
  </>;
}

function SupplierToolbar({ query, setQuery, options, tr }: {
  query: SupplierQuery; setQuery: (next: Partial<SupplierQuery>) => void; options?: MatchResults['filter_options']; tr: Translate;
}) {
  const [draft, setDraft] = useState(query.q);
  const [location, setLocation] = useState(query.location);
  const [brand, setBrand] = useState(query.brand);
  const [open, setOpen] = useState(() => activeFilterCount(query) > 0);
  // Text inputs commit after a short pause so typing does not send a request per keystroke.
  useEffect(() => { setDraft(query.q); }, [query.q]);
  useEffect(() => { setLocation(query.location); }, [query.location]);
  useEffect(() => { setBrand(query.brand); }, [query.brand]);
  useEffect(() => {
    if (draft.trim() === query.q && location.trim() === query.location && brand.trim() === query.brand) return;
    const timer = window.setTimeout(() => setQuery({ q: draft.trim(), location: location.trim(), brand: brand.trim() }), 400);
    return () => window.clearTimeout(timer);
  }, [draft, location, brand, query.q, query.location, query.brand]); // eslint-disable-line react-hooks/exhaustive-deps
  const filters = activeFilterCount(query);
  const subcategories = (options?.subcategories || []).filter(sub => !query.main_category_id || sub.parent_id === query.main_category_id);
  return <div className={styles.filterBar}>
    <div className={styles.filterRow}>
      <label className={styles.search}><IconSearch size={15} aria-hidden="true"/><span className={styles.srOnly}>{tr('Search suppliers')}</span>
        <input type="search" value={draft} maxLength={120} placeholder={tr('Search supplier name')} onChange={event => setDraft(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter') setQuery({ q: draft.trim() }); }}/></label>
      <label className={styles.inlineField}><span className={styles.srOnly}>{tr('Match type')}</span>
        <select value={query.match_type} onChange={event => setQuery({ match_type: event.target.value })}>
          <option value="">{tr('All match types')}</option>
          {Object.entries(MATCH_TYPES).map(([key, label]) => <option key={key} value={key}>{tr(label)}</option>)}
        </select></label>
      <label className={styles.inlineField}><span className={styles.srOnly}>{tr('Quotation availability')}</span>
        <select value={query.quotation} onChange={event => setQuery({ quotation: event.target.value })}>
          <option value="">{tr('Any quotation status')}</option>
          <option value="received">{tr('Quotation received')}</option><option value="saved_price">{tr('Saved price available')}</option>
          <option value="manual">{tr('Manual pricing required')}</option>
        </select></label>
      <label className={styles.inlineField}><span className={styles.srOnly}>{tr('Sort by')}</span>
        <select value={query.sort} onChange={event => setQuery({ sort: event.target.value })}>
          {Object.entries(SORTS).map(([key, label]) => <option key={key} value={key}>{tr(label)}</option>)}
        </select></label>
      <button type="button" className={styles.filterToggle} aria-expanded={open} onClick={() => setOpen(value => !value)}>
        <IconAdjustmentsHorizontal size={15}/>{tr('More filters')}{filters > 0 && <span className={styles.count}>{filters}</span>}</button>
      {(filters > 0 || query.q) && <button type="button" className={styles.clear} onClick={() => setQuery({ ...emptySupplierQuery(), sort: query.sort, limit: query.limit })}>
        <IconX size={13}/>{tr('Clear')}</button>}
    </div>
    {open && <div className={styles.filterRow} role="group" aria-label={tr('Supplier filters')}>
      <label className={styles.inlineField}><span className={styles.srOnly}>{tr('Discipline')}</span>
        <select value={query.discipline} onChange={event => setQuery({ discipline: event.target.value })}>
          <option value="">{tr('All disciplines')}</option>{(options?.disciplines || []).map(name => <option key={name} value={name}>{name}</option>)}
        </select></label>
      <label className={styles.inlineField}><span className={styles.srOnly}>{tr('Main category')}</span>
        <select value={query.main_category_id} onChange={event => setQuery({ main_category_id: event.target.value, subcategory_id: '' })}>
          <option value="">{tr('All main categories')}</option>{(options?.main_categories || []).map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select></label>
      <label className={styles.inlineField}><span className={styles.srOnly}>{tr('Sub category')}</span>
        <select value={query.subcategory_id} onChange={event => setQuery({ subcategory_id: event.target.value })}>
          <option value="">{tr('All sub categories')}</option>{subcategories.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select></label>
      <label className={styles.textField}><span className={styles.srOnly}>{tr('Location')}</span>
        <input value={location} maxLength={100} placeholder={tr('Location')} onChange={event => setLocation(event.target.value)}/></label>
      <label className={styles.textField}><span className={styles.srOnly}>{tr('Brand')}</span>
        <input value={brand} maxLength={100} placeholder={tr('Brand')} onChange={event => setBrand(event.target.value)}/></label>
    </div>}
  </div>;
}

function SupplierPanel({ view, query, setQuery, result, city, saved, toggleFavorite, busyMatching, tr, arabic }: {
  view: 'filtered' | 'total'; query: SupplierQuery; setQuery: (next: Partial<SupplierQuery>) => void;
  result: { data?: MatchResults; loading: boolean; fetching: boolean; error?: Error; reload: () => void };
  city?: string; saved: (id: string) => boolean; toggleFavorite: (id: string, saved: boolean) => Promise<void>;
  busyMatching: boolean; tr: Translate; arabic: boolean;
}) {
  const [expanded, setExpanded] = useState('');
  const data = result.data;
  const rows = data?.matches || [];
  const total = data?.page_total ?? (view === 'total' ? data?.total_candidates : data?.matched_suppliers) ?? 0;
  const filtered = activeFilterCount(query) > 0 || !!query.q;
  const columns = 8;
  const nameSort = query.sort === 'name' ? 'ascending' : query.sort === 'name_desc' ? 'descending' : 'none';
  return <div className={styles.panelBody}>
    <SupplierToolbar query={query} setQuery={setQuery} options={data?.filter_options} tr={tr}/>
    {view === 'total' && <p className={styles.note}>{tr('Total matches include candidates excluded by delivery location, brand or product evidence. Only filtered suppliers are invited.')}</p>}
    <div className={styles.tableWrap} data-busy={result.fetching && !!data} aria-busy={result.fetching}>
      <table className={styles.table}>
        <caption className={styles.srOnly}>{view === 'total' ? tr('Total Matches') : tr('Filtered Suppliers')}</caption>
        <thead><tr>
          <th scope="col" className={styles.expandCol}><span className={styles.srOnly}>{tr('Details')}</span></th>
          <th scope="col" aria-sort={nameSort}><button type="button" className={styles.sortButton}
            onClick={() => setQuery({ sort: query.sort === 'name' ? 'name_desc' : 'name' })}>{tr('Supplier')}<IconChevronDown size={13} data-dir={nameSort}/></button></th>
          <th scope="col" className={styles.hideSm}>{tr('Matched product')}</th><th scope="col">{tr('Match type')}</th>
          <th scope="col" className={styles.hideSm}>{tr('Location coverage')}</th><th scope="col" className={styles.hideMd}>{tr('Quotation')}</th>
          <th scope="col" className={styles.hideMd}>{tr('Invitation')}</th><th scope="col" className={styles.actionCol}><span className={styles.srOnly}>{tr('Actions')}</span></th>
        </tr></thead>
        <tbody>
          {result.error && !data ? <ErrorRow error={result.error} retry={result.reload} columns={columns} tr={tr}/>
            : result.loading && !data ? <SkeletonRows rows={query.limit} columns={columns}/>
            : !rows.length ? <EmptyRow columns={columns}>{filtered ? <><strong>{tr('No suppliers match these filters.')}</strong>
              <button type="button" className={styles.textLink} onClick={() => setQuery({ ...emptySupplierQuery(), sort: query.sort, limit: query.limit })}>{tr('Clear filters')}</button></>
              : busyMatching ? tr('Matching is underway. Suppliers will appear here.')
              : view === 'total' ? tr('No candidate suppliers recorded yet.')
              : data?.explanation || tr('No eligible suppliers currently match this RFQ.')}</EmptyRow>
            : rows.map(match => <SupplierRow key={match.id} match={match} city={city} favorite={saved(match.supplier_org_id)} toggle={toggleFavorite}
              expanded={expanded === match.id} onExpand={() => setExpanded(value => value === match.id ? '' : match.id)} tr={tr}/>)}
        </tbody>
      </table>
    </div>
    <Pager offset={query.offset} limit={query.limit} total={total} noun="suppliers" label={view === 'total' ? tr('Total match pages') : tr('Matched supplier pages')}
      busy={result.fetching} onChange={page => setQuery(page)} tr={tr} arabic={arabic}/>
  </div>;
}

export function Matching({
  id,
  embedded = false,
  rfqDetail,
  categoriesData,
}: {
  id?: string;
  embedded?: boolean;
  rfqDetail?: Schemas['RFQDetail'];
  categoriesData?: Schemas['CategoryOut'][];
}) {
  const { t, arabic } = useLocale();
  const tr: Translate = en => arabic ? matchingArabic[en] || t(en) : en;
  const { org, user } = useSession();
  const toast = useToast();
  const [selected, setSelected] = useState(() => id || (typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('rfq') || '' : ''));
  const currentId = id || selected;
  const [tab, setTabState] = useState<Tab>(() => {
    const value = !embedded && typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('tab') : null;
    return isTab(value) ? value : 'filtered';
  });
  // Per-tab pagination and filters survive tab switches; a different RFQ starts fresh.
  const [viewState, setViewState] = useState<ViewState>(() => initialViews(currentId));
  const views = viewState.rfq === currentId ? viewState : initialViews(currentId);
  const [favoriteOverrides, setFavoriteOverrides] = useState<Record<string, boolean>>({});
  const [selectedReminders, setSelectedReminders] = useState<string[]>([]);
  const [reminding, setReminding] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const [stalled, setStalled] = useState(false);
  const [pollRevision, setPollRevision] = useState(0);
  const [pollState] = useState(() => ({ key: '', started: 0, lastAdvance: 0, evaluated: 0, count: 0, wasBusy: false, rfq: '' }));
  // Previous rows stay visible while a new page or filter loads, never across RFQs.
  const keepPreviousData = pollState.rfq === currentId;

  const qRfqs = useServerList<Schemas['RFQOut']>(embedded ? null : user.is_admin ? 'rfqs' : org ? `rfqs?org_id=${org.id}` : null, 'Search RFQs');
  const detailQuery = useApi<Schemas['RFQDetail']>(currentId && !rfqDetail ? `rfqs/${currentId}` : null, { staleTimeMs: 120000 });
  const taxonomyQuery = useApi<Schemas['CategoryOut'][]>(currentId && !categoriesData ? 'taxonomy/categories' : null, { staleTimeMs: 1800000 });
  const effectiveDetail = rfqDetail ?? detailQuery.data;
  const effectiveCategories = categoriesData ?? taxonomyQuery.data;
  // The filtered page also carries the RFQ-wide counts and matching status for the header.
  const results = useApi<MatchResults>(currentId ? supplierResultsPath(currentId, 'filtered', views.filtered) : null, { staleTimeMs: 4000, keepPreviousData });
  const totalResults = useApi<MatchResults>(currentId && tab === 'total' ? supplierResultsPath(currentId, 'total', views.total) : null, { staleTimeMs: 15000, keepPreviousData });
  const invitationResults = useApi<MatchResults>(currentId && tab === 'invitations' ? invitationResultsPath(currentId, views.invitations) : null, { staleTimeMs: 10000, keepPreviousData });
  const quotationsSummary = useApi<QuotationsSummary>(currentId && tab === 'quotations' ? `matching/rfqs/${currentId}/quotations-summary` : null, { staleTimeMs: 15000 });
  const notifications = useApi<NotificationRecords>(currentId && tab === 'notifications' ? notificationsPath(currentId, views.notifications) : null, { staleTimeMs: 15000, keepPreviousData });
  const favorites = useApi<Favorite[]>(org && currentId && (tab === 'filtered' || tab === 'total') ? `organizations/${org.id}/favorites?limit=100&offset=0` : null, { staleTimeMs: 300000 });
  const rfq = effectiveDetail?.rfq;
  const data = results.data;
  const state = matchingState(data, stalled);
  const terminalStatus = data?.matching_status === 'completed' || data?.matching_status === 'failed';
  const busyProgress = !terminalStatus && (data?.progress === 'published' || data?.progress === 'matching_queued' || data?.progress === 'matching_in_progress');
  const activeQuery = tab === 'total' ? totalResults : tab === 'invitations' ? invitationResults : tab === 'quotations' ? quotationsSummary : tab === 'notifications' ? notifications : null;

  const updateViews = (change: (current: ViewState) => ViewState) => setViewState(previous => change(previous.rfq === currentId ? previous : initialViews(currentId)));
  const setSupplierQuery = (view: 'filtered' | 'total') => (next: Partial<SupplierQuery>) => updateViews(current => {
    // Paging passes its own offset; any filter, search or sort change returns to page one.
    return { ...current, [view]: { ...current[view], ...next, offset: next.offset ?? 0 } };
  });
  const setTab = (next: Tab) => {
    setTabState(next);
    if (!embedded && typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      if (next === 'filtered') url.searchParams.delete('tab'); else url.searchParams.set('tab', next);
      window.history.replaceState({}, '', url.toString());
    }
  };
  const choose = (value: string) => {
    setSelected(value); setViewState(initialViews(value));
    setSelectedReminders([]); setStalled(false); setPollRevision(revision => revision + 1);
    if (!embedded && typeof window !== 'undefined') {
      const url = new URL(window.location.href); url.searchParams.set('rfq', value);
      window.history.replaceState({}, '', url.toString());
    }
  };
  useEffect(() => { pollState.rfq = currentId; }, [currentId, pollState]);
  useEffect(() => { if (!currentId && qRfqs.rows[0]) choose(qRfqs.rows[0].id); }, [currentId, qRfqs.rows]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setSelectedReminders([]); setStalled(false); }, [currentId]);
  useEffect(() => { setFavoriteOverrides({}); }, [org?.id]);
  // Polling reads the latest query handles through a ref so that a poll's own
  // fetching flag never restarts the timer (which would defeat the backoff).
  const live = useRef({ results, activeQuery });
  live.current = { results, activeQuery };
  useEffect(() => {
    if (busyProgress) {
      pollState.wasBusy = true;
    } else if (pollState.wasBusy && terminalStatus) {
      // Matching finished: refresh the open tab once, then polling stops.
      pollState.wasBusy = false;
      live.current.activeQuery?.reload();
    }
    if (!busyProgress || state.inconsistent) return;
    const key = `${currentId}:${pollRevision}`;
    if (pollState.key !== key) {
      pollState.key = key; pollState.started = Date.now(); pollState.lastAdvance = pollState.started;
      pollState.evaluated = data?.suppliers_evaluated || 0; pollState.count = 0;
    }
    if ((data?.suppliers_evaluated || 0) > pollState.evaluated) {
      pollState.evaluated = data?.suppliers_evaluated || 0;
      pollState.lastAdvance = Date.now();
    }
    const delay = () => pollState.count === 0 ? 1500
      : pollState.count === 1 ? 2000 : Math.min(15_000, 3000 * 2 ** Math.min(pollState.count - 1, 3));
    let timer: number;
    const poll = () => {
      if (Date.now() - pollState.lastAdvance >= 60_000 || Date.now() - pollState.started >= 180_000 || pollState.count >= 24) { setStalled(true); return; }
      const current = live.current.results;
      if (document.visibilityState === 'visible' && !current.fetching) {
        current.reload();
        pollState.count += 1;
      }
      timer = window.setTimeout(poll, delay());
    };
    timer = window.setTimeout(poll, delay());
    return () => window.clearTimeout(timer);
  }, [currentId, busyProgress, terminalStatus, state.inconsistent, pollRevision, data?.suppliers_evaluated, pollState]);
  const invitations = invitationResults.data?.invitations || [];
  const waitingReminder = invitations.some(row => row.next_reminder_at && new Date(row.next_reminder_at).getTime() > Date.now());
  useEffect(() => {
    if (!waitingReminder) return;
    const timer = window.setInterval(() => setClock(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, [waitingReminder]);

  const refresh = () => {
    setStalled(false); setPollRevision(v => v + 1); results.reload();
    if (!rfqDetail) detailQuery.reload();
    activeQuery?.reload();
  };
  const toggleFavorite = async (supplierId: string, saved: boolean) => {
    if (!org) return;
    try {
      await api(`organizations/${org.id}/favorites/${supplierId}`, { method: saved ? 'DELETE' : 'PUT' });
      setFavoriteOverrides(previous => ({ ...previous, [supplierId]: !saved }));
      toast.success(saved ? t('Removed from favorites') : t('Saved as favorite'));
    } catch { toast.error(t('Action failed'), tr('Could not update favorites.')); }
  };
  const favoriteIds = new Set((favorites.data || []).map(row => row.supplier_org_id));
  const saved = (supplierId: string) => favoriteOverrides[supplierId] ?? favoriteIds.has(supplierId);
  const categories = useMemo(() => new Map((effectiveCategories || []).map(c => [c.id, arabic && c.name_ar ? c.name_ar : c.name_en])), [effectiveCategories, arabic]);
  const mainCategories = [...new Set((effectiveDetail?.items || []).map(item => categories.get(item.main_category_id || '')).filter(Boolean))].join(' · ');
  const quantities = Object.entries((effectiveDetail?.items || []).reduce<Record<string, number>>((totals, item) => {
    totals[item.unit] = (totals[item.unit] || 0) + Number(item.quantity); return totals;
  }, {})).map(([unit, quantity]) => `${new Intl.NumberFormat(arabic ? 'ar-SA' : 'en-GB').format(quantity)} ${unit}`).join(' · ');
  // Cooldowns are per supplier: the backend marks each pending invitation it will accept.
  // Closed, cancelled and expired RFQs never offer reminder actions (the API returns 409).
  const remindersEnabled = data?.reminders_enabled !== false;
  const remindersDisabledReason = data?.reminders_disabled_reason || tr('Reminders are not available for this RFQ.');
  const eligibleReminderIds = new Set(invitations.filter(row => remindersEnabled && row.reminder_eligible && !isSubmitted(row.quotation_status)
    && (!row.next_reminder_at || new Date(row.next_reminder_at).getTime() <= clock)).map(row => row.id));
  const reminders = selectedReminders.filter(value => eligibleReminderIds.has(value));
  // ids: one supplier, the selected suppliers, or undefined = every pending supplier.
  const sendReminders = async (ids?: string[]) => {
    if (reminding || !remindersEnabled || (ids && !ids.length)) return;
    setReminding(true);
    try {
      const outcome = await api<{ queued: number; message: string }>(`rfqs/${currentId}/reminders`,
        { method: 'POST', body: json(ids ? { invitation_ids: ids } : {}) });
      if (outcome.queued) toast.success(tr('Reminders queued'), outcome.message);
      else toast.error(tr('No reminder was queued'), outcome.message);
      setSelectedReminders([]); invitationResults.reload(); results.reload(); notifications.reload();
    } catch (error) { toast.error(t('Action failed'), error instanceof Error ? error.message : tr('Could not record reminders. Please refresh and try again.')); }
    finally { setReminding(false); }
  };
  // Admins retry the delivery itself; buyers can only queue a fresh reminder to the supplier.
  const retryNotification = async (record: NotificationRecord) => {
    try {
      if (user.is_admin) {
        await api(`notifications/admin/outbox/${record.id}/retry`, { method: 'POST' });
        toast.success(tr('Delivery retry queued'));
      } else if (record.invitation_id) {
        const outcome = await api<{ queued: number; message: string }>(`rfqs/${currentId}/reminders`,
          { method: 'POST', body: json({ invitation_ids: [record.invitation_id] }) });
        if (outcome.queued) toast.success(tr('Reminders queued'), outcome.message);
        else toast.error(tr('No reminder was queued'), outcome.message);
      }
      notifications.reload();
    } catch (error) { toast.error(t('Action failed'), error instanceof Error ? error.message : tr('Could not retry this notification.')); }
  };
  const stateLabels: Record<string, string> = {
    loading: tr('Loading status'), queued: tr('Matching in progress'), processing: tr('Matching in progress'),
    completed: tr('Matching completed'), no_eligible: tr('No eligible suppliers found'),
    failed: tr('Matching failed'), review: tr('Status needs review'),
    draft: tr('Draft RFQ'), closed: tr('RFQ closed'),
  };
  const number = (value?: number | null) => value == null ? '—' : new Intl.NumberFormat(arabic ? 'ar-SA' : 'en-GB').format(value);
  const stats = [
    { label: tr('Total matched suppliers'), value: data?.total_candidates, tab: 'total' as Tab },
    { label: tr('Filtered suppliers'), value: data?.matched_suppliers, tab: 'filtered' as Tab },
    { label: tr('Invitations recorded'), value: data?.invited_suppliers, tab: 'invitations' as Tab },
    { label: tr('Quotations received'), value: data?.quotations_received, tab: 'quotations' as Tab },
  ];
  const onTabKey = (event: React.KeyboardEvent) => {
    const index = TABS.indexOf(tab);
    const forward = arabic ? 'ArrowLeft' : 'ArrowRight';
    const back = arabic ? 'ArrowRight' : 'ArrowLeft';
    const next = event.key === forward ? TABS[(index + 1) % TABS.length] : event.key === back ? TABS[(index + TABS.length - 1) % TABS.length]
      : event.key === 'Home' ? TABS[0] : event.key === 'End' ? TABS[TABS.length - 1] : null;
    if (!next) return;
    event.preventDefault(); setTab(next);
    document.getElementById(`matching-tab-${next}`)?.focus();
  };

  return <div className={styles.workspace} dir={arabic ? 'rtl' : 'ltr'} data-embedded={embedded}>
    <header className={styles.header}>
      <div className={styles.headerTop}>
        <div className={styles.titleBlock}>
          {embedded ? <h2>{tr('Matched suppliers')}</h2> : <h1>{tr('Matched suppliers')}</h1>}
          {currentId && <span role="status" className={styles.status} data-state={state.label}>{stateLabels[state.label]}</span>}
        </div>
        <div className={styles.headerActions}>
          {currentId && <Link className={styles.textLink} href={`/app/buyer/compare?rfq=${currentId}`}>{tr('Compare quotations')}<IconArrowUpRight size={14} className={styles.flip}/></Link>}
          <Button variant="outline" size="sm" onClick={refresh} disabled={!currentId || results.fetching}><IconRefresh size={15}/>{t('Refresh')}</Button>
        </div>
      </div>
      {!embedded && <div className={styles.selector} aria-label={tr('RFQ selection')} role="group">
        <label htmlFor="matching-rfq">{tr('RFQ')}</label>
        <select id="matching-rfq" value={currentId} onChange={event => choose(event.target.value)}>
          <option value="">{tr('Select an RFQ')}</option>
          {rfq && !qRfqs.rows.some(row => row.id === rfq.id) && <option value={rfq.id}>{rfq.reference} · {rfq.project_name || rfq.title}</option>}
          {qRfqs.rows.map(item => <option key={item.id} value={item.id}>{item.reference} · {item.project_name || item.title}</option>)}
        </select>
        <details className={styles.rfqSearch}><summary>{tr('Find another RFQ')}</summary>{qRfqs.controls}</details>
      </div>}
      {!embedded && <State loading={qRfqs.loading} error={qRfqs.error} retry={qRfqs.reload}/>}
      {rfq && <div className={styles.rfqMeta}>
        {!embedded && <strong>{rfq.project_name || rfq.title}</strong>}
        <span><IconMapPin size={13}/>{rfq.city || tr('Delivery city not specified')}</span>
        <span>{mainCategories || tr('Category not specified')}</span><span>{quantities || tr('No quantities recorded')}</span>
        <span>{t(rfq.status.replaceAll('_', ' '))}</span>
        {!embedded && <span className={styles.metaLinks}><Link href={`/app/buyer/rfqs/${rfq.id}`}>{tr('View RFQ')}</Link>
          {['draft', 'published', 'collecting_quotes'].includes(rfq.status) && <Link href={`/app/buyer/rfqs/${rfq.id}?edit=1`}>{tr('Edit RFQ')}</Link>}</span>}
      </div>}
      {!rfqDetail && <State error={detailQuery.error} retry={detailQuery.reload}/>}
      {currentId && <dl className={styles.stats} aria-label={tr('RFQ summary')}>
        {stats.map(stat => <div key={stat.label}><dt>{stat.label}</dt><dd>{results.loading && !data ? <span className={styles.statSkeleton} aria-hidden="true"/> : number(stat.value)}</dd></div>)}
      </dl>}
    </header>
    {!currentId && !qRfqs.loading && <p className={styles.empty}>{tr('Select an RFQ to see supplier recommendations.')}</p>}
    {currentId && <>
      {results.error && !data && <State error={results.error} retry={results.reload}/>}
      {(state.inconsistent || stalled) && <div role="alert" className={styles.warning}>{state.inconsistent
        ? tr('The matching status and recorded results disagree. Review the available results and refresh; contact support if this continues.')
        : tr('Matching status has not updated. Automatic refresh is paused; these are the latest available results. Refresh or contact support.')}</div>}
      {state.label === 'failed' && <p role="alert" className={styles.warning}>{data?.explanation || tr('Matching could not finish. Refresh to check for an update or contact support.')}</p>}
      <div className={styles.tabs} role="tablist" aria-label={tr('Matched supplier sections')} onKeyDown={onTabKey}>
        {TABS.map(key => <button key={key} type="button" role="tab" id={`matching-tab-${key}`} aria-selected={tab === key}
          aria-controls="matching-tabpanel" tabIndex={tab === key ? 0 : -1} onClick={() => setTab(key)}>{tr(TAB_LABELS[key])}</button>)}
      </div>
      <section className={styles.panel} role="tabpanel" id="matching-tabpanel" aria-labelledby={`matching-tab-${tab}`}>
        {tab === 'filtered' && <SupplierPanel view="filtered" query={views.filtered} setQuery={setSupplierQuery('filtered')} result={results}
          city={rfq?.city} saved={saved} toggleFavorite={toggleFavorite} busyMatching={state.busy} tr={tr} arabic={arabic}/>}
        {tab === 'total' && <SupplierPanel view="total" query={views.total} setQuery={setSupplierQuery('total')} result={totalResults}
          city={rfq?.city} saved={saved} toggleFavorite={toggleFavorite} busyMatching={state.busy} tr={tr} arabic={arabic}/>}
        {tab === 'invitations' && <InvitationsPanel data={invitationResults.data} loading={invitationResults.loading} fetching={invitationResults.fetching}
          error={invitationResults.error} reload={invitationResults.reload} page={views.invitations}
          setPage={page => updateViews(current => ({ ...current, invitations: page }))}
          selected={reminders} setSelected={setSelectedReminders} eligible={eligibleReminderIds} reminding={reminding}
          remindersEnabled={remindersEnabled} remindersDisabledReason={remindersDisabledReason} sendReminders={sendReminders}
          pendingTotal={invitationResults.data?.pending_suppliers ?? data?.pending_suppliers ?? 0} compareHref={`/app/buyer/compare?rfq=${currentId}`} tr={tr} arabic={arabic}/>}
        {tab === 'quotations' && <QuotationsPanel data={quotationsSummary.data} loading={quotationsSummary.loading} fetching={quotationsSummary.fetching}
          error={quotationsSummary.error} reload={quotationsSummary.reload} page={views.quotations}
          setPage={page => updateViews(current => ({ ...current, quotations: page }))} rfqId={currentId} tr={tr} t={t} arabic={arabic}/>}
        {tab === 'notifications' && <NotificationsPanel data={notifications.data} loading={notifications.loading} fetching={notifications.fetching}
          error={notifications.error} reload={notifications.reload} query={views.notifications}
          setQuery={query => updateViews(current => ({ ...current, notifications: query }))} retry={retryNotification}
          admin={!!user.is_admin} remindersEnabled={remindersEnabled} tr={tr} arabic={arabic}/>}
      </section>
      {user.is_admin && !embedded && <AdminMatchingDiagnostics rfqId={currentId} onRerunDone={results.reload} />}
    </>}
  </div>;
}

