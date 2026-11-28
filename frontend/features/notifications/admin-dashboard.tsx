"use client";
// Admin notification management: real delivery states, failure reasons, history,
// retry of eligible failures, address revalidation and supplier contact correction.
import { useMemo, useState } from 'react';
import { IconRefresh } from '@tabler/icons-react';
import { api, json } from '@/lib/api/client';
import { useApi } from '@/lib/api/use-api';
import { Button } from '@/components/ui/button';
import { State } from '@/features/shared/ui';
import { useLocale } from '@/lib/locale';
import { useToast } from '@/features/shared/toast';
import styles from './admin-dashboard.module.css';

type Row = {
  id: string; created_at: string; channel: string; template: string; status: string; outbox_status: string;
  provider_status: string | null; rfq_id: string | null; rfq_reference: string | null; supplier_org_id: string | null;
  supplier_name: string | null; destination: string; contact_name: string | null; attempts: number;
  next_attempt_at: string | null; submitted_at: string | null; provider_message_id: string | null;
  failure_category: string | null; failure_reason: string | null; soft_bounce_retries: number;
  provider_response: null | { stage?: string; code?: number; enhanced_code?: string; response?: string; failure_class?: string };
  restriction: null | { id: string; reason: string }; history_count: number; retry_eligible: boolean; correctable: boolean;
};
type Summary = Record<string, number> & { total: number };
type Detail = Row & { destination_full: string; history: { at: string; kind: string; detail: string | null; source?: string; applied?: string }[];
  related: { id: string; template: string; created_at: string; status: string }[] };

const CARDS: [string, string][] = [
  ['total', 'Total notifications'], ['queued', 'Queued'], ['processing', 'Processing'], ['accepted', 'Accepted by provider'],
  ['delivered', 'Confirmed delivered'], ['failed', 'Failed'], ['hard_bounced', 'Hard bounced'], ['soft_bounced', 'Soft bounced'],
  ['suppressed', 'Suppressed'], ['pending_sms', 'Pending SMS'], ['retry_scheduled', 'Retry scheduled'], ['uncertain', 'Outcome uncertain'],
];
const LABELS: Record<string, string> = {
  queued: 'Queued', sending: 'Processing', processing: 'Processing', accepted: 'Accepted by provider', sent: 'Sent (no delivery report)',
  delivered: 'Confirmed delivered', failed: 'Failed', hard_bounced: 'Hard bounced', soft_bounced: 'Soft bounced', suppressed: 'Suppressed',
  skipped: 'Skipped', retry_scheduled: 'Retry scheduled', uncertain: 'Outcome uncertain', simulated: 'Simulated (test mode)',
};
const CATEGORY: Record<string, string> = {
  authentication_failure: 'SMTP authentication failure', invalid_recipient: 'Invalid recipient', permanent_rejection: 'Permanent rejection',
  temporary_rejection: 'Temporary rejection', rate_limit: 'Provider rate limit', spam_rejection: 'Spam / policy rejection',
  mailbox_full: 'Mailbox full', confirmed_delivery: 'Confirmed delivery',
};

const when = (value?: string | null) => value ? new Date(value).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' }) : '—';

export default function AdminNotificationDashboard() {
  const { t } = useLocale();
  const toast = useToast();
  const [filters, setFilters] = useState({ rfq_id: '', org_id: '', channel: '', status: '', date_from: '', date_to: '' });
  const [applied, setApplied] = useState(filters);
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [correction, setCorrection] = useState('');
  const query = useMemo(() => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(applied)) if (value) params.set(key, key.startsWith('date_') ? new Date(value).toISOString() : value);
    return params;
  }, [applied]);
  const summaryParams = new URLSearchParams(query); summaryParams.delete('status');
  const summary = useApi<Summary>(`notifications/admin/deliveries/summary?${summaryParams}`, { staleTimeMs: 3000 });
  const list = useApi<{ total: number; rows: Row[] }>(`notifications/admin/deliveries?${query}&limit=50&offset=${offset}`, { staleTimeMs: 3000 });
  const detail = useApi<Detail>(openId ? `notifications/admin/deliveries/${openId}` : null);
  const reload = () => { summary.reload(); list.reload(); detail.reload(); };

  const run = async (label: string, path: string, body: unknown) => {
    if (busy) return;
    setBusy(true);
    try { await api(path, { method: 'POST', body: json(body) }); toast.success(t(label)); setCorrection(''); reload(); }
    catch (error) { toast.error(t('Action failed'), error instanceof Error ? error.message : t('Please try again.')); }
    finally { setBusy(false); }
  };
  const apply = (next = filters) => { setApplied(next); setOffset(0); };

  return <section className={styles.dashboard} aria-labelledby="notification-dashboard">
    <div className={styles.head}><div><h2 id="notification-dashboard">{t('Notification delivery')}</h2>
      <p>{t('Accepted by provider means the email or SMS provider took the message; only a delivery report confirms delivery.')}</p></div>
      <Button variant="outline" size="sm" onClick={reload}><IconRefresh size={15} aria-hidden />{t('Refresh')}</Button></div>
    <State loading={summary.loading} error={summary.error} retry={summary.reload} />
    {summary.data && <div className={styles.cards}>{CARDS.map(([key, label]) =>
      <button type="button" key={key} className={styles.card} aria-pressed={applied.status === key}
        onClick={() => { const next = { ...filters, status: key === 'total' ? '' : key }; setFilters(next); apply(next); }}>
        <span>{t(label)}</span><strong>{summary.data?.[key] ?? 0}</strong></button>)}</div>}

    <form className={styles.filters} onSubmit={event => { event.preventDefault(); apply(); }}>
      <label className="field">{t('RFQ ID')}<input value={filters.rfq_id} onChange={e => setFilters({ ...filters, rfq_id: e.target.value.trim() })} /></label>
      <label className="field">{t('Supplier organization ID')}<input value={filters.org_id} onChange={e => setFilters({ ...filters, org_id: e.target.value.trim() })} /></label>
      <label className="field">{t('Channel')}<select value={filters.channel} onChange={e => setFilters({ ...filters, channel: e.target.value })}>
        <option value="">{t('All')}</option><option value="email">Email</option><option value="sms">SMS</option><option value="whatsapp">WhatsApp</option></select></label>
      <label className="field">{t('Status')}<select value={filters.status} onChange={e => setFilters({ ...filters, status: e.target.value })}>
        <option value="">{t('All')}</option>{Object.entries(LABELS).filter(([k]) => k !== 'sending').map(([k, v]) => <option key={k} value={k}>{t(v)}</option>)}
        <option value="pending_sms">{t('Pending SMS')}</option></select></label>
      <label className="field">{t('From')}<input type="date" value={filters.date_from} onChange={e => setFilters({ ...filters, date_from: e.target.value })} /></label>
      <label className="field">{t('To')}<input type="date" value={filters.date_to} onChange={e => setFilters({ ...filters, date_to: e.target.value })} /></label>
      <div className={styles.filterActions}><Button type="submit" size="sm">{t('Apply filters')}</Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => { const empty = { rfq_id: '', org_id: '', channel: '', status: '', date_from: '', date_to: '' }; setFilters(empty); apply(empty); }}>{t('Clear')}</Button></div>
    </form>

    <State loading={list.loading} error={list.error} retry={list.reload} empty={!list.loading && !list.error && !list.data?.rows.length} />
    {!!list.data?.rows.length && <div className={styles.tableWrap}><table className={styles.table}>
      <thead><tr><th>{t('Created')}</th><th>{t('RFQ')}</th><th>{t('Supplier')}</th><th>{t('Channel')}</th><th>{t('Recipient')}</th>
        <th>{t('Status')}</th><th>{t('Failure reason')}</th><th>{t('Attempts')}</th><th><span className="sr-only">{t('Actions')}</span></th></tr></thead>
      <tbody>{list.data.rows.map(row => <tr key={row.id} aria-selected={openId === row.id}>
        <td>{when(row.created_at)}</td><td>{row.rfq_reference || '—'}<small>{row.template}</small></td>
        <td>{row.supplier_name || '—'}{row.contact_name && <small>{row.contact_name}</small>}</td><td>{row.channel}</td>
        <td dir="ltr">{row.destination || '—'}</td>
        <td><span className={styles.status} data-status={row.status}>{t(LABELS[row.status] || row.status)}</span>
          {row.restriction && <small>{t('Address')}: {t(row.restriction.reason.replaceAll('_', ' '))}</small>}</td>
        <td>{row.failure_category && <b>{t(CATEGORY[row.failure_category] || row.failure_category)}</b>}<small>{row.failure_reason || ''}</small></td>
        <td>{row.attempts}{row.soft_bounce_retries ? <small>{t('Soft-bounce resends')}: {row.soft_bounce_retries}</small> : null}</td>
        <td className={styles.rowActions}><Button size="sm" variant="ghost" onClick={() => setOpenId(openId === row.id ? null : row.id)}>{openId === row.id ? t('Close') : t('Details')}</Button>
          {row.retry_eligible && <Button size="sm" variant="outline" disabled={busy} onClick={() => {
            const confirmed = row.outbox_status !== 'uncertain' || window.confirm(t('Confirm you checked the provider/mailbox logs and this message was NOT accepted. Retrying an accepted message could send a duplicate.'));
            if (confirmed) void run('Retry scheduled', `notifications/admin/outbox/${row.id}/retry`, { confirmed_not_accepted: row.outbox_status === 'uncertain' });
          }}>{t('Retry')}</Button>}</td>
      </tr>)}</tbody></table></div>}
    {!!list.data && list.data.total > 50 && <div className={styles.pager}>
      <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>{t('Previous')}</Button>
      <span>{offset + 1}–{Math.min(offset + 50, list.data.total)} / {list.data.total}</span>
      <Button size="sm" variant="outline" disabled={offset + 50 >= list.data.total} onClick={() => setOffset(offset + 50)}>{t('Next')}</Button></div>}

    {openId && <aside className={styles.detail} aria-label={t('Delivery history')}>
      <State loading={detail.loading} error={detail.error} retry={detail.reload} />
      {detail.data && <>
        <h3>{t('Delivery history')} · {detail.data.rfq_reference || detail.data.template}</h3>
        <dl className={styles.facts}>
          <div><dt>{t('Recipient')}</dt><dd dir="ltr">{detail.data.destination_full}</dd></div>
          <div><dt>{t('Status')}</dt><dd>{t(LABELS[detail.data.status] || detail.data.status)}</dd></div>
          <div><dt>{t('Provider message ID')}</dt><dd dir="ltr">{detail.data.provider_message_id || '—'}</dd></div>
          <div><dt>{t('Next attempt')}</dt><dd>{when(detail.data.next_attempt_at)}</dd></div>
          {detail.data.provider_response && <div className={styles.wide}><dt>{t('Provider response')}</dt><dd>
            {[detail.data.provider_response.stage, detail.data.provider_response.code, detail.data.provider_response.enhanced_code].filter(Boolean).join(' · ')}
            <br />{detail.data.provider_response.response}</dd></div>}
        </dl>
        <ol className={styles.timeline}>{detail.data.history.map((entry, index) => <li key={index}><time>{when(entry.at)}</time>
          <b>{t(entry.kind.replaceAll('_', ' '))}</b>{entry.source && <small>{entry.source}{entry.applied ? ` · ${entry.applied.replaceAll('_', ' ')}` : ''}</small>}
          {entry.detail && <span>{entry.detail}</span>}</li>)}</ol>
        {detail.data.restriction && <div className={styles.fix}>
          <p>{t('This address is blocked for automatic sending')}: <b>{t(detail.data.restriction.reason.replaceAll('_', ' '))}</b>. {t('The supplier record is kept.')}</p>
          {detail.data.restriction.reason !== 'opted_out' && <Button size="sm" variant="outline" disabled={busy} onClick={() => {
            const note = window.prompt(t('Why is this address valid again? (5-500 characters)')) || '';
            if (note.trim().length >= 5) void run('Address revalidated', `notifications/admin/restrictions/${detail.data?.restriction?.id}/revalidate`, { note });
          }}>{t('Revalidate address')}</Button>}
        </div>}
        {detail.data.correctable && <form className={styles.fix} onSubmit={event => { event.preventDefault(); void run('Contact corrected', `notifications/admin/deliveries/${detail.data?.id}/correct-contact`, { email: correction, resend: true }); }}>
          <label className="field">{t('Correct supplier email and resend')}<input type="email" required value={correction} onChange={e => setCorrection(e.target.value)} dir="ltr" /></label>
          <Button type="submit" size="sm" disabled={busy || !correction}>{t('Save and resend')}</Button>
        </form>}
      </>}
    </aside>}
  </section>;
}
