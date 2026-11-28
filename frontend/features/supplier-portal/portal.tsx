'use client';
// Supplier invitation portal: /supplier-portal#token=...
// The fragment never reaches a server. It is read here, removed from the address bar
// at once, and exchanged once for a restricted HttpOnly session scoped to this single
// invitation. Later requests carry only the invitation id (not secret) plus the cookie.
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { IconAlertCircle, IconCheck, IconDownload, IconFileText, IconLock, IconWorld } from '@tabler/icons-react';
import { apiUrl } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { useLocale } from '@/lib/locale';
import { portalArabic } from './copy';
import styles from './portal.module.css';

type Item = {
  id: string; description: string; specifications: string; quantity: string; unit: string; matched: boolean;
  discipline?: string | null; main_category?: string | null; subcategory?: string | null;
  required_brand?: string | null; brand_mandatory?: boolean;
};
type Line = {
  rfq_item_id: string; unit_price: string; quantity: string; discount_percent: string; brand: string; model: string;
  offered_specs: string; lead_days: number; note: string; prefilled_from_price_book: boolean;
};
type Totals = { subtotal: string; vat: string; total: string; currency: string };
type Portal = {
  invitation_id: string; supplier: { name: string }; buyer: { name: string; visible: boolean };
  rfq: { reference: string; title: string; city: string; delivery_address: string; response_deadline: string;
         required_delivery_date: string | null; instructions: string };
  items: Item[]; lines: Line[];
  quotation: null | { id: string; status: string; currency: string; vat_rate: string; submitted_at: string | null;
                      commercial_terms: Record<string, string>; totals: Totals; submission_source?: string };
  editable: boolean; rfq_open: boolean; invitation_status: string; can_accept: boolean; can_decline: boolean;
  can_download_quotation: boolean; attachments: { id: string; filename: string; size?: number | null }[];
  verification: { verified: boolean; channel: string }; session_expires_at: string;
};
type Failure = { code: string; message: string };
type Draft = Record<string, { price: string; brand: string; model: string; lead: string; discount: string; note: string; specs: string }>;

const STATE_TITLES: Record<string, string> = {
  invalid_link: 'Invalid link', link_expired: 'Link expired', link_revoked: 'Link revoked',
  invitation_withdrawn: 'Invitation withdrawn', invitation_declined: 'Invitation declined',
  invitation_unavailable: 'Access unavailable', supplier_unavailable: 'Access unavailable',
  session_expired: 'Session ended', session_required: 'Secure supplier portal',
};

class PortalError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}

async function call<T>(path: string, invitation: string | null, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');
  if (invitation) headers.set('X-Portal-Invitation', invitation);
  let response: Response;
  try {
    response = await fetch(apiUrl(path), { ...init, headers, credentials: 'include', cache: 'no-store' });
  } catch {
    throw new PortalError('network', 'The service could not be reached. Check your connection and try again.', 0);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    if (detail && typeof detail === 'object' && 'code' in detail) throw new PortalError(String(detail.code), String(detail.message), response.status);
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? String(detail[0]?.msg || '') : '';
    throw new PortalError('error', message || 'Something went wrong. Please try again.', response.status);
  }
  return data as T;
}

const money = (value: number, currency: string) => `${currency} ${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function draftFrom(portal: Portal): Draft {
  const result: Draft = {};
  for (const item of portal.items) {
    const line = portal.lines.find(l => l.rfq_item_id === item.id);
    result[item.id] = {
      // Never invent a price: blank unless the supplier (or their saved price book) set one.
      price: line && Number(line.unit_price) > 0 ? line.unit_price : '', brand: line?.brand || '', model: line?.model || '',
      lead: String(line?.lead_days ?? 7), discount: line?.discount_percent && Number(line.discount_percent) ? line.discount_percent : '',
      note: line?.note || '', specs: line?.offered_specs || '',
    };
  }
  return result;
}

export function SupplierPortal() {
  const { arabic, setArabic } = useLocale();
  const tr = useCallback((text: string) => (arabic ? portalArabic[text] ?? text : text), [arabic]);
  const started = useRef(false);
  const [invitation, setInvitation] = useState<string | null>(null);
  const [portal, setPortal] = useState<Portal | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [terms, setTerms] = useState({ vat: '15', delivery: '', payment: '', validity: '', reference: '', conditions: '' });
  const [declining, setDeclining] = useState(false);
  const [declineReason, setDeclineReason] = useState('');
  const [declined, setDeclined] = useState(false);
  const [codeSentTo, setCodeSentTo] = useState('');
  const [code, setCode] = useState('');
  const [confirm, setConfirm] = useState(false);

  const load = useCallback((next: Portal) => {
    setPortal(next);
    setDraft(draftFrom(next));
    const t = next.quotation?.commercial_terms || {};
    setTerms({
      vat: next.quotation ? String(Math.round(Number(next.quotation.vat_rate) * 10000) / 100) : '15',
      delivery: t.delivery_terms || '', payment: t.payment_terms || '', reference: t.quotation_reference || '',
      validity: t.validity_until ? String(t.validity_until).slice(0, 10) : '', conditions: t.terms_and_conditions || '',
    });
  }, []);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get('token');
    const known = new URLSearchParams(window.location.search).get('invitation');
    // Remove the token from the visible URL and history before anything else.
    if (window.location.hash) window.history.replaceState(null, '', window.location.pathname + window.location.search);
    if (token) {
      call<{ invitation_id: string; portal: Portal }>('supplier-portal/session', null, { method: 'POST', body: JSON.stringify({ token }) })
        .then(result => {
          setInvitation(result.invitation_id);
          window.history.replaceState(null, '', `/supplier-portal?invitation=${encodeURIComponent(result.invitation_id)}`);
          load(result.portal);
        })
        .catch((error: PortalError) => setFailure({ code: error.code, message: error.message }));
    } else if (known && /^[0-9a-f-]{36}$/i.test(known)) {
      setInvitation(known);
      call<Portal>('supplier-portal/me', known).then(load).catch((error: PortalError) => setFailure({ code: error.code, message: error.message }));
    } else {
      setFailure({ code: 'session_required', message: 'Open the secure link from your invitation email or SMS.' });
    }
  }, [load]);

  const act = async <T,>(name: string, path: string, body?: unknown): Promise<T | null> => {
    if (busy) return null;
    setBusy(name); setNotice(null);
    try {
      return await call<T>(path, invitation, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (error) {
      const e = error as PortalError;
      if (['session_expired', 'link_revoked', 'invitation_withdrawn', 'session_required', 'supplier_unavailable'].includes(e.code)) setFailure({ code: e.code, message: e.message });
      else setNotice({ kind: 'error', text: e.message || tr('Something went wrong. Please try again.') });
      return null;
    } finally { setBusy(null); }
  };

  const currency = portal?.quotation?.currency || 'SAR';
  const quotable = useMemo(() => (portal?.items || []).filter(item => item.matched || !portal?.items.some(i => i.matched)), [portal]);
  const others = useMemo(() => (portal?.items || []).filter(item => !quotable.includes(item)), [portal, quotable]);
  const estimate = useMemo(() => {
    let subtotal = 0;
    for (const item of quotable) {
      const row = draft[item.id];
      const price = Number(row?.price || 0);
      if (!(price > 0)) continue;
      subtotal += Number(item.quantity) * price * (1 - Number(row.discount || 0) / 100);
    }
    const vat = subtotal * Number(terms.vat || 0) / 100;
    return { subtotal, vat, total: subtotal + vat };
  }, [draft, quotable, terms.vat]);

  const quotation = () => ({
    currency, price_basis: 'supply', vat_rate: String(Number(terms.vat || 0) / 100),
    commercial_terms: {
      delivery_terms: terms.delivery, payment_terms: terms.payment, quotation_reference: terms.reference,
      terms_and_conditions: terms.conditions, validity_until: terms.validity ? `${terms.validity}T23:59:59Z` : null,
    },
    lines: quotable.filter(item => Number(draft[item.id]?.price || 0) > 0).map(item => {
      const row = draft[item.id];
      return {
        rfq_item_id: item.id, quantity: item.quantity, unit_price: row.price, brand: row.brand, model: row.model,
        offered_specs: row.specs, lead_days: Math.max(0, Math.min(3650, Number(row.lead || 0))), note: row.note,
        discount_percent: row.discount || '0', compliance: 'unconfirmed',
      };
    }),
  });

  const saveDraft = async () => {
    const body = quotation();
    if (!body.lines.length) { setNotice({ kind: 'error', text: tr('Price at least one matched item before submitting.') }); return; }
    const next = await act<Portal>('draft', 'supplier-portal/me/draft', { quotation: body });
    if (next) { load(next); setNotice({ kind: 'success', text: tr('Draft saved privately. The buyer cannot see drafts.') }); }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body = quotation();
    if (!body.lines.length) { setNotice({ kind: 'error', text: tr('Price at least one matched item before submitting.') }); return; }
    const next = await act<Portal>('submit', 'supplier-portal/me/submit', { quotation: body, confirm_submission: confirm });
    if (next) { load(next); setNotice({ kind: 'success', text: tr('Quotation submitted to the buyer.') }); }
  };

  const requestCode = async () => {
    const result = await act<{ sent_to?: string; verified: boolean }>('code', 'supplier-portal/me/verification/request');
    if (result?.verified && portal) setPortal({ ...portal, verification: { ...portal.verification, verified: true } });
    else if (result) setCodeSentTo(result.sent_to || '');
  };
  const confirmCode = async () => {
    const result = await act<{ verified: boolean }>('verify', 'supplier-portal/me/verification/confirm', { code });
    if (result?.verified && portal) { setPortal({ ...portal, verification: { ...portal.verification, verified: true } }); setNotice({ kind: 'success', text: tr('Recipient verified — you can submit.') }); }
  };

  const download = (path: string) => apiUrl(`supplier-portal/me/${path}${path.includes('?') ? '&' : '?'}invitation=${encodeURIComponent(invitation || '')}`);
  const date = (value?: string | null, withTime = false) => {
    if (!value) return tr('Not specified');
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(arabic ? 'ar-SA' : 'en-GB', withTime
      ? { dateStyle: 'medium', timeStyle: 'short' } : { dateStyle: 'medium' });
  };

  const header = (
    <header className={styles.top}>
      <Link href="/" className={styles.brand}>Procumaterial</Link>
      <span className={styles.secure}><IconLock size={15} aria-hidden />{tr('Secure supplier portal')}</span>
      <button type="button" className={styles.language} onClick={() => setArabic(!arabic)} aria-label="Switch language">
        <IconWorld size={16} aria-hidden />{arabic ? 'English' : 'العربية'}
      </button>
    </header>
  );

  if (declined) return <main className={styles.page} dir={arabic ? 'rtl' : 'ltr'}>{header}<section className={styles.stateCard} role="status"><IconCheck size={28} aria-hidden /><h1>{tr('Invitation declined')}</h1><p>{tr('You declined this invitation. The buyer has been informed.')}</p></section></main>;
  if (failure) return <main className={styles.page} dir={arabic ? 'rtl' : 'ltr'}>{header}<section className={styles.stateCard} role="alert"><IconAlertCircle size={28} aria-hidden /><h1>{tr(STATE_TITLES[failure.code] || 'Access unavailable')}</h1><p>{arabic && portalArabic[failure.message] ? portalArabic[failure.message] : failure.message}</p><small>{tr('Need help? Contact the buyer or Procumaterial support.')}</small></section></main>;
  if (!portal) return <main className={styles.page} dir={arabic ? 'rtl' : 'ltr'}>{header}<section className={styles.stateCard} aria-busy="true"><span className={styles.spinner} aria-hidden /><p>{tr('Opening your secure invitation…')}</p></section></main>;

  const quoteStatus = portal.quotation?.status;
  const submitted = Boolean(quoteStatus && quoteStatus !== 'draft');
  const canEdit = portal.editable && portal.invitation_status !== 'declined';

  return (
    <main className={styles.page} dir={arabic ? 'rtl' : 'ltr'} lang={arabic ? 'ar' : 'en'}>
      {header}
      <div className={styles.shell}>
        <section className={styles.hero}>
          <span className={styles.kicker}>{tr('Supplier RFQ invitation')}</span>
          <h1>{portal.rfq.reference} — {portal.rfq.title}</h1>
          <p>{tr('This private link gives access to this request only.')}</p>
          <dl className={styles.facts}>
            <div><dt>{tr('Supplier')}</dt><dd>{portal.supplier.name}</dd></div>
            <div><dt>{tr('Buyer')}</dt><dd>{portal.buyer.name}</dd></div>
            <div><dt>{tr('RFQ reference')}</dt><dd>{portal.rfq.reference}</dd></div>
            <div><dt>{tr('Project')}</dt><dd>{portal.rfq.title}</dd></div>
            <div><dt>{tr('Delivery location')}</dt><dd>{portal.rfq.delivery_address || portal.rfq.city || tr('Not specified')}</dd></div>
            <div><dt>{tr('Required delivery date')}</dt><dd>{date(portal.rfq.required_delivery_date)}</dd></div>
            <div><dt>{tr('Quotation deadline')}</dt><dd>{date(portal.rfq.response_deadline, true)}</dd></div>
            <div><dt>{tr('Invitation status')}</dt><dd><span className={styles.chip} data-state={portal.invitation_status}>{tr(portal.invitation_status)}</span></dd></div>
            <div><dt>{tr('Quotation status')}</dt><dd><span className={styles.chip} data-state={quoteStatus || 'none'}>{quoteStatus ? tr(quoteStatus) : tr('Not started')}</span></dd></div>
          </dl>
          <div className={styles.actions}>
            {portal.can_accept && <Button disabled={!!busy} onClick={async () => { const next = await act<Portal>('accept', 'supplier-portal/me/accept'); if (next) { load(next); setNotice({ kind: 'success', text: tr('Invitation accepted. Prepare your quotation below.') }); } }}><IconCheck size={16} aria-hidden />{tr('Accept RFQ')}</Button>}
            {portal.can_decline && !submitted && <Button variant="outline" disabled={!!busy} onClick={() => setDeclining(true)}>{tr('Decline RFQ')}</Button>}
            <Button variant="outline" asChild><a href={download(`rfq.pdf?lang=${arabic ? 'ar' : 'en'}`)}><IconDownload size={16} aria-hidden />{tr('Download RFQ')}</a></Button>
            {portal.can_download_quotation && <Button variant="outline" asChild><a href={download(`quotation.pdf?lang=${arabic ? 'ar' : 'en'}`)}><IconFileText size={16} aria-hidden />{tr('Download submitted quotation')}</a></Button>}
          </div>
          {declining && <div className={styles.confirmBox} role="dialog" aria-label={tr('Decline this RFQ?')}>
            <strong>{tr('Decline this RFQ?')}</strong>
            <label className="field">{tr('Reason (optional)')}<textarea value={declineReason} maxLength={500} onChange={e => setDeclineReason(e.target.value)} /></label>
            <div className={styles.actions}>
              <Button variant="destructive" disabled={!!busy} onClick={async () => { const done = await act<{ status: string }>('decline', 'supplier-portal/me/decline', { reason: declineReason }); if (done) setDeclined(true); }}>{tr('Confirm decline')}</Button>
              <Button variant="ghost" onClick={() => setDeclining(false)}>{tr('Cancel')}</Button>
            </div>
          </div>}
        </section>

        {notice && <p className={styles.notice} data-kind={notice.kind} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</p>}
        {!portal.rfq_open && <p className={styles.notice} data-kind="warning" role="status">{tr('This RFQ is closed or past its deadline. You can still view and download documents.')}</p>}

        {submitted && portal.quotation && <section className={styles.panel} aria-labelledby="submitted-heading">
          <h2 id="submitted-heading"><IconCheck size={18} aria-hidden /> {tr('Your quotation was submitted')}</h2>
          <dl className={styles.totals}>
            <div><dt>{tr('Quotation status')}</dt><dd>{tr(portal.quotation.status)}</dd></div>
            <div><dt>{tr('Submitted')}</dt><dd>{date(portal.quotation.submitted_at, true)}</dd></div>
            <div><dt>{tr('Subtotal')}</dt><dd>{portal.quotation.totals.currency} {portal.quotation.totals.subtotal}</dd></div>
            <div><dt>{tr('VAT')}</dt><dd>{portal.quotation.totals.currency} {portal.quotation.totals.vat}</dd></div>
            <div><dt>{tr('Total')}</dt><dd><strong>{portal.quotation.totals.currency} {portal.quotation.totals.total}</strong></dd></div>
          </dl>
          {portal.quotation.submission_source === 'automatic' && <p className={styles.muted}>{tr('Submitted automatically from your authorized price book.')}</p>}
        </section>}

        <form className={styles.panel} onSubmit={submit} aria-labelledby="materials-heading">
          <div className={styles.panelHead}><h2 id="materials-heading">{canEdit && !submitted ? tr('Prepare quotation') : tr('Matched materials')}</h2>
            {canEdit && !submitted && <p className={styles.muted}>{tr('Leave the price blank to exclude this item (partial quotation).')}</p>}</div>
          <fieldset disabled={!canEdit || submitted || !!busy} className={styles.items}>
            {quotable.map((item, index) => {
              const row = draft[item.id] || { price: '', brand: '', model: '', lead: '7', discount: '', note: '', specs: '' };
              const line = portal.lines.find(l => l.rfq_item_id === item.id);
              const set = (key: keyof typeof row, value: string) => setDraft(previous => ({ ...previous, [item.id]: { ...row, [key]: value } }));
              const lineTotal = Number(row.price) > 0 ? Number(item.quantity) * Number(row.price) * (1 - Number(row.discount || 0) / 100) : null;
              return <article key={item.id} className={styles.item}>
                <header><span className={styles.index}>{index + 1}</span><div><h3>{item.description}</h3>
                  <p className={styles.taxonomy}>{[item.discipline, item.main_category, item.subcategory].filter(Boolean).join(' › ') || tr('Not specified')}</p></div>
                  <strong className={styles.qty}>{Number(item.quantity).toLocaleString('en-US')} {item.unit}</strong></header>
                {item.specifications && <p className={styles.specs}><b>{tr('Specifications')}:</b> {item.specifications}</p>}
                {item.required_brand && <p className={styles.brandReq}><b>{tr('Required brand')}:</b> {item.required_brand}{item.brand_mandatory ? ` (${tr('mandatory')})` : ''}</p>}
                {line?.prefilled_from_price_book && <p className={styles.prefill}>{tr('From your saved price book — review before submitting')}</p>}
                <div className={styles.lineGrid}>
                  <label className="field">{tr('Unit price (excl. VAT)')} ({currency})<input inputMode="decimal" type="number" min="0.01" step="0.01" value={row.price} onChange={e => set('price', e.target.value)} placeholder={tr('Not quoted')} /></label>
                  <label className="field">{tr('Discount %')}<input type="number" min="0" max="100" step="0.01" value={row.discount} onChange={e => set('discount', e.target.value)} /></label>
                  <label className="field">{tr('Brand offered')}<input maxLength={150} value={row.brand} onChange={e => set('brand', e.target.value)} /></label>
                  <label className="field">{tr('Model')}<input maxLength={150} value={row.model} onChange={e => set('model', e.target.value)} /></label>
                  <label className="field">{tr('Lead time (days)')}<input type="number" min="0" max="3650" value={row.lead} onChange={e => set('lead', e.target.value)} /></label>
                  <label className="field">{tr('Notes')}<input maxLength={4000} value={row.note} onChange={e => set('note', e.target.value)} /></label>
                </div>
                <p className={styles.lineTotal}>{tr('Line total')}: <b>{lineTotal === null ? tr('Not quoted') : money(lineTotal, currency)}</b></p>
              </article>;
            })}
          </fieldset>
          {!!others.length && <details className={styles.others}><summary>{tr('Other RFQ lines')} ({others.length})</summary>
            <ul>{others.map(item => <li key={item.id}>{item.description} — {Number(item.quantity).toLocaleString('en-US')} {item.unit}</li>)}</ul></details>}

          {canEdit && !submitted && <>
            <h2 className={styles.subhead}>{tr('Commercial terms')}</h2>
            <div className={styles.lineGrid}>
              <label className="field">{tr('VAT rate %')}<input type="number" min="0" max="100" step="0.01" value={terms.vat} onChange={e => setTerms({ ...terms, vat: e.target.value })} /></label>
              <label className="field">{tr('Quotation valid until')}<input type="date" value={terms.validity} onChange={e => setTerms({ ...terms, validity: e.target.value })} /></label>
              <label className="field">{tr('Your quotation reference (optional)')}<input maxLength={100} value={terms.reference} onChange={e => setTerms({ ...terms, reference: e.target.value })} /></label>
              <label className="field">{tr('Delivery terms')}<textarea maxLength={4000} value={terms.delivery} onChange={e => setTerms({ ...terms, delivery: e.target.value })} /></label>
              <label className="field">{tr('Payment terms')}<textarea maxLength={4000} value={terms.payment} onChange={e => setTerms({ ...terms, payment: e.target.value })} /></label>
              <label className="field">{tr('Terms and conditions')}<textarea maxLength={10000} value={terms.conditions} onChange={e => setTerms({ ...terms, conditions: e.target.value })} /></label>
            </div>
            <dl className={styles.totals} aria-live="polite">
              <div><dt>{tr('Subtotal')}</dt><dd>{money(estimate.subtotal, currency)}</dd></div>
              <div><dt>{tr('VAT')}</dt><dd>{money(estimate.vat, currency)}</dd></div>
              <div><dt>{tr('Total')}</dt><dd><strong>{money(estimate.total, currency)}</strong></dd></div>
            </dl>
            <p className={styles.muted}>{tr('Estimated — final totals are calculated by Procumaterial when you save.')}</p>
            <div className={styles.actions}>
              <Button type="button" variant="outline" disabled={!!busy} onClick={() => void saveDraft()}>{busy === 'draft' ? tr('Saving…') : tr('Save quotation draft')}</Button>
            </div>

            <section className={styles.verify} aria-labelledby="verify-heading">
              <h2 id="verify-heading" className={styles.subhead}>{tr('Verify before submitting')}</h2>
              {portal.verification.verified ? <p className={styles.verified}><IconCheck size={16} aria-hidden /> {tr('Verified')}</p> : <>
                <p className={styles.muted}>{tr('A binding quotation needs a one-time code sent to the email or phone this invitation was delivered to.')}</p>
                <div className={styles.codeRow}>
                  <Button type="button" variant="quiet" disabled={!!busy} onClick={() => void requestCode()}>{tr('Send verification code')}</Button>
                  {codeSentTo && <span className={styles.muted}>{tr('Code sent to')} <b dir="ltr">{codeSentTo}</b></span>}
                </div>
                {codeSentTo && <div className={styles.codeRow}>
                  <label className="field">{tr('Enter the 6-digit code')}<input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={e => setCode(e.target.value.replace(/\D/g, ''))} dir="ltr" /></label>
                  <Button type="button" disabled={code.length !== 6 || !!busy} onClick={() => void confirmCode()}>{tr('Verify')}</Button>
                </div>}
              </>}
            </section>

            <label className={styles.confirm}><input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} />
              <span>{tr('I am authorized by')} <b>{portal.supplier.name}</b> {tr('to submit these prices and terms to the buyer. This quotation is binding.')}</span></label>
            <div className={styles.actions}>
              <Button type="submit" disabled={!!busy || !confirm || !portal.verification.verified}>{busy === 'submit' ? tr('Submitting…') : tr('Submit quotation')}</Button>
            </div>
          </>}
        </form>

        <section className={styles.panel} aria-labelledby="attachments-heading">
          <h2 id="attachments-heading">{tr('Attachments')}</h2>
          {portal.attachments.length ? <ul className={styles.files}>{portal.attachments.map(file => <li key={file.id}>
            <a href={download(`attachments/${encodeURIComponent(file.id)}`)} rel="noopener"><IconFileText size={16} aria-hidden />{file.filename}</a>
            {file.size ? <small>{Math.max(1, Math.round(file.size / 1024))} KB</small> : null}</li>)}</ul>
            : <p className={styles.muted}>{tr('No attachments for this RFQ.')}</p>}
        </section>

        <footer className={styles.footer}>
          <span>{tr('Your secure session expires')}: {date(portal.session_expires_at, true)}</span>
          <button type="button" onClick={async () => { await act('logout', 'supplier-portal/me/logout'); setFailure({ code: 'session_required', message: 'Open the secure link from your invitation email or SMS.' }); }}>{tr('Sign out')}</button>
        </footer>
      </div>
    </main>
  );
}
