"use client";

import { useState } from 'react';
import Link from 'next/link';
import { IconArrowUpRight, IconChevronLeft, IconChevronRight, IconDownload, IconRefresh } from '@tabler/icons-react';
import { apiUrl } from '@/lib/api/client';
import type { Schemas } from '@/lib/api/generated/schema';
import { Button } from '@/components/ui/button';
import {
  deliveryLabel, failureReasonLabel, isSubmitted, NOTIFICATION_GROUP_LABELS, NOTIFICATION_GROUPS, PAGE_SIZES, pageRange,
  type NotificationQuery, type PageQuery,
} from './matching-model';
import styles from './matched-suppliers.module.css';

export type Translate = (english: string) => string;
export type QuotationBrief = {
  id: string; reference?: string; supplier_org_id: string; supplier_name: string; status: string;
  submission_source: string; submitted_at: string | null; currency: string; price_basis: string;
  covered_items: number; total_items: number;
  totals: { subtotal: string; vat: string; total: string; currency: string };
};
export type QuotationsSummary = {
  rfq_id: string; automatic_quotations: QuotationBrief[]; manual_quotations: QuotationBrief[];
  preparing_automatic_count: number; preparing_manual_count: number; manual_pricing_required_count?: number; total_submitted: number;
};
export type NotificationRecord = {
  id: string; supplier_org_id: string | null; supplier_name: string; contact_name: string | null; destination: string | null;
  channel: 'email' | 'sms'; template: string | null; status: string; status_group: string; failure_reason: string | null;
  created_at: string | null; sent_at: string | null; invitation_id: string | null; retryable: boolean;
};
export type NotificationRecords = { rfq_id: string; total: number; limit: number; offset: number; counts: Record<string, number>; records: NotificationRecord[] };
type Invitation = Schemas['BuyerInvitation'];

export const formatDate = (value: string | null | undefined, arabic: boolean, withTime = true) => value
  ? new Intl.DateTimeFormat(arabic ? 'ar-SA-u-ca-gregory' : 'en-GB', withTime ? { dateStyle: 'medium', timeStyle: 'short' } : { dateStyle: 'medium' }).format(new Date(value))
  : '—';

/** "Showing 1–10 of 94 suppliers", page size 10/20/50 and previous/next. */
export function Pager({ offset, limit, total, noun, label, busy, onChange, tr, arabic }: {
  offset: number; limit: number; total: number; noun: string; label: string; busy?: boolean;
  onChange: (page: PageQuery) => void; tr: Translate; arabic: boolean;
}) {
  const range = pageRange(offset, limit, total);
  const number = (value: number) => new Intl.NumberFormat(arabic ? 'ar-SA' : 'en-GB').format(value);
  return <nav className={styles.pager} aria-label={label}>
    <span role="status" aria-live="polite">{total
      ? `${tr('Showing')} ${number(range.from)}–${number(range.to)} ${tr('of')} ${number(total)} ${tr(noun)}`
      : `${tr('No')} ${tr(noun)}`}</span>
    <div className={styles.pagerControls}>
      <label>{tr('Rows per page')}
        <select value={limit} onChange={event => onChange({ limit: Number(event.target.value), offset: 0 })}>
          {PAGE_SIZES.map(size => <option key={size} value={size}>{size}</option>)}
        </select></label>
      <span className={styles.pageCount}>{tr('Page')} {number(range.page)} / {number(range.pages)}</span>
      <Button variant="outline" size="sm" aria-label={tr('Previous page')} disabled={busy || offset <= 0}
        onClick={() => onChange({ limit, offset: Math.max(0, offset - limit) })}><IconChevronLeft size={15} className={styles.flip}/></Button>
      <Button variant="outline" size="sm" aria-label={tr('Next page')} disabled={busy || offset + limit >= total}
        onClick={() => onChange({ limit, offset: offset + limit })}><IconChevronRight size={15} className={styles.flip}/></Button>
    </div>
  </nav>;
}

/** Compact fixed-height placeholder rows; the table keeps its size while loading. */
export function SkeletonRows({ rows, columns }: { rows: number; columns: number }) {
  return <>{Array.from({ length: Math.min(rows, 10) }, (_, row) => <tr key={row} className={styles.skeletonRow} aria-hidden="true">
    {Array.from({ length: columns }, (_, column) => <td key={column}><span/></td>)}
  </tr>)}</>;
}

export function ErrorRow({ error, retry, columns, tr }: { error: Error; retry: () => void; columns: number; tr: Translate }) {
  return <tr><td colSpan={columns} className={styles.emptyCell}><div role="alert" className={styles.inlineError}>
    <span>{error.message || tr('Could not load this list.')}</span>
    <Button variant="outline" size="sm" onClick={retry}><IconRefresh size={14}/>{tr('Retry')}</Button></div></td></tr>;
}

export function EmptyRow({ columns, children }: { columns: number; children: React.ReactNode }) {
  return <tr><td colSpan={columns} className={styles.emptyCell}>{children}</td></tr>;
}

const responseLabel = (status: string, tr: Translate) => status === 'accepted' ? tr('Accepted')
  : status === 'declined' ? tr('Declined') : status === 'invited' ? tr('Pending response') : tr(status.replaceAll('_', ' '));

function Delivery({ email, sms, emailReason, smsReason, tr }: { email?: string | null; sms?: string | null; emailReason?: string | null; smsReason?: string | null; tr: Translate }) {
  // A recorded invitation is not a delivery: each channel shows its real outcome.
  return <span className={styles.deliveryStatus}>
    <span data-status={email || 'not_queued'} title={emailReason ? tr(failureReasonLabel(emailReason)) : undefined}>{tr('Email')}: {tr(deliveryLabel(email))}</span>
    <span data-status={sms || 'not_queued'} title={smsReason ? tr(failureReasonLabel(smsReason)) : undefined}>{tr('SMS')}: {tr(deliveryLabel(sms))}</span>
  </span>;
}

export function InvitationsPanel({ data, loading, fetching, error, reload, page, setPage, selected, setSelected, eligible, reminding,
  remindersEnabled, remindersDisabledReason, sendReminders, pendingTotal, compareHref, tr, arabic }: {
  data?: Schemas['MatchResults']; loading: boolean; fetching: boolean; error?: Error; reload: () => void;
  page: PageQuery; setPage: (page: PageQuery) => void; selected: string[]; setSelected: (ids: string[]) => void;
  eligible: Set<string>; reminding: boolean; remindersEnabled: boolean; remindersDisabledReason: string;
  sendReminders: (ids?: string[]) => Promise<void>; pendingTotal: number; compareHref: string; tr: Translate; arabic: boolean;
}) {
  const rows: Invitation[] = data?.invitations || [];
  const total = data?.invitation_total ?? data?.invited_suppliers ?? 0;
  const eligibleOnPage = rows.filter(row => eligible.has(row.id)).map(row => row.id);
  const allSelected = eligibleOnPage.length > 0 && eligibleOnPage.every(id => selected.includes(id));
  const columns = 7;
  return <div className={styles.panelBody}>
    <div className={styles.toolbar}>
      <p className={styles.note}>{tr('A recorded invitation does not confirm email or SMS delivery.')}</p>
      <div className={styles.toolbarActions}>
        {remindersEnabled ? <>
          <Button size="sm" disabled={!selected.length || reminding} onClick={() => void sendReminders(selected)}>
            {reminding ? tr('Sending…') : `${tr('Remind selected')}${selected.length ? ` (${selected.length})` : ''}`}</Button>
          <Button size="sm" variant="outline" disabled={reminding || !pendingTotal} onClick={() => void sendReminders()}>
            {tr('Remind all pending')}{pendingTotal ? ` (${pendingTotal})` : ''}</Button>
        </> : <span className={styles.note} role="status">{remindersDisabledReason}</span>}
        <Link className={styles.textLink} href={compareHref}>{tr('Compare quotations')}<IconArrowUpRight size={14} className={styles.flip}/></Link>
      </div>
    </div>
    <div className={styles.tableWrap} data-busy={fetching && !!data}>
      <table className={styles.table}>
        <caption className={styles.srOnly}>{tr('Invitations')}</caption>
        <thead><tr>
          <th scope="col" className={styles.checkCol}><input type="checkbox" aria-label={tr('Select all eligible suppliers on this page')}
            disabled={!eligibleOnPage.length || reminding || !remindersEnabled} checked={allSelected}
            onChange={event => setSelected(event.target.checked ? [...new Set([...selected, ...eligibleOnPage])] : selected.filter(id => !eligibleOnPage.includes(id)))}/></th>
          <th scope="col">{tr('Supplier')}</th><th scope="col">{tr('Response')}</th>
          <th scope="col" className={styles.hideMd}>{tr('Invited')}</th><th scope="col">{tr('Quotation')}</th>
          <th scope="col" className={styles.hideSm}>{tr('Delivery')}</th><th scope="col" className={styles.actionCol}>{tr('Reminder')}</th>
        </tr></thead>
        <tbody>
          {error && !data ? <ErrorRow error={error} retry={reload} columns={columns} tr={tr}/>
            : loading && !data ? <SkeletonRows rows={page.limit} columns={columns}/>
            : !rows.length ? <EmptyRow columns={columns}>{tr('No invitations recorded for this RFQ yet.')}</EmptyRow>
            : rows.map(row => <tr key={row.id}>
              <td className={styles.checkCol}><input type="checkbox" aria-label={`${tr('Select')} ${row.supplier_name}`}
                disabled={!eligible.has(row.id) || reminding} checked={selected.includes(row.id)}
                onChange={event => setSelected(event.target.checked ? [...selected, row.id] : selected.filter(id => id !== row.id))}/></td>
              <td><span className={styles.primary}>{row.supplier_name}</span></td>
              <td><span className={styles.pill} data-tone={row.status === 'accepted' ? 'positive' : row.status === 'declined' ? 'negative' : 'neutral'}>{responseLabel(row.status, tr)}</span></td>
              <td className={styles.hideMd}>{formatDate(row.invited_at || row.created_at, arabic, false)}</td>
              <td>{isSubmitted(row.quotation_status) ? tr('Quotation received') : row.quotation_status === 'draft' ? tr('Draft in progress') : tr('Not submitted')}</td>
              <td className={styles.hideSm}><Delivery email={row.email_status} sms={row.sms_status} emailReason={row.email_reason} smsReason={row.sms_reason} tr={tr}/></td>
              <td className={styles.actionCol}>{eligible.has(row.id)
                ? <Button size="sm" variant="outline" disabled={reminding} onClick={() => void sendReminders([row.id])}>{tr('Send reminder')}</Button>
                : <small className={styles.muted}>{row.next_reminder_at ? `${tr('Available')} ${formatDate(row.next_reminder_at, arabic)}`
                  : row.reminder_count ? `${row.reminder_count} ${tr('sent')}` : '—'}</small>}</td>
            </tr>)}
        </tbody>
      </table>
    </div>
    <Pager offset={page.offset} limit={page.limit} total={total} noun="invitations" label={tr('Invitation pages')} busy={fetching}
      onChange={next => { setPage(next); setSelected([]); }} tr={tr} arabic={arabic}/>
  </div>;
}

export function QuotationsPanel({ data, loading, fetching, error, reload, page, setPage, rfqId, tr, t, arabic }: {
  data?: QuotationsSummary; loading: boolean; fetching: boolean; error?: Error; reload: () => void;
  page: PageQuery & { kind: 'automatic' | 'manual' }; setPage: (page: PageQuery & { kind: 'automatic' | 'manual' }) => void;
  rfqId: string; tr: Translate; t: Translate; arabic: boolean;
}) {
  const rows = (page.kind === 'automatic' ? data?.automatic_quotations : data?.manual_quotations) || [];
  const visible = rows.slice(page.offset, page.offset + page.limit);
  const money = (value: string, currency: string) => {
    const amount = Number(value);
    return Number.isFinite(amount) ? `${new Intl.NumberFormat(arabic ? 'ar-SA' : 'en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount)} ${currency}` : `${value} ${currency}`;
  };
  const preparing = page.kind === 'automatic' ? data?.preparing_automatic_count || 0 : data?.preparing_manual_count || 0;
  const columns = 7;
  const kinds = [
    { key: 'automatic' as const, label: tr('Automatic quotations'), count: data?.automatic_quotations.length },
    { key: 'manual' as const, label: tr('Manual quotations'), count: data?.manual_quotations.length },
  ];
  return <div className={styles.panelBody}>
    <div className={styles.toolbar}>
      <div className={styles.segmented} role="tablist" aria-label={tr('Quotation source')} onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = page.kind === 'automatic' ? 'manual' : 'automatic';
        const target = event.key === 'Home' ? 'automatic' : event.key === 'End' ? 'manual' : next;
        setPage({ ...page, kind: target, offset: 0 });
        document.getElementById(`quotation-kind-${target}`)?.focus();
      }}>
        {kinds.map(kind => <button key={kind.key} type="button" role="tab" id={`quotation-kind-${kind.key}`} aria-selected={page.kind === kind.key}
          aria-controls="quotation-kind-panel" tabIndex={page.kind === kind.key ? 0 : -1}
          onClick={() => setPage({ ...page, kind: kind.key, offset: 0 })}>{kind.label}{kind.count != null && <span className={styles.count}>{kind.count}</span>}</button>)}
      </div>
      <div className={styles.toolbarActions}>
        {!!data && <span className={styles.note}>{page.kind === 'automatic'
          ? `${tr('Drafts pending supplier review')}: ${data.preparing_automatic_count} · ${tr('Manual pricing required')}: ${data.manual_pricing_required_count ?? 0}`
          : `${tr('Manual drafts in progress')}: ${data.preparing_manual_count}`}</span>}
        {data && data.total_submitted > 0
          ? <Link className={styles.buttonLink} href={`/app/buyer/compare?rfq=${rfqId}`}>{tr('Compare quotations')}<IconArrowUpRight size={14} className={styles.flip}/></Link>
          : <span className={styles.buttonLink} aria-disabled="true">{tr('Compare quotations')}</span>}
      </div>
    </div>
    <div className={styles.tableWrap} data-busy={fetching && !!data} role="tabpanel" id="quotation-kind-panel" aria-labelledby={`quotation-kind-${page.kind}`}>
      <table className={styles.table}>
        <caption className={styles.srOnly}>{page.kind === 'automatic' ? tr('Automatic quotations') : tr('Manual quotations')}</caption>
        <thead><tr>
          <th scope="col">{tr('Reference')}</th><th scope="col">{tr('Supplier')}</th>
          <th scope="col" className={styles.hideSm}>{tr('Quoted items')}</th><th scope="col" className={styles.num}>{tr('Total amount')}</th>
          <th scope="col" className={styles.hideSm}>{tr('Status')}</th><th scope="col" className={styles.hideMd}>{tr('Submitted')}</th>
          <th scope="col" className={styles.actionCol}><span className={styles.srOnly}>{tr('Actions')}</span></th>
        </tr></thead>
        <tbody>
          {error && !data ? <ErrorRow error={error} retry={reload} columns={columns} tr={tr}/>
            : loading && !data ? <SkeletonRows rows={5} columns={columns}/>
            : !visible.length ? <EmptyRow columns={columns}><strong>{page.kind === 'automatic' ? tr('No automatic quotations submitted yet.') : tr('No manual quotations submitted yet.')}</strong>
              <span>{preparing > 0
                ? page.kind === 'automatic'
                  ? tr('Quotations being prepared from saved supplier rates. Drafts remain private until authorized and submitted.')
                  : tr('Invited suppliers are preparing quotations. Drafts stay private until submitted.')
                : tr('Submitted quotations will appear here.')}</span></EmptyRow>
            : visible.map(quote => <tr key={quote.id}>
              <td><span className={styles.mono}>{quote.reference || quote.id.slice(0, 8).toUpperCase()}</span></td>
              <td><span className={styles.primary}>{quote.supplier_name}</span></td>
              <td className={styles.hideSm}>{quote.covered_items}/{quote.total_items} {t('items')}</td>
              <td className={styles.num}><strong>{money(quote.totals.total, quote.totals.currency || quote.currency)}</strong></td>
              <td className={styles.hideSm}><span className={styles.pill} data-tone={quote.status === 'awarded' ? 'positive' : quote.status === 'rejected' ? 'negative' : 'neutral'}>{t(quote.status)}</span></td>
              <td className={styles.hideMd}>{formatDate(quote.submitted_at, arabic)}</td>
              <td className={styles.actionCol}><div className={styles.rowActions}>
                <Link className={styles.textLink} href={`/app/buyer/quote/${quote.id}`}>{tr('View')}</Link>
                <a className={styles.iconButton} href={apiUrl(`quotations/${quote.id}/export.pdf?lang=${arabic ? 'ar' : 'en'}`)} target="_blank" rel="noreferrer"
                  aria-label={`${tr('Download PDF')}: ${quote.supplier_name}`} title={tr('Download PDF')}><IconDownload size={15}/></a>
              </div></td>
            </tr>)}
        </tbody>
      </table>
    </div>
    {rows.length > PAGE_SIZES[0] && <Pager offset={page.offset} limit={page.limit} total={rows.length} noun="quotations" label={tr('Quotation pages')}
      onChange={next => setPage({ ...page, ...next })} tr={tr} arabic={arabic}/>}
  </div>;
}

const TEMPLATE_LABELS: Record<string, string> = { rfq_invitation: 'Invitation', deadline_reminder: 'Reminder', rfq_revision: 'RFQ revision' };

export function NotificationsPanel({ data, loading, fetching, error, reload, query, setQuery, retry, admin, remindersEnabled, tr, arabic }: {
  data?: NotificationRecords; loading: boolean; fetching: boolean; error?: Error; reload: () => void;
  query: NotificationQuery; setQuery: (query: NotificationQuery) => void;
  retry: (record: NotificationRecord) => Promise<void>; admin: boolean; remindersEnabled: boolean; tr: Translate; arabic: boolean;
}) {
  const [busy, setBusy] = useState('');
  const counts = data?.counts || {};
  const all = NOTIFICATION_GROUPS.reduce((sum, key) => sum + (counts[key] || 0), 0);
  const columns = 7;
  const groups = [{ key: '', label: tr('All'), count: data ? all : undefined },
    ...NOTIFICATION_GROUPS.map(key => ({ key, label: tr(NOTIFICATION_GROUP_LABELS[key]), count: data ? counts[key] || 0 : undefined }))];
  return <div className={styles.panelBody}>
    <div className={styles.toolbar}>
      <div className={styles.chips} role="group" aria-label={tr('Delivery status')}>
        {groups.map(group => <button key={group.key || 'all'} type="button" aria-pressed={query.group === group.key} data-group={group.key || 'all'}
          onClick={() => setQuery({ ...query, group: group.key, offset: 0 })}>{group.label}{group.count != null && <span className={styles.count}>{group.count}</span>}</button>)}
      </div>
      <div className={styles.toolbarActions}>
        <label className={styles.inlineField}><span className={styles.srOnly}>{tr('Channel')}</span>
          <select value={query.channel} onChange={event => setQuery({ ...query, channel: event.target.value, offset: 0 })}>
            <option value="">{tr('All channels')}</option><option value="email">{tr('Email')}</option><option value="sms">{tr('SMS')}</option>
          </select></label>
        <Button variant="ghost" size="sm" onClick={reload} disabled={fetching}><IconRefresh size={14}/>{tr('Refresh')}</Button>
      </div>
    </div>
    <p className={styles.note}>{tr('Statuses come from the email and SMS providers. Provider accepted is not the same as delivered, and an invitation record is not a delivery confirmation.')}</p>
    <div className={styles.tableWrap} data-busy={fetching && !!data}>
      <table className={styles.table}>
        <caption className={styles.srOnly}>{tr('Notification records')}</caption>
        <thead><tr>
          <th scope="col">{tr('Supplier')}</th><th scope="col" className={styles.hideMd}>{tr('Contact')}</th>
          <th scope="col">{tr('Channel')}</th><th scope="col">{tr('Delivery status')}</th>
          <th scope="col" className={styles.hideSm}>{tr('Sent')}</th><th scope="col" className={styles.hideMd}>{tr('Failure reason')}</th>
          <th scope="col" className={styles.actionCol}><span className={styles.srOnly}>{tr('Actions')}</span></th>
        </tr></thead>
        <tbody>
          {error && !data ? <ErrorRow error={error} retry={reload} columns={columns} tr={tr}/>
            : loading && !data ? <SkeletonRows rows={query.limit} columns={columns}/>
            : !data?.records.length ? <EmptyRow columns={columns}>{query.group || query.channel
              ? tr('No notifications match this status or channel.') : tr('No email or SMS notifications recorded yet.')}</EmptyRow>
            : data.records.map(record => <tr key={record.id}>
              <td><span className={styles.primary}>{record.supplier_name || '—'}</span></td>
              <td className={styles.hideMd}><span className={styles.stack}><span>{record.contact_name || tr('Assigned contact')}</span>
                {record.destination && <small className={styles.mono} dir="ltr">{record.destination}</small>}</span></td>
              <td><span className={styles.stack}><span>{record.channel === 'sms' ? tr('SMS') : tr('Email')}</span>
                {record.template && <small className={styles.muted}>{tr(TEMPLATE_LABELS[record.template] || record.template.replaceAll('_', ' '))}</small>}</span></td>
              <td><span className={styles.pill} data-group={record.status_group} title={tr(deliveryLabel(record.status))}>{tr(NOTIFICATION_GROUP_LABELS[record.status_group] || deliveryLabel(record.status))}</span></td>
              <td className={styles.hideSm}>{formatDate(record.sent_at || record.created_at, arabic)}</td>
              <td className={styles.hideMd}>{record.failure_reason ? tr(failureReasonLabel(record.failure_reason)) : '—'}</td>
              <td className={styles.actionCol}>{record.retryable && (admin || (remindersEnabled && record.invitation_id))
                ? <Button size="sm" variant="outline" disabled={!!busy} title={admin ? undefined : tr('Queues a reminder to this supplier through its current contacts.')}
                  onClick={async () => { setBusy(record.id); try { await retry(record); } finally { setBusy(''); } }}>
                  {busy === record.id ? tr('Sending…') : admin ? tr('Retry') : tr('Resend')}</Button> : null}</td>
            </tr>)}
        </tbody>
      </table>
    </div>
    <Pager offset={query.offset} limit={query.limit} total={data?.total || 0} noun="notifications" label={tr('Notification pages')} busy={fetching}
      onChange={next => setQuery({ ...query, ...next })} tr={tr} arabic={arabic}/>
  </div>;
}
