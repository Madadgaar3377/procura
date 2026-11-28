"use client";

import { Fragment, useMemo, useState } from 'react';
import Link from 'next/link';
import * as Dialog from '@radix-ui/react-dialog';
import {
  IconAlertTriangle, IconArrowLeft, IconArrowUpRight, IconBookmark, IconChevronDown, IconChevronLeft, IconChevronRight,
  IconDownload, IconEye, IconFileText, IconPrinter, IconTrophy, IconX,
} from '@tabler/icons-react';
import { api, apiUrl, json } from '@/lib/api/client';
import { useApi } from '@/lib/api/use-api';
import type { Schemas } from '@/lib/api/generated/schema';
import { State } from '@/features/shared/ui';
import { Button } from '@/components/ui/button';
import { useLocale } from '@/lib/locale';
import { useToast } from '@/features/shared/toast';
import {
  allowedDecisions, complianceCounts, COMPLIANCE, formatAmount, formatQuantity, isPositive, isTab, leadTime, pageOf, PAGE_SIZES,
  percent, reviewLines, sourceKind, TABS, unquotedItems, validity, vatRates,
  type Compliance, type Decision, type HistoryEvent, type QuoteDetail, type QuoteDocument, type ReviewLine, type Tab,
} from './review-model';
import { reviewArabic } from './review-copy';
import styles from './buyer-review.module.css';

type Translate = (english: string) => string;
type Revision = { id: string; number: number; rfq_revision: number; status: string; created_at: string };

const TAB_LABELS: Record<Tab, string> = {
  materials: 'Quoted Materials', technical: 'Technical Comparison', commercial: 'Commercial Terms', attachments: 'Attachments', activity: 'Activity',
};
const STATUS_LABELS: Record<string, string> = { submitted: 'Submitted', shortlisted: 'Shortlisted', awarded: 'Awarded', rejected: 'Rejected', draft: 'Draft' };
const COMPLIANCE_LABELS: Record<Compliance, string> = {
  compliant: 'Compliant', partial: 'Partially compliant', noncompliant: 'Non-compliant', unconfirmed: 'Unconfirmed',
};
const PREVIEWABLE = /^(application\/pdf|image\/(png|jpeg|webp))$/;

function formatDate(value: string | null | undefined, arabic: boolean, withTime = false) {
  if (!value) return null;
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(arabic ? 'ar-SA-u-ca-gregory' : 'en-GB', withTime ? { dateStyle: 'medium', timeStyle: 'short' } : { dateStyle: 'medium' }).format(date);
}
const fileSize = (bytes: number) => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

/** Fetches a protected file with the session cookie, then opens or saves it. */
async function openFile(path: string, filename: string, mode: 'preview' | 'download') {
  const response = await fetch(apiUrl(path), { credentials: 'include' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const url = URL.createObjectURL(await response.blob());
  if (mode === 'preview') window.open(url, '_blank', 'noopener');
  else { const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click(); }
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function Money({ value, currency, strong }: { value?: string | null; currency: string; strong?: boolean }) {
  const amount = formatAmount(value);
  if (amount == null) return <span className={styles.muted}>—</span>;
  const content = <><bdi className={styles.amount}>{amount}</bdi> <span className={styles.currency}>{currency}</span></>;
  return strong ? <strong>{content}</strong> : <span>{content}</span>;
}

function ComplianceTag({ value, tr }: { value: Compliance; tr: Translate }) {
  return <span className={styles.compliance} data-value={value}>{tr(COMPLIANCE_LABELS[value])}</span>;
}

function Pager({ total, offset, limit, onChange, tr }: { total: number; offset: number; limit: number; onChange: (offset: number, limit: number) => void; tr: Translate }) {
  const page = pageOf(Array.from({ length: total }), offset, limit);
  return <nav className={styles.pager} aria-label={tr('Material pages')}>
    <span role="status" aria-live="polite">{`${tr('Showing')} ${page.from}–${page.to} ${tr('of')} ${total} ${tr('materials')}`}</span>
    <div className={styles.pagerControls}>
      <label>{tr('Rows per page')}<select value={limit} onChange={event => onChange(0, Number(event.target.value))}>
        {PAGE_SIZES.map(size => <option key={size} value={size}>{size}</option>)}</select></label>
      <Button variant="outline" size="sm" aria-label={tr('Previous page')} disabled={page.offset <= 0} onClick={() => onChange(Math.max(0, page.offset - limit), limit)}>
        <IconChevronLeft size={15} className={styles.flip}/></Button>
      <Button variant="outline" size="sm" aria-label={tr('Next page')} disabled={page.offset + limit >= total} onClick={() => onChange(page.offset + limit, limit)}>
        <IconChevronRight size={15} className={styles.flip}/></Button>
    </div>
  </nav>;
}

function MaterialsTab({ lines, unquoted, currency, categoryName, page, setPage, tr }: {
  lines: ReviewLine[]; unquoted: Schemas['RFQItemOut'][]; currency: string; categoryName: (id: string | null) => string;
  page: { offset: number; limit: number }; setPage: (page: { offset: number; limit: number }) => void; tr: Translate;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const paged = lines.length > PAGE_SIZES[0];
  const visible = paged ? pageOf(lines, page.offset, page.limit).rows : lines;
  const columns = 14;
  return <div className={styles.panelBody}>
    {unquoted.length > 0 && <details className={styles.notice}>
      <summary><IconAlertTriangle size={15}/>{`${tr('Partial quotation')}: ${unquoted.length} ${tr('requested materials are not priced')}`}</summary>
      <ul>{unquoted.map(item => <li key={item.id}>{item.description} <span className={styles.muted}>· {formatQuantity(item.quantity)} {item.unit}</span></li>)}</ul>
    </details>}
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className={styles.srOnly}>{tr('Quoted materials')}</caption>
        <thead><tr>
          <th scope="col" className={styles.expandCol}><span className={styles.srOnly}>{tr('Details')}</span></th>
          <th scope="col" className={styles.hideLg}>{tr('Main Category')}</th>
          <th scope="col" className={styles.hideLg}>{tr('Sub Category')}</th>
          <th scope="col">{tr('Material description')}</th>
          <th scope="col" className={styles.hideSm}>{tr('Brand')}</th>
          <th scope="col" className={styles.hideMd}>{tr('Model')}</th>
          <th scope="col" className={styles.hideSm}>{tr('Unit')}</th>
          <th scope="col" className={`${styles.num} ${styles.hideMd}`}>{tr('Requested qty')}</th>
          <th scope="col" className={`${styles.num} ${styles.hideSm}`}>{tr('Offered qty')}</th>
          <th scope="col" className={`${styles.num} ${styles.hideSm}`}>{tr('Unit price')}</th>
          <th scope="col" className={`${styles.num} ${styles.hideMd}`}>{tr('Discount')}</th>
          <th scope="col" className={styles.num}>{tr('Line total')}</th>
          <th scope="col" className={`${styles.num} ${styles.hideMd}`}>{tr('Lead time')}</th>
          <th scope="col" className={styles.hideSm}>{tr('Compliance')}</th>
        </tr></thead>
        <tbody>
          {!visible.length ? <tr><td colSpan={columns} className={styles.emptyCell}>{tr('This quotation has no priced materials.')}</td></tr>
            : visible.map(row => {
              const open = expanded === row.id;
              const detailsId = `quote-line-${row.id}`;
              const quantityDiffers = row.requestedQuantity != null && row.doc.quantity != null && Number(row.requestedQuantity) !== Number(row.doc.quantity);
              return <Fragment key={row.id}>
                <tr className={styles.lineRow} data-expanded={open}>
                  <td className={styles.expandCol}><button type="button" className={styles.expand} aria-expanded={open} aria-controls={detailsId}
                    aria-label={`${tr('Line details')}: ${row.description}`} onClick={() => setExpanded(open ? null : row.id)}><IconChevronDown size={16}/></button></td>
                  <td className={styles.hideLg}>{categoryName(row.mainCategoryId)}</td>
                  <td className={styles.hideLg}>{categoryName(row.subcategoryId)}</td>
                  <td><span className={styles.primary}>{row.description || '—'}</span>
                    {row.doc.offered_description && row.doc.offered_description !== row.description && <small className={styles.sub}>{tr('Offered')}: {row.doc.offered_description}</small>}</td>
                  <td className={styles.hideSm}>{row.doc.brand || '—'}</td>
                  <td className={styles.hideMd}>{row.doc.model || '—'}</td>
                  <td className={styles.hideSm}>{row.doc.unit || '—'}</td>
                  <td className={`${styles.num} ${styles.hideMd}`}>{formatQuantity(row.requestedQuantity)}</td>
                  <td className={`${styles.num} ${styles.hideSm}`} data-differs={quantityDiffers}>{formatQuantity(row.doc.quantity)}</td>
                  <td className={`${styles.num} ${styles.hideSm}`}><Money value={row.doc.unit_price} currency={currency}/></td>
                  <td className={`${styles.num} ${styles.hideMd}`}>{isPositive(row.doc.discount_percent)
                    ? <span className={styles.stack}><span>{formatQuantity(row.doc.discount_percent)}%</span><small className={styles.muted}>−{formatAmount(row.doc.discount_amount)}</small></span> : '—'}</td>
                  <td className={styles.num}><Money value={row.doc.line_total} currency={currency} strong/></td>
                  <td className={`${styles.num} ${styles.hideMd}`}>{row.doc.lead_days != null ? `${row.doc.lead_days} ${tr('days')}` : '—'}</td>
                  <td className={styles.hideSm}><ComplianceTag value={row.compliance} tr={tr}/></td>
                </tr>
                {open && <tr className={styles.detailsRow} id={detailsId}><td colSpan={columns}>
                  <div className={styles.details}>
                    <dl className={styles.facts}>
                      <div className={styles.showLg}><dt>{tr('Category')}</dt><dd>{[categoryName(row.mainCategoryId), categoryName(row.subcategoryId)].filter(value => value !== '—').join(' › ') || '—'}</dd></div>
                      <div className={styles.showSm}><dt>{tr('Brand / model')}</dt><dd>{[row.doc.brand, row.doc.model].filter(Boolean).join(' / ') || '—'}</dd></div>
                      <div className={styles.showMd}><dt>{tr('Requested qty')}</dt><dd>{formatQuantity(row.requestedQuantity)} {row.doc.unit}</dd></div>
                      <div className={styles.showSm}><dt>{tr('Offered qty')}</dt><dd>{formatQuantity(row.doc.quantity)} {row.doc.unit}</dd></div>
                      <div className={styles.showSm}><dt>{tr('Unit price')}</dt><dd><Money value={row.doc.unit_price} currency={currency}/></dd></div>
                      <div className={styles.showMd}><dt>{tr('Lead time')}</dt><dd>{row.doc.lead_days != null ? `${row.doc.lead_days} ${tr('days')}` : '—'}</dd></div>
                      <div className={styles.showSm}><dt>{tr('Compliance')}</dt><dd><ComplianceTag value={row.compliance} tr={tr}/></dd></div>
                      {isPositive(row.doc.discount_amount) && <div><dt>{tr('Net after line discount')}</dt><dd><Money value={row.doc.net_total} currency={currency}/></dd></div>}
                      {row.doc.vat_rate && <div><dt>{tr('VAT rate')}</dt><dd>{percent(row.doc.vat_rate) || '—'}</dd></div>}
                      {row.line?.warranty_months != null && <div><dt>{tr('Warranty')}</dt><dd>{row.line.warranty_months} {tr('months')}</dd></div>}
                      {row.line?.availability && row.line.availability !== 'unknown' && <div><dt>{tr('Availability')}</dt><dd>{tr(row.line.availability.replaceAll('_', ' '))}</dd></div>}
                    </dl>
                    <div className={styles.specGrid}>
                      <section><h4>{tr('Requested specification')}</h4><p>{row.requestedSpecs || <span className={styles.muted}>{tr('No specification provided in the RFQ.')}</span>}</p>
                        {row.requiredBrand && <p><span className={styles.muted}>{tr('Required brand')}:</span> {row.requiredBrand}</p>}</section>
                      <section><h4>{tr('Offered description')}</h4><p>{row.doc.offered_description || <span className={styles.muted}>{tr('Not provided')}</span>}</p></section>
                      <section><h4>{tr('Offered specification')}</h4><p>{row.doc.specification || <span className={styles.muted}>{tr('Not provided')}</span>}</p></section>
                      <section><h4>{tr('Technical notes and remarks')}</h4><p>{row.line?.note || <span className={styles.muted}>{tr('No remarks from the supplier.')}</span>}</p></section>
                    </div>
                  </div>
                </td></tr>}
              </Fragment>;
            })}
        </tbody>
      </table>
    </div>
    {paged && <Pager total={lines.length} offset={page.offset} limit={page.limit} onChange={(offset, limit) => setPage({ offset, limit })} tr={tr}/>}
  </div>;
}

function TechnicalTab({ lines, tr }: { lines: ReviewLine[]; tr: Translate }) {
  const [filter, setFilter] = useState<Compliance | ''>('');
  const counts = complianceCounts(lines);
  const rows = filter ? lines.filter(line => line.compliance === filter) : lines;
  return <div className={styles.panelBody}>
    <div className={styles.toolbar}>
      <div className={styles.chips} role="group" aria-label={tr('Filter by compliance')}>
        <button type="button" aria-pressed={!filter} onClick={() => setFilter('')}>{tr('All')}<span className={styles.count}>{lines.length}</span></button>
        {COMPLIANCE.map(value => <button key={value} type="button" aria-pressed={filter === value} data-value={value} onClick={() => setFilter(value)}>
          {tr(COMPLIANCE_LABELS[value])}<span className={styles.count}>{counts[value]}</span></button>)}
      </div>
    </div>
    <p className={styles.note}>{tr('Compliance is declared by the supplier for each line. Supplier matching does not confirm technical compliance; verify specifications before award.')}</p>
    <div className={styles.tableWrap}>
      <table className={`${styles.table} ${styles.techTable}`}>
        <caption className={styles.srOnly}>{tr('Technical Comparison')}</caption>
        <thead><tr>
          <th scope="col">{tr('Material')}</th><th scope="col">{tr('Requested specification')}</th>
          <th scope="col">{tr('Offered specification')}</th><th scope="col">{tr('Compliance')}</th>
        </tr></thead>
        <tbody>
          {!rows.length ? <tr><td colSpan={4} className={styles.emptyCell}>{tr('No lines with this compliance status.')}</td></tr>
            : rows.map(row => <tr key={row.id}>
              <th scope="row"><span className={styles.primary}>{row.description}</span></th>
              <td><p className={styles.spec}>{row.requestedSpecs || <span className={styles.muted}>{tr('No specification provided in the RFQ.')}</span>}</p>
                {row.requiredBrand && <small className={styles.sub}>{tr('Required brand')}: {row.requiredBrand}</small>}</td>
              <td>{(row.doc.brand || row.doc.model) && <span className={styles.primary}>{[row.doc.brand, row.doc.model].filter(Boolean).join(' / ')}</span>}
                <p className={styles.spec}>{row.doc.specification || row.doc.offered_description || <span className={styles.muted}>{tr('Not provided')}</span>}</p></td>
              <td><ComplianceTag value={row.compliance} tr={tr}/></td>
            </tr>)}
        </tbody>
      </table>
    </div>
  </div>;
}

function CommercialTab({ detail, doc, tr, arabic }: { detail: QuoteDetail; doc: QuoteDocument; tr: Translate; arabic: boolean }) {
  const terms = (detail.quotation.commercial_terms || {}) as Record<string, unknown>;
  const value = (key: string) => typeof terms[key] === 'string' && (terms[key] as string).trim() ? (terms[key] as string) : null;
  const rates = vatRates(detail);
  const valid = validity(doc.validity_date || value('validity_until'));
  const missing = <span className={styles.muted}>{tr('Not provided')}</span>;
  const blocks: [string, React.ReactNode][] = [
    [tr('Payment terms'), value('payment_terms') || missing],
    [tr('Delivery terms'), value('delivery_terms') || missing],
    [tr('Quotation validity'), valid ? `${formatDate(valid.date, arabic)}${valid.expired ? ` · ${tr('Expired')}` : ''}` : missing],
    [tr('VAT percentage'), rates.length ? rates.join(' / ') : missing],
    [tr('Supplier reference'), value('quotation_reference') || missing],
    [tr('Price basis'), detail.quotation.price_basis === 'supply_install' ? tr('Supply and installation') : tr('Supply only')],
    [tr('Currency'), detail.quotation.currency],
  ];
  const conditions = value('terms_and_conditions');
  return <div className={styles.panelBody}>
    <dl className={styles.termGrid}>{blocks.map(([label, content]) => <div key={label}><dt>{label}</dt><dd>{content}</dd></div>)}</dl>
    <section className={styles.block}><h3>{tr('Terms and conditions')}</h3>
      {conditions ? <p className={styles.preWrap}>{conditions}</p> : <p className={styles.muted}>{tr('The supplier did not add terms and conditions.')}</p>}</section>
  </div>;
}

function AttachmentsTab({ quoteId, attachments, tr }: { quoteId: string; attachments: Schemas['RFQAttachmentOut'][]; tr: Translate }) {
  const toast = useToast();
  const [busy, setBusy] = useState('');
  const run = async (id: string, filename: string, mode: 'preview' | 'download') => {
    setBusy(`${id}:${mode}`);
    try { await openFile(`quotations/${quoteId}/attachments/${id}`, filename, mode); }
    catch { toast.error(tr('Could not open this attachment.')); }
    finally { setBusy(''); }
  };
  if (!attachments.length) return <p className={styles.empty}>{tr('The supplier did not attach technical documents, datasheets or files to this quotation.')}</p>;
  return <ul className={styles.files}>
    {attachments.map(file => <li key={file.id}>
      <IconFileText size={18} className={styles.fileIcon}/>
      <span className={styles.fileName}><span className={styles.primary}>{file.filename}</span>
        <small className={styles.muted}>{file.content_type.split('/').pop()?.toUpperCase()} · {fileSize(file.size)}</small></span>
      <span className={styles.rowActions}>
        {PREVIEWABLE.test(file.content_type) && <Button variant="ghost" size="sm" disabled={!!busy} onClick={() => void run(file.id, file.filename, 'preview')}>
          <IconEye size={15}/>{busy === `${file.id}:preview` ? tr('Opening…') : tr('Preview')}</Button>}
        <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void run(file.id, file.filename, 'download')}>
          <IconDownload size={15}/>{busy === `${file.id}:download` ? tr('Downloading…') : tr('Download')}</Button>
      </span>
    </li>)}
  </ul>;
}

function ActivityTab({ quoteId, detail, tr, arabic }: { quoteId: string; detail: QuoteDetail; tr: Translate; arabic: boolean }) {
  // Secondary data: loaded only when this tab opens.
  const history = useApi<HistoryEvent[]>(`quotations/${quoteId}/history`, { staleTimeMs: 30000 });
  const revisions = useApi<Revision[]>(`quotations/${quoteId}/revisions`, { staleTimeMs: 30000 });
  const events = history.data || [];
  const describe = (event: HistoryEvent) => {
    const details = event.details || {};
    switch (event.action) {
      case 'quotation.submitted': return { tone: 'info', title: tr('Quotation submitted'),
        note: details.source === 'automatic' ? tr('Submitted automatically from authorized saved rates') : tr('Submitted by the supplier') };
      case 'quotation.revision_started': return { tone: 'neutral', title: `${tr('Supplier started revision')} ${details.revision ?? ''}`.trim(),
        note: details.rfq_revision != null ? `${tr('For RFQ revision')} ${details.rfq_revision}` : '' };
      case 'quotation.shortlisted': return { tone: 'info', title: tr('Shortlisted by buyer'), note: '' };
      case 'quotation.awarded': return { tone: 'positive', title: tr('Awarded by buyer'), note: tr('A draft purchase order was prepared.') };
      case 'quotation.rejected': return { tone: 'negative', title: tr('Rejected by buyer'), note: typeof details.reason === 'string' ? `${tr('Reason')}: ${details.reason}` : '' };
      default: return { tone: 'neutral', title: event.action, note: '' };
    }
  };
  // Older quotations may predate submission audit events; their stored submission time is still real.
  const hasSubmission = events.some(event => event.action === 'quotation.submitted');
  const timeline = [...(!hasSubmission && detail.quotation.submitted_at ? [{ action: 'quotation.submitted', created_at: detail.quotation.submitted_at, details: { source: detail.quotation.submission_source } }] : []), ...events]
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  if (history.error && !history.data) return <State error={history.error} retry={history.reload}/>;
  return <div className={styles.panelBody}>
    {history.loading && !history.data ? <ul className={styles.timeline} aria-hidden="true">{[0, 1, 2].map(key => <li key={key} className={styles.skeletonLine}><span/><span/></li>)}</ul>
      : !timeline.length ? <p className={styles.empty}>{tr('No activity recorded for this quotation yet.')}</p>
      : <ol className={styles.timeline}>{timeline.map((event, index) => {
        const info = describe(event);
        return <li key={`${event.action}-${event.created_at}-${index}`} data-tone={info.tone}>
          <span className={styles.primary}>{info.title}</span>
          <time dateTime={event.created_at} className={styles.muted}>{formatDate(event.created_at, arabic, true)}</time>
          {info.note && <p>{info.note}</p>}
        </li>;
      })}</ol>}
    {!!revisions.data?.length && <section className={styles.block}><h3>{tr('Archived versions')}</h3>
      <ul className={styles.revisions}>{revisions.data.map(entry => <li key={entry.id}>
        <span className={styles.primary}>{tr('Version')} {entry.number}</span>
        <span className={styles.muted}>{tr(STATUS_LABELS[entry.status] || entry.status)} · {tr('RFQ revision')} {entry.rfq_revision} · {formatDate(entry.created_at, arabic, true)}</span>
      </li>)}</ul></section>}
  </div>;
}

function FinancialSummary({ doc, detail, tr }: { doc: QuoteDocument; detail: QuoteDetail; tr: Translate }) {
  // Authoritative backend totals (the same invoice() values the PDF and comparison page use).
  const totals = { ...(detail.totals || {}), ...(doc.totals || {}) };
  const currency = totals.currency || detail.quotation.currency;
  const rates = vatRates(detail);
  return <section className={styles.summary} aria-labelledby="quote-financial-summary">
    <h2 id="quote-financial-summary">{tr('Financial summary')}</h2>
    <dl>
      <div><dt>{tr('Subtotal')}</dt><dd><Money value={totals.subtotal} currency={currency}/></dd></div>
      <div><dt>{tr('Discount')}</dt><dd>{isPositive(totals.discount) ? <span>−<Money value={totals.discount} currency={currency}/></span> : <Money value="0.00" currency={currency}/>}</dd></div>
      {isPositive(totals.delivery_charge) && <div><dt>{tr('Delivery charges')}</dt><dd><Money value={totals.delivery_charge} currency={currency}/></dd></div>}
      <div className={styles.rule}><dt>{tr('Net amount before VAT')}</dt><dd><Money value={totals.net_amount} currency={currency}/></dd></div>
      <div><dt>{tr('VAT amount')}{rates.length ? ` (${rates.join(' / ')})` : ''}</dt><dd><Money value={totals.vat} currency={currency}/></dd></div>
      <div className={styles.grand}><dt>{tr('Grand total')}</dt><dd><Money value={totals.total} currency={currency} strong/></dd></div>
      <div><dt>{tr('Currency')}</dt><dd>{currency}</dd></div>
    </dl>
  </section>;
}

function DecisionPanel({ detail, rfqRevision, onDecided, tr, arabic }: {
  detail: QuoteDetail; rfqRevision?: number | null; onDecided: () => void; tr: Translate; arabic: boolean;
}) {
  const toast = useToast();
  const [confirm, setConfirm] = useState<Decision | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState<Decision | null>(null);
  const [error, setError] = useState('');
  const { decisions, awardBlocked } = allowedDecisions(detail, rfqRevision);
  const status = detail.quotation.status;
  const compareHref = `/app/buyer/compare?rfq=${detail.quotation.rfq_id}`;
  const doc = (detail.document || {}) as QuoteDocument;
  const decide = async (decision: Decision) => {
    if (busy) return;
    setBusy(decision); setError('');
    try {
      const reasonText = reason.trim();
      await api(`quotations/${detail.quotation.id}/decision/${decision}`, { method: 'POST', ...(decision === 'rejected' && reasonText ? { body: json({ reason: reasonText }) } : {}) });
      toast.success(decision === 'awarded' ? tr('Quotation awarded') : decision === 'rejected' ? tr('Quotation rejected') : tr('Quotation shortlisted'));
      setConfirm(null); setReason('');
      window.dispatchEvent(new Event('procura:data-changed'));
      onDecided();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : tr('The decision could not be recorded.');
      if (confirm) setError(message); else toast.error(tr('Action failed'), message);
    } finally { setBusy(null); }
  };
  const final = status === 'awarded' || status === 'rejected';
  return <section className={styles.decision} aria-labelledby="quote-decision">
    <h2 id="quote-decision">{tr('Buyer decision')}</h2>
    {final ? <p className={styles.decisionState} data-status={status}>{status === 'awarded' ? tr('This quotation has been awarded.') : tr('This quotation has been rejected.')}</p>
      : !decisions.length ? <p className={styles.note}>{tr('This quotation is not available for a decision.')}</p> : null}
    <div className={styles.decisionActions}>
      {decisions.includes('shortlisted') && <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void decide('shortlisted')}>
        <IconBookmark size={15}/>{busy === 'shortlisted' ? tr('Saving…') : tr('Shortlist quotation')}</Button>}
      {decisions.includes('awarded') && <Button size="sm" disabled={!!busy || !!awardBlocked} onClick={() => { setError(''); setConfirm('awarded'); }}
        aria-describedby={awardBlocked ? 'award-blocked' : undefined}><IconTrophy size={15}/>{tr('Award quotation')}</Button>}
      {decisions.includes('rejected') && <Button variant="outline" size="sm" className={styles.danger} disabled={!!busy} onClick={() => { setError(''); setConfirm('rejected'); }}>
        <IconX size={15}/>{tr('Reject quotation')}</Button>}
      <Link className={styles.buttonLink} href={compareHref}>{tr('Compare quotations')}<IconArrowUpRight size={14} className={styles.flip}/></Link>
    </div>
    {awardBlocked && decisions.includes('awarded') && <p id="award-blocked" className={styles.note}>{tr('The RFQ changed after this quotation was submitted. The supplier must revise it before it can be awarded.')}</p>}
    <Dialog.Root open={!!confirm} onOpenChange={open => { if (!open && !busy) { setConfirm(null); setError(''); } }}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay}/>
        <Dialog.Content className={styles.dialog} dir={arabic ? 'rtl' : 'ltr'}>
          <Dialog.Title>{confirm === 'awarded' ? tr('Award this quotation?') : tr('Reject this quotation?')}</Dialog.Title>
          <Dialog.Description>{confirm === 'awarded'
            ? tr('The supplier will be notified and a draft purchase order will be prepared. An award cannot be reversed from this page.')
            : tr('The supplier will be notified that their quotation was not selected. A rejected quotation cannot be awarded later.')}</Dialog.Description>
          <dl className={styles.dialogFacts}>
            <div><dt>{tr('Supplier')}</dt><dd>{detail.supplier_name || doc.supplier?.name || '—'}</dd></div>
            <div><dt>{tr('Quotation')}</dt><dd>{doc.quotation_reference || '—'}</dd></div>
            <div><dt>{tr('Grand total')}</dt><dd><Money value={doc.totals?.total ?? detail.totals?.total} currency={detail.quotation.currency} strong/></dd></div>
          </dl>
          {confirm === 'rejected' && <label className={styles.reason}>{tr('Rejection reason (optional)')}
            <textarea value={reason} maxLength={500} rows={3} onChange={event => setReason(event.target.value)} placeholder={tr('For example: price above budget, specification not met')}/>
            <small className={styles.muted}>{reason.length}/500 · {tr('Recorded in the quotation activity.')}</small></label>}
          {error && <p role="alert" className={styles.dialogError}>{error}</p>}
          <div className={styles.dialogActions}>
            <Button variant="outline" disabled={!!busy} autoFocus onClick={() => { setConfirm(null); setError(''); }}>{tr('Cancel')}</Button>
            <Button className={confirm === 'rejected' ? styles.dangerSolid : undefined} disabled={!!busy} onClick={() => confirm && void decide(confirm)}>
              {busy ? tr('Saving…') : confirm === 'awarded' ? tr('Confirm award') : tr('Confirm rejection')}</Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </section>;
}

function PrintSheet({ doc, lines, currency, detail, tr, arabic }: { doc: QuoteDocument; lines: ReviewLine[]; currency: string; detail: QuoteDetail; tr: Translate; arabic: boolean }) {
  // Complete record for printing: every line, unpaginated, plus terms and the one financial summary.
  const terms = (detail.quotation.commercial_terms || {}) as Record<string, string | undefined>;
  return <div className={styles.printOnly} aria-hidden="true">
    <table className={styles.printTable}>
      <thead><tr><th>#</th><th>{tr('Material description')}</th><th>{tr('Brand / model')}</th><th>{tr('Unit')}</th><th>{tr('Offered qty')}</th>
        <th>{tr('Unit price')}</th><th>{tr('Discount')}</th><th>{tr('Line total')}</th><th>{tr('Lead time')}</th><th>{tr('Compliance')}</th></tr></thead>
      <tbody>{lines.map((row, index) => <tr key={row.id}>
        <td>{index + 1}</td><td>{row.description}{row.doc.specification ? <small> {row.doc.specification}</small> : null}</td>
        <td>{[row.doc.brand, row.doc.model].filter(Boolean).join(' / ') || '—'}</td><td>{row.doc.unit || '—'}</td>
        <td>{formatQuantity(row.doc.quantity)}</td><td>{formatAmount(row.doc.unit_price)}</td>
        <td>{isPositive(row.doc.discount_percent) ? `${formatQuantity(row.doc.discount_percent)}%` : '—'}</td>
        <td>{formatAmount(row.doc.line_total)}</td><td>{row.doc.lead_days ?? '—'}</td><td>{tr(COMPLIANCE_LABELS[row.compliance])}</td>
      </tr>)}</tbody>
    </table>
    <div className={styles.printFooter}>
      <dl>
        {terms.payment_terms && <><dt>{tr('Payment terms')}</dt><dd>{terms.payment_terms}</dd></>}
        {terms.delivery_terms && <><dt>{tr('Delivery terms')}</dt><dd>{terms.delivery_terms}</dd></>}
        {doc.validity_date && <><dt>{tr('Quotation validity')}</dt><dd>{formatDate(doc.validity_date, arabic)}</dd></>}
        {terms.terms_and_conditions && <><dt>{tr('Terms and conditions')}</dt><dd className={styles.preWrap}>{terms.terms_and_conditions}</dd></>}
      </dl>
      <FinancialSummary doc={doc} detail={detail} tr={tr}/>
    </div>
    <p className={styles.muted}>{tr('Currency')}: {currency}</p>
  </div>;
}

function ReviewSkeleton() {
  return <div className={styles.skeleton} aria-hidden="true">
    <div className={styles.skeletonHeader}><span/><span/><span/></div>
    <div className={styles.cards}>{[0, 1, 2, 3].map(key => <div key={key} className={styles.card}><span className={styles.skeletonBar}/><span className={styles.skeletonValue}/></div>)}</div>
    <div className={styles.skeletonTable}>{[0, 1, 2, 3, 4, 5].map(key => <span key={key}/>)}</div>
  </div>;
}

/**
 * Buyer quotation review: a read-only view of the supplier's submitted commercial
 * record. No supplier field is editable here; buyer decisions go through the
 * existing decision endpoint, which enforces status, permission and award rules.
 */
export function BuyerQuotationReview({ id, role }: { id: string; role: string }) {
  const { t, arabic } = useLocale();
  const tr: Translate = english => arabic ? reviewArabic[english] || t(english) : english;
  const [tab, setTabState] = useState<Tab>(() => {
    const value = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('tab') : null;
    return isTab(value) ? value : 'materials';
  });
  const [materialsPage, setMaterialsPage] = useState({ offset: 0, limit: PAGE_SIZES[0] as number });
  const [downloading, setDownloading] = useState<'en' | 'ar' | null>(null);
  const toast = useToast();
  const quote = useApi<QuoteDetail>(`quotations/${id}`);
  const detail = quote.data;
  // Requested lines and the current RFQ revision; shared cache with the RFQ pages.
  const rfq = useApi<Schemas['RFQDetail']>(detail ? `rfqs/${detail.quotation.rfq_id}` : null, { staleTimeMs: 120000 });
  const categories = useApi<Schemas['CategoryOut'][]>(detail ? 'taxonomy/categories' : null, { staleTimeMs: 1800000 });
  const categoryNames = useMemo(() => new Map((categories.data || []).map(row => [row.id, arabic && row.name_ar ? row.name_ar : row.name_en])), [categories.data, arabic]);
  const lines = useMemo(() => reviewLines(detail, rfq.data?.items || []), [detail, rfq.data]);
  const doc = (detail?.document || {}) as QuoteDocument;

  const setTab = (next: Tab) => {
    setTabState(next);
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      if (next === 'materials') url.searchParams.delete('tab'); else url.searchParams.set('tab', next);
      window.history.replaceState({}, '', url.toString());
    }
  };
  const onTabKey = (event: React.KeyboardEvent) => {
    const index = TABS.indexOf(tab);
    const forward = arabic ? 'ArrowLeft' : 'ArrowRight';
    const back = arabic ? 'ArrowRight' : 'ArrowLeft';
    const next = event.key === forward ? TABS[(index + 1) % TABS.length] : event.key === back ? TABS[(index + TABS.length - 1) % TABS.length]
      : event.key === 'Home' ? TABS[0] : event.key === 'End' ? TABS[TABS.length - 1] : null;
    if (!next) return;
    event.preventDefault(); setTab(next);
    document.getElementById(`quote-tab-${next}`)?.focus();
  };
  const download = async (lang: 'en' | 'ar') => {
    setDownloading(lang);
    try { await openFile(`quotations/${id}/export.pdf?lang=${lang}`, `Quotation-${doc.quotation_reference || id}-${lang}.pdf`, 'download'); }
    catch { toast.error(tr('Could not download the quotation PDF.')); }
    finally { setDownloading(null); }
  };

  const backLink = <Link className={styles.back} href={`/app/${role === 'admin' ? 'admin' : 'buyer'}/quotations`}><IconArrowLeft size={15} className={styles.flip}/>{tr('Back to Quotations')}</Link>;
  if (!detail) return <div className={styles.page} dir={arabic ? 'rtl' : 'ltr'}>
    {backLink}
    {quote.error ? <State error={quote.error} retry={quote.reload}/> : <ReviewSkeleton/>}
  </div>;

  const currency = doc.currency || detail.quotation.currency;
  const status = detail.quotation.status;
  const kind = sourceKind(doc.submission_source || detail.quotation.submission_source);
  const valid = validity(doc.validity_date);
  const lead = leadTime(lines);
  const unquoted = unquotedItems(lines, rfq.data?.items);
  const requestedCount = rfq.data ? rfq.data.items.filter(item => item.is_active !== false).length : null;
  const categoryName = (categoryId: string | null) => (categoryId && categoryNames.get(categoryId)) || '—';
  const grandTotal = doc.totals?.total ?? detail.totals?.total;
  const facts: [string, React.ReactNode][] = [
    [tr('RFQ reference'), detail.rfq_reference || doc.rfq_reference || '—'],
    [tr('Project'), doc.rfq_title || rfq.data?.rfq.project_name || rfq.data?.rfq.title || '—'],
    [tr('Supplier'), detail.supplier_name || doc.supplier?.name || '—'],
    [tr('Buyer'), doc.buyer?.name || '—'],
    [tr('Quotation date'), formatDate(doc.quotation_date, arabic) || '—'],
    [tr('Valid until'), valid ? formatDate(valid.date, arabic) : tr('Not specified')],
  ];

  return <div className={styles.page} dir={arabic ? 'rtl' : 'ltr'}>
    <div className={styles.screenOnly}>
      <div className={styles.topBar}>{backLink}</div>
      <header className={styles.header}>
        <div className={styles.headerTop}>
          <div className={styles.titleBlock}>
            <h1><span className={styles.srOnly}>{tr('Quotation')} </span><bdi>{doc.quotation_reference || '—'}</bdi></h1>
            <span className={styles.status} data-status={status}>{tr(STATUS_LABELS[status] || status)}</span>
            <span className={styles.kind}>{kind === 'automatic' ? tr('Automatic quotation') : tr('Manual quotation')}</span>
          </div>
          <div className={styles.headerActions}>
            <Button variant="outline" size="sm" disabled={!!downloading} onClick={() => void download('en')}><IconDownload size={15}/>{downloading === 'en' ? tr('Preparing PDF…') : tr('PDF (English)')}</Button>
            <Button variant="outline" size="sm" disabled={!!downloading} onClick={() => void download('ar')}><IconDownload size={15}/>{downloading === 'ar' ? tr('Preparing PDF…') : tr('PDF (Arabic)')}</Button>
            <Button variant="outline" size="sm" onClick={() => window.print()}><IconPrinter size={15}/>{tr('Print')}</Button>
          </div>
        </div>
        <dl className={styles.facts}>{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      </header>

      {detail.quotation.review_required && <p role="status" className={styles.warning}><IconAlertTriangle size={15}/>
        {tr('The RFQ changed after this quotation was submitted. The supplier has been asked to review and resubmit.')}</p>}

      <dl className={styles.cards} aria-label={tr('Quotation summary')}>
        <div className={styles.card}><dt>{tr('Grand total')}</dt><dd><Money value={grandTotal} currency={currency} strong/></dd><small>{tr('Including VAT')}</small></div>
        <div className={styles.card}><dt>{tr('Quoted materials')}</dt>
          <dd>{lines.length}{requestedCount != null && <span className={styles.of}> / {requestedCount}</span>}</dd>
          <small>{requestedCount == null ? tr('Priced lines') : unquoted.length ? `${unquoted.length} ${tr('not priced')}` : tr('All requested materials priced')}</small></div>
        <div className={styles.card}><dt>{tr('Delivery lead time')}</dt>
          <dd>{lead ? lead.min === lead.max ? `${lead.max} ${tr('days')}` : `${lead.min}–${lead.max} ${tr('days')}` : '—'}</dd>
          <small>{lead ? lead.min === lead.max ? tr('All lines') : tr('Shortest to longest line') : tr('Not provided')}</small></div>
        <div className={styles.card} data-tone={valid?.expired ? 'negative' : valid && valid.daysLeft <= 7 ? 'warning' : undefined}><dt>{tr('Quotation validity')}</dt>
          <dd>{valid ? formatDate(valid.date, arabic) : tr('Not specified')}</dd>
          <small>{!valid ? tr('The supplier did not state a validity date') : valid.expired ? tr('Expired') : `${valid.daysLeft} ${tr('days remaining')}`}</small></div>
      </dl>

      <div className={styles.layout}>
        <div className={styles.main}>
          <div className={styles.tabs} role="tablist" aria-label={tr('Quotation sections')} onKeyDown={onTabKey}>
            {TABS.map(key => <button key={key} type="button" role="tab" id={`quote-tab-${key}`} aria-selected={tab === key} aria-controls="quote-tabpanel"
              tabIndex={tab === key ? 0 : -1} onClick={() => setTab(key)}>{tr(TAB_LABELS[key])}
              {key === 'materials' && <span className={styles.count}>{lines.length}</span>}
              {key === 'attachments' && <span className={styles.count}>{detail.attachments?.length || 0}</span>}</button>)}
          </div>
          <section className={styles.panel} role="tabpanel" id="quote-tabpanel" aria-labelledby={`quote-tab-${tab}`}>
            {tab === 'materials' && <MaterialsTab lines={lines} unquoted={unquoted} currency={currency} categoryName={categoryName} page={materialsPage} setPage={setMaterialsPage} tr={tr}/>}
            {tab === 'technical' && <TechnicalTab lines={lines} tr={tr}/>}
            {tab === 'commercial' && <CommercialTab detail={detail} doc={doc} tr={tr} arabic={arabic}/>}
            {tab === 'attachments' && <AttachmentsTab quoteId={id} attachments={detail.attachments || []} tr={tr}/>}
            {tab === 'activity' && <ActivityTab quoteId={id} detail={detail} tr={tr} arabic={arabic}/>}
          </section>
        </div>
        <aside className={styles.aside}>
          <FinancialSummary doc={doc} detail={detail} tr={tr}/>
          {role === 'buyer' && <DecisionPanel detail={detail} rfqRevision={rfq.data?.rfq.revision} onDecided={quote.reload} tr={tr} arabic={arabic}/>}
        </aside>
      </div>
    </div>

    <div className={styles.printOnly} aria-hidden="true">
      <h1 className={styles.printTitle}>{tr('Quotation')} {doc.quotation_reference}</h1>
      <dl className={styles.facts}>{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        <div><dt>{tr('Status')}</dt><dd>{tr(STATUS_LABELS[status] || status)}</dd></div></dl>
    </div>
    <PrintSheet doc={doc} lines={lines} currency={currency} detail={detail} tr={tr} arabic={arabic}/>
  </div>;
}
