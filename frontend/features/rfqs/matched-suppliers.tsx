"use client";

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { IconCircleCheck, IconRefresh, IconStar, IconStarFilled, IconMapPin, IconArrowUpRight } from '@tabler/icons-react';
import { api, json } from '@/lib/api/client';
import { useApi } from '@/lib/api/use-api';
import { useServerList } from '@/features/shared/server-list';
import type { Schemas } from '@/lib/api/generated/schema';
import { useSession } from '@/features/auth/session';
import { State } from '@/features/shared/ui';
import { Button } from '@/components/ui/button';
import { useLocale } from '@/lib/locale';
import { useToast } from '@/features/shared/toast';
import { eligibilityReason, isRecommended, isSubmitted, matchEvidence, matchingState, PAGE_SIZE, type Match } from './matching-model';
import styles from './matched-suppliers.module.css';
import { matchingArabic } from './matching-copy';

type Favorite = { supplier_org_id: string; name: string };
type Translate = (english: string) => string;

function SupplierRow({ match, city, favorite, toggle, tr }: {
  match: Match; city?: string; favorite: boolean; toggle: (id: string, saved: boolean) => Promise<void>;
  tr: Translate;
}) {
  const [busy, setBusy] = useState(false);
  const evidence = matchEvidence(match);
  const recommended = isRecommended(match);
  const reasons: Record<string, string> = {
    location: tr('Delivery location not covered'),
    verification: tr('Company verification required'),
    approval: tr('Capability approval pending'),
    inactive: tr('Supplier is currently inactive'),
    capability: tr('Required material capability not found'),
    coverage: tr('Delivery coverage unconfirmed'),
    qualifications: tr('Required project qualifications missing'),
    rejected: tr('Capability was not approved'),
    requirements: tr('Does not meet all RFQ requirements'),
  };
  const level = evidence === 'material' ? tr('Exact material capability')
    : evidence === 'subcategory' ? tr('Subcategory capability')
    : evidence === 'category' ? tr('Main category capability') : null;
  const relevant = (match.matched_categories || []).filter(Boolean).join(' \u00b7 ');
  const coverage = !city ? tr('Delivery location not specified')
    : match.service_coverage_status === 'covered' ? `${tr('Delivery covered')} \u00b7 ${city}`
    : match.service_coverage_status === 'not covered' ? tr('Delivery location not covered')
    : tr('Delivery coverage unconfirmed');
  return <article className={styles.supplier} data-recommended={recommended}>
    <div className={styles.identity}>
      <span className={styles.avatar} aria-hidden="true">{(match.supplier_name || '').slice(0, 2)}</span>
      <div><h3><Link href={`/suppliers/${encodeURIComponent(match.supplier_org_id)}`}>{match.supplier_name || tr('Supplier profile')}</Link></h3>
        <span className={styles.verification}>{match.verification_status === 'verified'
          ? <><IconCircleCheck size={14}/>{tr('Verified company')}</>
          : tr('Verification pending')}</span>
      </div>
    </div>
    <div className={styles.evidence}>
      {recommended ? <><strong>{level || tr('Approved capability match')}</strong>
        <span>{relevant || tr('Review capabilities in the supplier profile')}</span>
        {evidence === 'material' && !!match.covered_items?.length && <span>{match.covered_items.slice(0, 2).join(' \u00b7 ')}</span>}
        {evidence === 'category' && <small>{tr('Confirm the specific product in the quotation.')}</small>}
      </> : <><strong className={styles.reason}>{reasons[eligibilityReason(match)]}</strong>
        {match.invitation_review_required && <small>{tr('Earlier invitation retained; current eligibility needs review.')}</small>}</>}
      <span className={styles.coverage}><IconMapPin size={13}/>{coverage}</span>
    </div>
    <div className={styles.supplierActions}>
      <span className={styles.invitationStatus}>{match.invitation_status === 'accepted' ? tr('Invitation accepted')
        : match.invitation_status === 'declined' ? tr('Invitation declined')
        : match.invitation_status ? tr('Invitation recorded') : tr('No invitation recorded')}</span>
      <div><Link className={styles.profileLink} href={`/suppliers/${encodeURIComponent(match.supplier_org_id)}`}>{tr('View profile')}<IconArrowUpRight size={15}/></Link>
        <button type="button" className={styles.favorite} disabled={busy}
          aria-label={`${favorite ? tr('Remove from favorites') : tr('Save as favorite')}: ${match.supplier_name}`}
          aria-pressed={favorite} onClick={async () => { setBusy(true); try { await toggle(match.supplier_org_id, favorite); } finally { setBusy(false); } }}>
          {favorite ? <IconStarFilled size={19}/> : <IconStar size={19}/>}</button>
      </div>
    </div>
  </article>;
}

export function Matching({ id, embedded = false }: { id?: string; embedded?: boolean }) {
  const { t, arabic } = useLocale();
  const tr: Translate = en => arabic ? matchingArabic[en] || t(en) : en;
  const { org, user } = useSession();
  const toast = useToast();
  const [selected, setSelected] = useState(() => id || (typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('rfq') || '' : ''));
  const currentId = id || selected;
  const [page, setPage] = useState({ rfq: currentId, offset: 0 });
  const [invPage, setInvPage] = useState({ rfq: currentId, offset: 0 });
  const offset = page.rfq === currentId ? page.offset : 0;
  const invitationOffset = invPage.rfq === currentId ? invPage.offset : 0;
  const [tab, setTab] = useState<'recommended' | 'favorites'>('recommended');
  const [favoriteOffset, setFavoriteOffset] = useState(0);
  const [favoriteOverrides, setFavoriteOverrides] = useState<Record<string, boolean>>({});
  const [knownFavorites, setKnownFavorites] = useState<Record<string, boolean>>({});
  const [favoriteBusy, setFavoriteBusy] = useState<string | null>(null);
  const [selectedReminders, setSelectedReminders] = useState<string[]>([]);
  const [reminding, setReminding] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const [stalled, setStalled] = useState(false);
  const [pollRevision, setPollRevision] = useState(0);

  const qRfqs = useServerList<Schemas['RFQOut']>(embedded ? null : user.is_admin ? 'rfqs' : org ? `rfqs?org_id=${org.id}` : null, 'Search RFQs');
  const detail = useApi<Schemas['RFQDetail']>(currentId ? `rfqs/${currentId}` : null);
  const taxonomy = useApi<Schemas['CategoryOut'][]>(currentId ? 'taxonomy/categories' : null);
  const resultPath = (start: number) => `matching/rfqs/${currentId}/results?limit=${PAGE_SIZE}&offset=${start}`;
  const results = useApi<Schemas['MatchResults']>(currentId ? resultPath(offset) : null);
  // Separate invitation paging only needs another bounded request when its page differs.
  const invitationResults = useApi<Schemas['MatchResults']>(currentId && invitationOffset !== offset ? resultPath(invitationOffset) : null);
  const progressResult = invitationOffset === offset ? results : invitationResults;
  const favorites = useApi<Favorite[]>(org ? `organizations/${org.id}/favorites?limit=26&offset=${favoriteOffset}` : null);
  const rfq = detail.data?.rfq;
  const data = results.data;
  const invitations = progressResult.data?.invitations || [];
  const state = matchingState(data, stalled);
  const busyProgress = data?.progress === 'matching_queued' || data?.progress === 'matching_in_progress';

  const choose = (value: string) => {
    setSelected(value);
    setPage({ rfq: value, offset: 0 }); setInvPage({ rfq: value, offset: 0 });
    setSelectedReminders([]); setStalled(false); setPollRevision(value => value + 1);
    if (!embedded && typeof window !== 'undefined') {
      const url = new URL(window.location.href); url.searchParams.set('rfq', value);
      window.history.replaceState({}, '', url.toString());
    }
  };
  useEffect(() => { if (!currentId && qRfqs.rows[0]) choose(qRfqs.rows[0].id); }, [currentId, qRfqs.rows]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setSelectedReminders([]); setStalled(false); }, [currentId]);
  useEffect(() => { setFavoriteOverrides({}); setKnownFavorites({}); setFavoriteOffset(0); }, [org?.id]);
  useEffect(() => {
    const rows = favorites.data;
    if (rows) setKnownFavorites(previous => ({ ...previous,
      ...Object.fromEntries(rows.map(row => [row.supplier_org_id, true])) }));
  }, [favorites.data]);
  useEffect(() => {
    if (favoriteOffset && favorites.data?.length === 0 && !favorites.fetching)
      setFavoriteOffset(value => Math.max(0, value - PAGE_SIZE));
  }, [favorites.data, favorites.fetching, favoriteOffset]);
  useEffect(() => {
    if (!busyProgress || state.inconsistent) return;
    // Bound polling: a stale job flag must never produce an endless spinner.
    const deadline = Date.now() + 90_000;
    const timer = window.setInterval(() => {
      if (Date.now() >= deadline) { setStalled(true); window.clearInterval(timer); return; }
      if (document.visibilityState === 'visible') { results.reload(); if (invitationOffset !== offset) invitationResults.reload(); }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [currentId, busyProgress, state.inconsistent, pollRevision, results.reload, invitationResults.reload, invitationOffset, offset]);
  useEffect(() => {
    const available = data?.reminder_available_at;
    if (!available || new Date(available).getTime() <= Date.now()) return;
    const timer = window.setInterval(() => setClock(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, [data?.reminder_available_at]);

  const refresh = () => {
    setStalled(false); setPollRevision(v => v + 1); results.reload(); detail.reload();
    if (invitationOffset !== offset) invitationResults.reload();
  };
  const toggleFavorite = async (supplierId: string, saved: boolean) => {
    if (!org) return;
    try {
      await api(`organizations/${org.id}/favorites/${supplierId}`, { method: saved ? 'DELETE' : 'PUT' });
      setFavoriteOverrides(previous => ({ ...previous, [supplierId]: !saved })); favorites.reload();
      toast.success(saved ? t('Removed from favorites') : t('Saved as favorite'));
    } catch { toast.error(t('Action failed'), tr('Could not update favorites.')); }
  };
  const favoriteIds = new Set((favorites.data || []).map(row => row.supplier_org_id));
  const saved = (supplierId: string) => favoriteOverrides[supplierId] ?? (favoriteIds.has(supplierId) || !!knownFavorites[supplierId]);
  const matches = data?.matches || [];
  const recommended = matches.filter(isRecommended);
  const other = matches.filter(match => !isRecommended(match));
  const categories = useMemo(() => new Map((taxonomy.data || []).map(c => [c.id, arabic && c.name_ar ? c.name_ar : c.name_en])), [taxonomy.data, arabic]);
  const mainCategories = [...new Set((detail.data?.items || []).map(item => categories.get(item.main_category_id || '')).filter(Boolean))].join(' \u00b7 ');
  const quantities = Object.entries((detail.data?.items || []).reduce<Record<string, number>>((totals, item) => {
    totals[item.unit] = (totals[item.unit] || 0) + Number(item.quantity); return totals;
  }, {})).map(([unit, quantity]) => `${new Intl.NumberFormat(arabic ? 'ar-SA' : 'en-GB').format(quantity)} ${unit}`).join(' \u00b7 ');
  const received = data?.quotations_received ?? invitations.filter(row => isSubmitted(row.quotation_status)).length;
  const partialQuoteCount = data?.quotations_received == null && (invitationOffset > 0 || (data?.invited_suppliers || 0) > invitations.length);
  const cooldownActive = !!data?.reminder_available_at && new Date(data.reminder_available_at).getTime() > clock;
  const eligibleReminderIds = new Set(invitations.filter(row => row.reminder_eligible && !cooldownActive && !isSubmitted(row.quotation_status)).map(row => row.id));
  const reminders = selectedReminders.filter(value => eligibleReminderIds.has(value));
  const sendReminders = async () => {
    if (!reminders.length || reminding || cooldownActive) return;
    setReminding(true);
    try {
      await api(`rfqs/${currentId}/reminders`, { method: 'POST', body: json({ invitation_ids: reminders }) });
      toast.success(tr('Reminder request recorded.'));
      setSelectedReminders([]); results.reload(); if (invitationOffset !== offset) invitationResults.reload();
    } catch { toast.error(t('Action failed'), tr('Could not record reminders. Please refresh and try again.')); }
    finally { setReminding(false); }
  };
  const stateLabels: Record<string, string> = {
    loading: tr('Loading'), queued: tr('Queued'), processing: tr('Processing'),
    completed: tr('Completed'), failed: tr('Failed'), review: tr('Status needs review'),
    draft: tr('Draft RFQ'), closed: tr('RFQ closed'),
  };
  const row = (match: Match) => <SupplierRow key={match.id} match={match} city={rfq?.city} favorite={saved(match.supplier_org_id)} toggle={toggleFavorite} tr={tr}/>;
  const totalEvaluated = data?.suppliers_evaluated;
  const nextMatches = totalEvaluated == null ? matches.length === PAGE_SIZE : offset + PAGE_SIZE < totalEvaluated;

  return <div className={styles.workspace} dir={arabic ? 'rtl' : 'ltr'}>
    {!embedded && <header className={styles.header}><div><h1>{tr('Find the right suppliers.')}</h1>
      <p>{tr('Review relevant suppliers, track invitations, and compare the quotations you receive.')}</p></div>
      <Button variant="ghost" size="sm" onClick={refresh} disabled={results.fetching || detail.fetching}><IconRefresh size={15}/>{t('Refresh')}</Button></header>}
    {!embedded && <section className={styles.selector} aria-label={tr('RFQ selection')}>
      <div className={styles.selectorRow}><label htmlFor="matching-rfq">{tr('Request for quotation')}</label>
        <select id="matching-rfq" value={currentId} onChange={event => choose(event.target.value)}>
          <option value="">{tr('Select an RFQ')}</option>
          {rfq && !qRfqs.rows.some(row => row.id === rfq.id) && <option value={rfq.id}>{rfq.reference} · {rfq.project_name || rfq.title}</option>}
          {qRfqs.rows.map(item => <option key={item.id} value={item.id}>{item.reference} · {item.project_name || item.title}</option>)}
        </select>
        <details className={styles.rfqSearch}><summary>{tr('Find another RFQ')}</summary>{qRfqs.controls}</details>
      </div><State loading={qRfqs.loading} error={qRfqs.error} retry={qRfqs.reload}/>
      {rfq && <div className={styles.rfqMeta}><span><IconMapPin size={14}/>{rfq.city || tr('Delivery city not specified')}</span>
        <span>{mainCategories || tr('Category not specified')}</span><span>{quantities || tr('No quantities recorded')}</span>
        <span>{t(rfq.status.replaceAll('_', ' '))}</span><Link href={`/app/buyer/rfqs/${rfq.id}`}>{tr('View RFQ')}</Link></div>}
    </section>}
    <State loading={detail.loading} error={detail.error} retry={detail.reload}/>
    {!currentId && !qRfqs.loading && <p className={styles.empty}>{tr('Select an RFQ to see supplier recommendations.')}</p>}
    {currentId && <>
      <div className={styles.summary} aria-label={tr('RFQ summary')}>
        <div><span>{tr('Matched suppliers')}</span><strong>{data?.matched_suppliers ?? '\u2014'}</strong></div>
        <div><span>{tr('Invitations recorded')}</span><strong>{data?.invited_suppliers ?? '\u2014'}</strong></div>
        <div><span>{tr('Quotations received')}</span><strong>{progressResult.data ? received : '\u2014'}</strong>{partialQuoteCount && <small>{tr('On this invitation page')}</small>}</div>
      </div>
      <section className={styles.recommendations} aria-label={tr('Recommended suppliers')}>
        <div className={styles.sectionHeader}><div><h2>{tr('Recommended suppliers')}</h2><p>{tr('Approved capabilities and delivery coverage. Confirm technical requirements in the quotation.')}</p></div>
          <div className={styles.stateActions}>{embedded && <Button variant="ghost" size="sm" onClick={refresh} disabled={results.fetching}><IconRefresh size={15}/>{t('Refresh')}</Button>}
            <span role="status" className={styles.status} data-state={state.label}>{stateLabels[state.label]}</span></div></div>
        {(state.inconsistent || stalled) && <div role="alert" className={styles.warning}>{state.inconsistent
          ? tr('The matching status and recorded results disagree. Review the available results and refresh; contact support if this continues.')
          : tr('Matching status has not updated. Automatic refresh is paused; these are the latest available results. Refresh or contact support.')}</div>}
        {state.label === 'failed' && <p role="alert" className={styles.warning}>{tr('Matching could not finish. Refresh to check for an update or contact support.')}</p>}
        <div className={styles.tabs} role="group" aria-label={tr('Supplier views')}>
          <button type="button" aria-pressed={tab === 'recommended'} onClick={() => setTab('recommended')}>{tr('Recommendations')}</button>
          <button type="button" aria-pressed={tab === 'favorites'} onClick={() => setTab('favorites')}><IconStar size={14}/>{tr('Favorites')}</button>
        </div>
        {tab === 'recommended' ? <><State loading={results.loading} error={results.error} retry={results.reload}/>
          {data && <><div className={styles.supplierList}>{recommended.map(row)}</div>
            {!recommended.length && <p className={styles.empty}>{state.busy ? tr('Matching is underway. Recommendations will appear here.')
              : state.label === 'draft' ? tr('Publish this RFQ to start supplier matching.')
              : totalEvaluated && totalEvaluated > PAGE_SIZE ? tr('No recommendations on this evaluated page. Review the other pages.')
              : tr('No eligible suppliers currently match this RFQ.')}</p>}
            {!!other.length && <details className={styles.other}><summary>{tr('Other evaluated suppliers')} <span>{other.length} · {tr('on this page')}</span></summary><div className={styles.supplierList}>{other.map(row)}</div></details>}
            <nav className={styles.pagination} aria-label={tr('Evaluated supplier pages')}>
              <span>{tr('Evaluated page')} {Math.floor(offset / PAGE_SIZE) + 1}{totalEvaluated != null ? ` · ${totalEvaluated} ${tr('evaluated in total')}` : ''}</span>
              <div><Button variant="outline" size="sm" disabled={!offset || results.fetching} onClick={() => setPage({ rfq: currentId, offset: Math.max(0, offset - PAGE_SIZE) })}>{t('Previous')}</Button>
                <Button variant="outline" size="sm" disabled={!nextMatches || results.fetching} onClick={() => setPage({ rfq: currentId, offset: offset + PAGE_SIZE })}>{t('Next')}</Button></div>
            </nav></>}
        </> : <><p className={styles.muted}>{tr('Saved suppliers. Eligibility for this RFQ is shown in Recommendations.')}</p>
          <State loading={favorites.loading} error={favorites.error} retry={favorites.reload}/>
          {(favorites.data || []).slice(0, PAGE_SIZE).map(favorite => <div className={styles.savedRow} key={favorite.supplier_org_id}><Link href={`/suppliers/${encodeURIComponent(favorite.supplier_org_id)}`}>{favorite.name}</Link>
            <Button variant="ghost" size="sm" disabled={favoriteBusy === favorite.supplier_org_id} onClick={async () => { setFavoriteBusy(favorite.supplier_org_id); try { await toggleFavorite(favorite.supplier_org_id, true); } finally { setFavoriteBusy(null); } }}><IconStarFilled size={16}/>{tr('Remove')}</Button></div>)}
          {!favorites.loading && !favorites.error && !favorites.data?.length && <p className={styles.empty}>{tr('No saved suppliers on this page.')}</p>}
          <nav className={styles.pagination} aria-label={tr('Favorite supplier pages')}><span>{t('page')} {Math.floor(favoriteOffset / PAGE_SIZE) + 1}</span><div>
            <Button variant="outline" size="sm" disabled={!favoriteOffset || favorites.fetching} onClick={() => setFavoriteOffset(value => Math.max(0, value - PAGE_SIZE))}>{t('Previous')}</Button>
            <Button variant="outline" size="sm" disabled={(favorites.data?.length || 0) <= PAGE_SIZE || favorites.fetching} onClick={() => setFavoriteOffset(value => value + PAGE_SIZE)}>{t('Next')}</Button></div></nav>
        </>}
      </section>
      <section className={styles.progress} aria-label={tr('Invitation and quotation progress')}>
        <div className={styles.sectionHeader}><div><h2>{tr('Invitations & quotations')}</h2>
          <p>{tr('Recorded invitations let suppliers review your RFQ, respond, and submit a quotation. A recorded invitation does not confirm email or SMS delivery.')}</p></div>
          <Link className={styles.profileLink} href={`/app/buyer/compare?rfq=${currentId}`}>{tr('Compare quotations')}<IconArrowUpRight size={15}/></Link></div>
        <State loading={progressResult.loading} error={progressResult.error} retry={progressResult.reload}/>
        <div className={styles.responseCounts}><span>{tr('Invitations created')}: <b>{data?.invited_suppliers ?? '\u2014'}</b></span>
          <span>{tr('Accepted')}: <b>{progressResult.data ? invitations.filter(row => row.status === 'accepted').length : '—'}</b></span>
          <span>{tr('Declined')}: <b>{progressResult.data ? invitations.filter(row => row.status === 'declined').length : '—'}</b></span>
          <span>{tr('Pending response')}: <b>{progressResult.data ? invitations.filter(row => row.status === 'invited').length : '—'}</b></span>
          <small>{tr('Response counts are for this invitation page.')}</small></div>
        {!!invitations.length && <details className={styles.invitationDetails}><summary>{tr('Review responses & send reminders')}</summary>
          {invitations.map(invitation => <div key={invitation.id} className={styles.invitationRow}>
            <label><input type="checkbox" disabled={!eligibleReminderIds.has(invitation.id) || reminding} checked={reminders.includes(invitation.id)}
              onChange={event => setSelectedReminders(previous => event.target.checked ? [...previous, invitation.id] : previous.filter(value => value !== invitation.id))}/>
              <span>{invitation.supplier_name}</span></label>
            <span>{invitation.status === 'accepted' ? tr('Accepted') : invitation.status === 'declined' ? tr('Declined') : invitation.status === 'invited' ? tr('Pending response') : t(invitation.status.replaceAll('_', ' '))}</span>
            <span>{isSubmitted(invitation.quotation_status) ? tr('Quotation received') : tr('No quotation submitted')}</span>
          </div>)}
          <div className={styles.reminderActions}><Button size="sm" disabled={!reminders.length || reminding || cooldownActive} onClick={sendReminders}>{reminding ? t('Saving...') : tr('Remind selected suppliers')}</Button>
            <small>{cooldownActive && data?.reminder_available_at ? `${tr('Next reminder available')}: ${new Intl.DateTimeFormat(arabic ? 'ar-SA-u-ca-gregory' : 'en-GB', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(data.reminder_available_at))}`
              : tr('Reminders have a 24-hour cooldown.')}</small></div>
        </details>}
        <nav className={styles.pagination} aria-label={tr('Invitation pages')}><span>{tr('Invitation page')} {Math.floor(invitationOffset / PAGE_SIZE) + 1}</span><div>
          <Button size="sm" variant="outline" disabled={!invitationOffset || progressResult.fetching} onClick={() => { setInvPage({ rfq: currentId, offset: Math.max(0, invitationOffset - PAGE_SIZE) }); setSelectedReminders([]); }}>{t('Previous')}</Button>
          <Button size="sm" variant="outline" disabled={progressResult.fetching || (data?.invited_suppliers != null ? invitationOffset + PAGE_SIZE >= data.invited_suppliers : invitations.length < PAGE_SIZE)} onClick={() => { setInvPage({ rfq: currentId, offset: invitationOffset + PAGE_SIZE }); setSelectedReminders([]); }}>{t('Next')}</Button></div></nav>
      </section>
    </>}
  </div>;
}
