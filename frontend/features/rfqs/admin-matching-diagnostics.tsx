"use client";

import { useState } from 'react';
import { api } from '@/lib/api/client';
import { useApi } from '@/lib/api/use-api';
import { useLocale } from '@/lib/locale';
import { Button } from '@/components/ui/button';
import { State } from '@/features/shared/ui';
import './grouped-rfq-materials.css';

type DiagnosticResponse = {
  rfq_id: string;
  rfq_items: number;
  classified_items?: number;
  unclassified_items?: number;
  active_suppliers_checked: number;
  category_candidate_organizations: number;
  approved_matching_capabilities: number;
  exact_matches?: number;
  category_matches?: number;
  ai_assisted_candidates?: number;
  rejected_by_location: number;
  rejected_by_verification_or_eligibility: number;
  matches_created: number;
  invitations_created: number;
  delivery_summary?: Record<string, number>;
  invitation_statuses?: Record<string, number>;
  item_diagnostics?: {
    item_id: string;
    description: string;
    material_id?: string | null;
    main_category_name?: string | null;
    subcategory_name?: string | null;
    classified: boolean;
    matched_supplier_count: number;
  }[];
  eligible_suppliers?: {
    supplier_org_id: string;
    supplier_name: string;
    status: string;
    score: number;
    match_levels: string[];
    coverage: string;
    invitation_status?: string | null;
    quotation_status?: string | null;
    ai_assisted: boolean;
    reasons: string[];
  }[];
  excluded_suppliers?: {
    supplier_org_id: string;
    supplier_name: string;
    status: string;
    reasons: string[];
  }[];
  supplier_reasons: {
    supplier_org_id: string;
    supplier_name: string;
    status: string;
    reasons: string[];
  }[];
  funnel?: Record<string, number>;
  channel_delivery?: Record<string, Record<string, number>>;
  reminders?: { requests?: number; suppliers_reminded?: number; recent?: { created_at: string | null; status: string; suppliers: number | null }[] };
  failed_job_reasons?: string[];
  matching_duration_ms?: number | null;
};

// Candidate funnel, in pipeline order, computed by the backend from persisted results.
const FUNNEL: [string, string, string][] = [
  ['initial_candidates', 'Initial candidates', 'المرشحون الأوليون'],
  ['discipline_qualified', 'Discipline qualified', 'مؤهلون حسب التخصص'],
  ['main_category_qualified', 'Main category qualified', 'مؤهلون حسب الفئة الرئيسية'],
  ['subcategory_qualified', 'Subcategory qualified', 'مؤهلون حسب الفئة الفرعية'],
  ['exact_product_matches', 'Exact product matches', 'مطابقات منتج دقيقة'],
  ['fuzzy_product_matches', 'Fuzzy product matches', 'مطابقات منتج تقريبية'],
  ['category_capability_fallback_matches', 'Category fallback (no product named)', 'مطابقة الفئة (دون منتج محدد)'],
  ['insufficient_product_evidence', 'Insufficient product evidence', 'دليل منتج غير كافٍ'],
  ['location_exclusions', 'Location exclusions', 'استبعادات الموقع'],
  ['brand_exclusions', 'Brand exclusions', 'استبعادات العلامة التجارية'],
  ['inactive_or_rejected', 'Inactive, rejected or pending', 'غير نشط أو مرفوض أو معلق'],
  ['final_eligible_suppliers', 'Final eligible suppliers', 'الموردون المؤهلون النهائيون'],
  ['invitations_active', 'Invitations created', 'الدعوات المنشأة'],
  ['invitations_cancelled', 'Invitations superseded', 'الدعوات الملغاة'],
  ['automatic_quotations', 'Automatic quotations', 'عروض الأسعار التلقائية'],
  ['quotations_submitted', 'Quotations submitted', 'عروض الأسعار المقدمة'],
];

export function AdminMatchingDiagnostics({
  rfqId,
  onRerunDone,
}: {
  rfqId: string;
  onRerunDone?: () => void;
}) {
  const { t, arabic } = useLocale();
  const w = (en: string, ar: string) => (arabic ? ar : t(en));
  const diag = useApi<DiagnosticResponse>(`matching/admin/rfqs/${rfqId}/diagnostics`);
  const [rerunning, setRerunning] = useState(false);
  const [rerunMessage, setRerunMessage] = useState('');

  async function handleRerun() {
    setRerunning(true);
    setRerunMessage('');
    try {
      const res = await api<{ job_status: string; deduplicated: boolean }>(
        `matching/admin/rfqs/${rfqId}/rerun`,
        { method: 'POST' },
      );
      setRerunMessage(
        res.deduplicated
          ? w('Matching job is already queued.', 'مهمة المطابقة موجودة بالفعل في قائمة الانتظار.')
          : w('Matching re-run queued.', 'تمت إعادة جدولة مطابقة الموردين.'),
      );
      diag.reload();
      onRerunDone?.();
    } catch (err) {
      setRerunMessage(err instanceof Error ? err.message : w('Could not queue re-run', 'تعذرت إعادة التشغيل'));
    } finally {
      setRerunning(false);
    }
  }

  const d = diag.data;
  const deliveryEntries = Object.entries(d?.delivery_summary || {});
  const invStatusEntries = Object.entries(d?.invitation_statuses || {});

  return (
    <section className="diag-panel" dir={arabic ? 'rtl' : 'ltr'} aria-label={w('Admin matching diagnostics', 'تشخيص المطابقة للإدارة')}>
      <div className="diag-header">
        <div>
          <h2 style={{ margin: 0 }}>{w('Admin matching diagnostics', 'تشخيص المطابقة للإدارة')}</h2>
          <small>
            {w(
              'Complete pipeline visibility: item classification, exact & category matches, AI candidates, exclusions, and invitation delivery.',
              'رؤية شاملة لمسار المطابقة: تصنيف المواد، المطابقات الدقيقة والفئوية، مرشحي الذكاء الاصطناعي، الاستبعادات وحالة الإرسال.',
            )}
          </small>
        </div>
        <div className="live-actions">
          <Button type="button" variant="outline" onClick={diag.reload} disabled={diag.loading}>
            {w('Refresh diagnostics', 'تحديث التشخيص')}
          </Button>
          <Button type="button" onClick={() => void handleRerun()} disabled={rerunning}>
            {rerunning ? w('Queueing...', 'جارٍ الجدولة...') : w('Re-run matching', 'إعادة تشغيل المطابقة')}
          </Button>
        </div>
      </div>

      {rerunMessage && <p className="rfq-grouped-summary" role="status">{rerunMessage}</p>}
      <State loading={diag.loading} error={diag.error} retry={diag.reload} />

      {d && (
        <>
          <div className="diag-kpis">
            <div className="diag-kpi">
              <strong>{d.rfq_items}</strong>
              <span>{w('RFQ items processed', 'المواد المعالجة')}</span>
            </div>
            <div className="diag-kpi">
              <strong>
                {d.classified_items ?? d.rfq_items} / {d.unclassified_items ?? 0}
              </strong>
              <span>{w('Classified / Unclassified', 'مصنفة / غير مصنفة')}</span>
            </div>
            <div className="diag-kpi">
              <strong>{d.exact_matches ?? 0}</strong>
              <span>{w('Exact matches', 'مطابقات دقيقة')}</span>
            </div>
            <div className="diag-kpi">
              <strong>{d.category_matches ?? 0}</strong>
              <span>{w('Category / keyword matches', 'مطابقات الفئة والكلمات')}</span>
            </div>
            <div className="diag-kpi">
              <strong>{d.ai_assisted_candidates ?? 0}</strong>
              <span>{w('AI-assisted candidates', 'مرشحو الذكاء الاصطناعي')}</span>
            </div>
            <div className="diag-kpi">
              <strong>{d.matches_created}</strong>
              <span>{w('Final eligible suppliers', 'الموردون المؤهلون')}</span>
            </div>
            <div className="diag-kpi">
              <strong>{d.invitations_created}</strong>
              <span>{w('Invitations created', 'الدعوات المنشأة')}</span>
            </div>
            <div className="diag-kpi">
              <strong>{d.rejected_by_location + d.rejected_by_verification_or_eligibility}</strong>
              <span>{w('Excluded suppliers', 'الموردون المستبعدون')}</span>
            </div>
          </div>

          {d.funnel && (
            <div>
              <h3>{w('Candidate filtering', 'تصفية المرشحين')}{d.matching_duration_ms != null ? ` · ${Math.round(d.matching_duration_ms)} ms` : ''}</h3>
              <div className="diag-kpis">
                {FUNNEL.filter(([key]) => d.funnel?.[key] != null).map(([key, en, ar]) => (
                  <div className="diag-kpi" key={key}>
                    <strong>{d.funnel?.[key]}</strong>
                    <span>{w(en, ar)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {(Object.keys(d.channel_delivery || {}).length > 0 || !!d.reminders?.requests || !!d.failed_job_reasons?.length) && (
            <div>
              <h3>{w('Email, SMS and reminders', 'البريد والرسائل والتذكيرات')}</h3>
              <div className="rfq-grouped-controls">
                {Object.entries(d.channel_delivery || {}).flatMap(([channel, statuses]) =>
                  Object.entries(statuses).map(([status, count]) => (
                    <span key={`${channel}-${status}`} className="diag-badge">{channel.toUpperCase()} {status}: {count}</span>
                  )))}
                <span className="diag-badge">{w('Reminder requests', 'طلبات التذكير')}: {d.reminders?.requests ?? 0}</span>
                <span className="diag-badge">{w('Suppliers reminded', 'الموردون المذكَّرون')}: {d.reminders?.suppliers_reminded ?? 0}</span>
                {(d.failed_job_reasons || []).map(reason => (
                  <span key={reason} className="diag-badge" data-tone="warn">{reason}</span>
                ))}
              </div>
            </div>
          )}

          {(invStatusEntries.length > 0 || deliveryEntries.length > 0) && (
            <div>
              <h3>{w('Invitations & delivery status', 'الدعوات وحالة التسليم')}</h3>
              <div className="rfq-grouped-controls">
                {invStatusEntries.map(([status, count]) => (
                  <span key={`inv-${status}`} className="diag-badge" data-tone="ok">
                    {status}: {count}
                  </span>
                ))}
                {deliveryEntries.map(([status, count]) => (
                  <span key={`del-${status}`} className="diag-badge">
                    {w('Delivery', 'التسليم')} ({status}): {count}
                  </span>
                ))}
              </div>
            </div>
          )}

          {!!d.item_diagnostics?.length && (
            <div>
              <h3>{w('RFQ items classification & coverage', 'تصنيف وتغطية مواد الطلب')}</h3>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{w('Description', 'الوصف')}</th>
                      <th>{w('Main category', 'الفئة الرئيسية')}</th>
                      <th>{w('Subcategory', 'الفئة الفرعية')}</th>
                      <th>{w('Classification', 'التصنيف')}</th>
                      <th>{w('Matched suppliers', 'الموردون المطابقون')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.item_diagnostics.map((item) => (
                      <tr key={item.item_id}>
                        <td>{item.description}</td>
                        <td>{item.main_category_name || '—'}</td>
                        <td>{item.subcategory_name || '—'}</td>
                        <td>
                          <span className="diag-badge" data-tone={item.classified ? 'ok' : 'warn'}>
                            {item.classified ? w('Classified', 'مصنف') : w('Unclassified', 'غير مصنف')}
                          </span>
                        </td>
                        <td>{item.matched_supplier_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {!!d.eligible_suppliers?.length && (
            <div>
              <h3>{w('Final eligible suppliers', 'الموردون المؤهلون النهائيون')}</h3>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{w('Supplier', 'المورد')}</th>
                      <th>{w('Score', 'النقاط')}</th>
                      <th>{w('Match levels', 'مستويات المطابقة')}</th>
                      <th>{w('Coverage', 'التغطية')}</th>
                      <th>{w('Invitation / Quotation', 'الدعوة / العرض')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.eligible_suppliers.map((sup) => (
                      <tr key={sup.supplier_org_id}>
                        <td>
                          <strong>{sup.supplier_name}</strong>
                          {sup.ai_assisted && (
                            <>
                              {' '}
                              <span className="diag-badge" data-tone="ai">
                                AI
                              </span>
                            </>
                          )}
                        </td>
                        <td>{sup.score}</td>
                        <td>{sup.match_levels.join(', ') || '—'}</td>
                        <td>{sup.coverage}</td>
                        <td>
                          {[sup.invitation_status, sup.quotation_status].filter(Boolean).join(' · ') || '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {!!(d.excluded_suppliers?.length || d.supplier_reasons?.length) && (
            <details open={!d.matches_created}>
              <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
                {w('Excluded suppliers and reasons', 'الموردون المستبعدون وأسباب الاستبعاد')} (
                {(d.excluded_suppliers || []).length})
              </summary>
              <div className="table-wrap" style={{ marginTop: '0.5rem' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>{w('Supplier', 'المورد')}</th>
                      <th>{w('Status', 'الحالة')}</th>
                      <th>{w('Reason', 'السبب')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(d.excluded_suppliers || []).map((sup) => (
                      <tr key={sup.supplier_org_id}>
                        <td>{sup.supplier_name}</td>
                        <td>
                          <span className="diag-badge" data-tone={sup.status === 'location_mismatch' ? 'warn' : 'bad'}>
                            {sup.status}
                          </span>
                        </td>
                        <td>{sup.reasons.join(' ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}
