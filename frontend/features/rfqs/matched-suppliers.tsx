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
import { AdminMatchingDiagnostics } from './admin-matching-diagnostics';

type Favorite = { supplier_org_id: string; name: string; profile_url?: string | null };
type Translate = (english: string) => string;
type QuotationBrief = {
  id: string;
  supplier_org_id: string;
  supplier_name: string;
  status: string;
  submission_source: string;
  submitted_at: string | null;
  currency: string;
  price_basis: string;
  covered_items: number;
  total_items: number;
  totals: { subtotal: string; vat: string; total: string; currency: string };
};
type QuotationsSummary = {
  rfq_id: string;
  automatic_quotations: QuotationBrief[];
  manual_quotations: QuotationBrief[];
  preparing_automatic_count: number;
  preparing_manual_count: number;
  total_submitted: number;
};
type NotificationsStatus = {
  rfq_id: string;
  total: number;
  by_status: Record<string, number>;
  by_channel: { email: Record<string, number>; sms: Record<string, number> };
};

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
  const level = evidence === 'material' ? tr('Exact material match')
    : evidence === 'description' ? tr('Product match')
    : evidence === 'subcategory' ? tr('Subcategory capability')
    : evidence === 'category' ? tr('Broad category capability') : null;
  const itemLevel: Record<string, string> = {
    'exact material': tr('Exact material'), 'product alias': tr('Same product'), 'product family': tr('Same product family'),
    subcategory: tr('Subcategory capability'), 'main category': tr('Broad category'), 'AI-assisted semantic match': tr('AI-assisted'),
  };
  const items = match.matched_items || [];
  const relevant = (match.matched_categories || []).filter(Boolean).join(' \u00b7 ');
  const coverage = !city ? tr('Delivery location not specified')
    : match.service_coverage_status === 'covered' ? `${tr('Delivery covered')} \u00b7 ${city}`
    : match.service_coverage_status === 'not covered' ? tr('Delivery location not covered')
    : tr('Delivery coverage unconfirmed');
  return <article className={styles.supplier} data-recommended={recommended}>
    <div className={styles.identity}>
      <span className={styles.avatar} aria-hidden="true">{(match.supplier_name || '').slice(0, 2)}</span>
      <div><h3>{match.profile_url?<Link href={match.profile_url}>{match.supplier_name || tr('Supplier profile')}</Link>:match.supplier_name || tr('Supplier profile')}</h3>
        <span className={styles.verification}>{match.verification_status === 'verified'
          ? <><IconCircleCheck size={14}/>{tr('Verified company')}</>
          : tr('Verification pending')}</span>
      </div>
    </div>
    <div className={styles.evidence}>
      {recommended ? <><span className={styles.matchBadge} data-strength={match.match_strength || 'capability'}>{level || tr('Approved capability match')}</span>
        {items.length ? <ul className={styles.matchedItems} aria-label={tr('Matched materials')}>
          {items.map(item => <li key={item.item_id}>{item.description}
            <small>{' \u00b7 '}{[item.match_level && itemLevel[item.match_level], item.discipline, item.main_category !== item.discipline ? item.main_category : null, item.subcategory].filter(Boolean).join(' \u203a ')}</small></li>)}
        </ul> : <span>{relevant || tr('Review capabilities in the supplier profile')}</span>}
        <span>{`${match.matched_item_count || items.length || match.covered_items?.length || 0} ${tr('matched RFQ items')} \u00b7 ${match.saved_price_available ? tr('Saved price available') : tr('Manual pricing required')}`}</span>
        {(evidence === 'category' || evidence === 'subcategory') && <small>{tr('Category capability: this RFQ item does not name a specific product.')}</small>}
        {evidence !== 'material' && <small>{tr('Technical compliance is not confirmed by this match; review specifications in the quotation.')}</small>}
      </> : <><strong className={styles.reason}>{reasons[eligibilityReason(match)]}</strong>
        {match.invitation_review_required && <small>{tr('Earlier invitation retained; current eligibility needs review.')}</small>}</>}
      <span className={styles.coverage}><IconMapPin size={13}/>{coverage}</span>
    </div>
    <div className={styles.supplierActions}>
      <span className={styles.invitationStatus}>{match.invitation_status === 'accepted' ? tr('Invitation accepted')
        : match.invitation_status === 'declined' ? tr('Invitation declined')
        : match.invitation_status ? tr('Invitation recorded') : tr('No invitation recorded')}</span>
      <span className={styles.invitationStatus}>{match.quotation_status ? `${tr('Quotation')}: ${tr(match.quotation_status)}` : tr('No quotation submitted')}</span>
      <div>{match.profile_url&&<Link className={styles.profileLink} href={match.profile_url}>{tr('View profile')}<IconArrowUpRight size={15}/></Link>}
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
  const detail = useApi<Schemas['RFQDetail']>(currentId ? `rfqs/${currentId}` : null, { staleTimeMs: 60000 });
  const taxonomy = useApi<Schemas['CategoryOut'][]>(currentId ? 'taxonomy/categories' : null, { staleTimeMs: 600000 });
  const resultPath = (start: number) => `matching/rfqs/${currentId}/results?limit=${PAGE_SIZE}&offset=${start}`;
  const results = useApi<Schemas['MatchResults']>(currentId ? resultPath(offset) : null, { staleTimeMs: 5000 });
  // Separate invitation paging only needs another bounded request when its page differs.
  const invitationResults = useApi<Schemas['MatchResults']>(currentId && invitationOffset !== offset ? resultPath(invitationOffset) : null, { staleTimeMs: 5000 });
  const quotationsSummary = useApi<QuotationsSummary>(currentId ? `matching/rfqs/${currentId}/quotations-summary` : null, { staleTimeMs: 10000 });
  const notificationsStatus = useApi<NotificationsStatus>(currentId ? `matching/rfqs/${currentId}/notifications-status` : null, { staleTimeMs: 10000 });
  const progressResult = invitationOffset === offset ? results : invitationResults;
  const favorites = useApi<Favorite[]>(org ? `organizations/${org.id}/favorites?limit=26&offset=${favoriteOffset}` : null, { staleTimeMs: 60000 });
  const rfq = detail.data?.rfq;
  const data = results.data;
  const invitations = progressResult.data?.invitations || [];
  const state = matchingState(data, stalled);
  const busyProgress = data?.progress === 'published' || data?.progress === 'matching_queued' || data?.progress === 'matching_in_progress';
  const [pollState] = useState(() => ({ key: '', started: 0, count: 0 }));

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
    const key = `${currentId}:${pollRevision}`;
    if (pollState.key !== key) { pollState.key = key; pollState.started = Date.now(); pollState.count = 0; }
    let timer: number;
    const poll = () => {
      if (Date.now() - pollState.started >= 90_000) { setStalled(true); return; }
      if (document.visibilityState === 'visible') {
        results.reload();
        if (invitationOffset !== offset) invitationResults.reload();
        pollState.count += 1;
      }
      timer = window.setTimeout(poll, Math.min(30_000, 5000 * 2 ** Math.min(pollState.count, 3)));
    };
    timer = window.setTimeout(poll, 5000);
    return () => window.clearTimeout(timer);
  }, [currentId, busyProgress, state.inconsistent, pollRevision, results.reload, invitationResults.reload, invitationOffset, offset]);
  const waitingReminder = invitations.some(row => row.next_reminder_at && new Date(row.next_reminder_at).getTime() > Date.now());
  useEffect(() => {
    if (!waitingReminder) return;
    const timer = window.setInterval(() => setClock(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, [waitingReminder]);

  const refresh = () => {
    setStalled(false); setPollRevision(v => v + 1); results.reload(); detail.reload();
    quotationsSummary.reload(); notificationsStatus.reload();
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
  const categories = useMemo(() => new Map((taxonomy.data || []).map(c => [c.id, arabic && c.name_ar ? c.name_ar : c.name_en])), [taxonomy.data, arabic]);
  const mainCategories = [...new Set((detail.data?.items || []).map(item => categories.get(item.main_category_id || '')).filter(Boolean))].join(' \u00b7 ');
  const quantities = Object.entries((detail.data?.items || []).reduce<Record<string, number>>((totals, item) => {
    totals[item.unit] = (totals[item.unit] || 0) + Number(item.quantity); return totals;
  }, {})).map(([unit, quantity]) => `${new Intl.NumberFormat(arabic ? 'ar-SA' : 'en-GB').format(quantity)} ${unit}`).join(' \u00b7 ');
  const received = data?.quotations_received ?? invitations.filter(row => isSubmitted(row.quotation_status)).length;
  const partialQuoteCount = data?.quotations_received == null && (invitationOffset > 0 || (data?.invited_suppliers || 0) > invitations.length);
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
      setSelectedReminders([]); results.reload(); if (invitationOffset !== offset) invitationResults.reload();
      notificationsStatus.reload();
    } catch (error) { toast.error(t('Action failed'), error instanceof Error ? error.message : tr('Could not record reminders. Please refresh and try again.')); }
    finally { setReminding(false); }
  };
  const dateTime = (value: string) => new Intl.DateTimeFormat(arabic ? 'ar-SA-u-ca-gregory' : 'en-GB', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
  const stateLabels: Record<string, string> = {
    loading: tr('Matching in progress'), queued: tr('Matching in progress'), processing: tr('Matching in progress'),
    completed: tr('Matching completed'), no_eligible: tr('No eligible suppliers found'),
    failed: tr('Matching failed'), review: tr('Status needs review'),
    draft: tr('Draft RFQ'), closed: tr('RFQ closed'),
  };
  const row = (match: Match) => <SupplierRow key={match.id} match={match} city={rfq?.city} favorite={saved(match.supplier_org_id)} toggle={toggleFavorite} tr={tr}/>;
  // Results list final eligible suppliers only, 25 per server page.
  const totalMatched = data?.matched_suppliers;
  const nextMatches = totalMatched == null ? matches.length === PAGE_SIZE : offset + PAGE_SIZE < totalMatched;

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
              : data.explanation || tr('No eligible suppliers currently match this RFQ.')}</p>}
            <nav className={styles.pagination} aria-label={tr('Matched supplier pages')}>
              <span>{tr('Page')} {Math.floor(offset / PAGE_SIZE) + 1}{totalMatched != null ? ` · ${totalMatched} ${tr('matched suppliers')}` : ''}</span>
              <div><Button variant="outline" size="sm" disabled={!offset || results.fetching} onClick={() => setPage({ rfq: currentId, offset: Math.max(0, offset - PAGE_SIZE) })}>{t('Previous')}</Button>
                <Button variant="outline" size="sm" disabled={!nextMatches || results.fetching} onClick={() => setPage({ rfq: currentId, offset: offset + PAGE_SIZE })}>{t('Next')}</Button></div>
            </nav></>}
        </> : <><p className={styles.muted}>{tr('Saved suppliers. Eligibility for this RFQ is shown in Recommendations.')}</p>
          <State loading={favorites.loading} error={favorites.error} retry={favorites.reload}/>
          {(favorites.data || []).slice(0, PAGE_SIZE).map(favorite => <div className={styles.savedRow} key={favorite.supplier_org_id}>{favorite.profile_url ? <Link href={favorite.profile_url}>{favorite.name}</Link> : <span title={tr('This supplier is not currently listed in the public directory.')}>{favorite.name}</span>}
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
        {!!invitations.length && <details className={styles.invitationDetails}><summary>{remindersEnabled ? tr('Review responses & send reminders') : tr('Review responses')}</summary>
          {!remindersEnabled && <p className={styles.muted} role="status">{remindersDisabledReason}</p>}
          {invitations.map(invitation => <div key={invitation.id} className={styles.invitationRow}>
            <label><input type="checkbox" disabled={!eligibleReminderIds.has(invitation.id) || reminding} checked={reminders.includes(invitation.id)}
              onChange={event => setSelectedReminders(previous => event.target.checked ? [...previous, invitation.id] : previous.filter(value => value !== invitation.id))}/>
              <span>{invitation.supplier_name}</span></label>
            <span>{invitation.status === 'accepted' ? tr('Accepted') : invitation.status === 'declined' ? tr('Declined') : invitation.status === 'invited' ? tr('Pending response') : t(invitation.status.replaceAll('_', ' '))}</span>
            <span>{isSubmitted(invitation.quotation_status) ? tr('Quotation received') : tr('No quotation submitted')}</span>
            {eligibleReminderIds.has(invitation.id)
              ? <Button size="sm" variant="outline" disabled={reminding} onClick={() => void sendReminders([invitation.id])}>{tr('Send reminder')}</Button>
              : <small>{invitation.next_reminder_at ? `${tr('Next reminder available')}: ${dateTime(invitation.next_reminder_at)}` : ''}</small>}
          </div>)}
          {remindersEnabled && <div className={styles.reminderActions}><Button size="sm" disabled={!reminders.length || reminding} onClick={() => void sendReminders(reminders)}>{reminding ? t('Saving...') : tr('Remind selected suppliers')}</Button>
            <Button size="sm" variant="outline" disabled={reminding || !data?.pending_suppliers} onClick={() => void sendReminders()}>{tr('Remind all pending suppliers')}</Button>
            <small>{tr('Each supplier can be reminded once per cooldown period. Suppliers without a valid contact are reported as skipped.')}</small></div>}
        </details>}
        <nav className={styles.pagination} aria-label={tr('Invitation pages')}><span>{tr('Invitation page')} {Math.floor(invitationOffset / PAGE_SIZE) + 1}</span><div>
          <Button size="sm" variant="outline" disabled={!invitationOffset || progressResult.fetching} onClick={() => { setInvPage({ rfq: currentId, offset: Math.max(0, invitationOffset - PAGE_SIZE) }); setSelectedReminders([]); }}>{t('Previous')}</Button>
          <Button size="sm" variant="outline" disabled={progressResult.fetching || (data?.invited_suppliers != null ? invitationOffset + PAGE_SIZE >= data.invited_suppliers : invitations.length < PAGE_SIZE)} onClick={() => { setInvPage({ rfq: currentId, offset: invitationOffset + PAGE_SIZE }); setSelectedReminders([]); }}>{t('Next')}</Button></div></nav>
      </section>
      <section className={styles.progress} aria-label={tr('Automatic quotations')}>
        <div className={styles.sectionHeader}><div><h2>{tr('Automatic quotations')}</h2>
          <p>{tr('Submitted quotations generated automatically from authorized supplier Private Price Books.')}</p></div>
          <span role="status" className={styles.status} data-state={quotationsSummary.data?.automatic_quotations.length ? 'completed' : quotationsSummary.data?.preparing_automatic_count ? 'processing' : 'idle'}>
            {quotationsSummary.data?.automatic_quotations.length ? tr('Quotations received')
              : quotationsSummary.data?.preparing_automatic_count ? tr('Quotations being prepared')
              : tr('No automatic quotations yet')}
          </span></div>
        <State loading={quotationsSummary.loading} error={quotationsSummary.error} retry={quotationsSummary.reload}/>
        {quotationsSummary.data && <>
          <div className={styles.responseCounts}>
            <span>{tr('Submitted automatic quotations')}: <b>{quotationsSummary.data.automatic_quotations.length}</b></span>
            <span>{tr('Automatic drafts pending supplier review')}: <b>{quotationsSummary.data.preparing_automatic_count}</b></span>
          </div>
          {quotationsSummary.data.automatic_quotations.length > 0 ? quotationsSummary.data.automatic_quotations.map(q => (
            <div key={q.id} className={styles.invitationRow}>
              <span><strong>{q.supplier_name}</strong> · {q.covered_items}/{q.total_items} {t('items')}</span>
              <span><strong>{q.totals.total} {q.currency}</strong></span>
              <Link className={styles.profileLink} href={`/app/buyer/quote/${q.id}`}>{t('Open quotation')}<IconArrowUpRight size={15}/></Link>
            </div>
          )) : <p className={styles.empty}>{quotationsSummary.data.preparing_automatic_count > 0
            ? tr('Quotations being prepared from saved supplier rates. Drafts remain private until authorized and submitted.')
            : tr('No automatic quotations submitted yet.')}</p>}
        </>}
      </section>
      <section className={styles.progress} aria-label={tr('Manual quotations')}>
        <div className={styles.sectionHeader}><div><h2>{tr('Manual quotations')}</h2>
          <p>{tr('Quotations prepared and submitted manually by invited suppliers.')}</p></div>
          <span role="status" className={styles.status} data-state={quotationsSummary.data?.manual_quotations.length ? 'completed' : quotationsSummary.data?.preparing_manual_count ? 'processing' : 'idle'}>
            {quotationsSummary.data?.manual_quotations.length ? tr('Quotations received')
              : quotationsSummary.data?.preparing_manual_count ? tr('Quotations being prepared')
              : tr('No manual quotations yet')}
          </span></div>
        <State loading={quotationsSummary.loading} error={quotationsSummary.error} retry={quotationsSummary.reload}/>
        {quotationsSummary.data && <>
          <div className={styles.responseCounts}>
            <span>{tr('Submitted manual quotations')}: <b>{quotationsSummary.data.manual_quotations.length}</b></span>
            <span>{tr('Manual drafts in progress')}: <b>{quotationsSummary.data.preparing_manual_count}</b></span>
          </div>
          {quotationsSummary.data.manual_quotations.length > 0 ? quotationsSummary.data.manual_quotations.map(q => (
            <div key={q.id} className={styles.invitationRow}>
              <span><strong>{q.supplier_name}</strong> · {q.covered_items}/{q.total_items} {t('items')}</span>
              <span><strong>{q.totals.total} {q.currency}</strong></span>
              <Link className={styles.profileLink} href={`/app/buyer/quote/${q.id}`}>{t('Open quotation')}<IconArrowUpRight size={15}/></Link>
            </div>
          )) : <p className={styles.empty}>{tr('No manual quotations submitted yet.')}</p>}
        </>}
      </section>
      <section className={styles.progress} aria-label={tr('Notification status')}>
        <div className={styles.sectionHeader}><div><h2>{tr('Notification status')}</h2>
          <p>{tr('Background email and SMS delivery status tracked independently from invitations.')}</p></div>
          <Button variant="ghost" size="sm" onClick={notificationsStatus.reload} disabled={notificationsStatus.fetching}><IconRefresh size={15}/>{t('Refresh')}</Button></div>
        <State loading={notificationsStatus.loading} error={notificationsStatus.error} retry={notificationsStatus.reload}/>
        {notificationsStatus.data && <>
          <div className={styles.responseCounts}>
            <span>{tr('Total queued/processed')}: <b>{notificationsStatus.data.total}</b></span>
            {Object.entries(notificationsStatus.data.by_status).map(([label, count]) => (
              <span key={label}>{t(label.replaceAll('_', ' '))}: <b>{count}</b></span>
            ))}
          </div>
          {notificationsStatus.data.total === 0 && <p className={styles.empty}>{tr('No email or SMS notifications recorded yet.')}</p>}
        </>}
      </section>
      {user.is_admin && !embedded && <AdminMatchingDiagnostics rfqId={currentId} onRerunDone={results.reload} />}
    </>}
  </div>;
}
