"use client";
import { useEffect, useRef, useState } from 'react';
import { IconCheck, IconFilter, IconRefresh, IconSparkles, IconWand, IconX } from '@tabler/icons-react';
import { api, json } from '@/lib/api/client';
import { useApi } from '@/lib/api/use-api';
import { useLocale } from '@/lib/locale';
import { Button } from '@/components/ui/button';
import { AdminModal } from '@/features/shared/admin-modal';
import { adminConfirm } from '@/features/shared/admin-confirm';
import { useToast } from '@/features/shared/toast';

type ReviewMaterialPreview = {
  id: string;
  code: string;
  name_en: string;
  current_discipline: string | null;
  inferred_discipline: string | null;
  safe: boolean;
  evidence: string;
  issue: string;
};

type CategoryReviewItem = {
  id: string;
  code: string;
  name_en: string;
  name_ar: string;
  parent_id: string | null;
  parent_name_en: string | null;
  hierarchy_path?: string[];
  issue_type: string;
  issue_label: string;
  reason: string;
  secondary_issues: string[];
  active_material_count: number;
  inactive_material_count: number;
  missing_discipline_count: number;
  unsupported_discipline_count: number;
  duplicate_ids: string[];
  duplicate_canonical_id: string | null;
  duplicate_safe: boolean;
  proposed_action: string;
  proposed_discipline: string | null;
  safe_to_auto_fix: boolean;
  can_approve: boolean;
  can_retain_empty: boolean;
  can_merge: boolean;
  can_archive: boolean;
  requires_manual_review: boolean;
  evidence: string[];
  current_decision: string | null;
  materials_preview: ReviewMaterialPreview[];
};

type CategoryReviewAnalysis = {
  total_pending: number;
  total_filtered: number;
  total_safe_auto_fix: number;
  total_valid_approvable: number;
  total_conflicts: number;
  counts_by_issue_type: Record<string, number>;
  counts_by_action: Record<string, number>;
  preview_token: string;
  batch_size: number;
  job_size: number;
  limit: number;
  offset: number;
  items: CategoryReviewItem[];
};

type PreviewItem = {
  category_id: string;
  code: string;
  name_en: string;
  parent_name_en: string | null;
  issue_type: string;
  proposed_action: string;
  proposed_discipline: string | null;
  materials_to_update: number;
  duplicate_count: number;
  reason: string;
};

type BulkFixResponse = {
  dry_run: boolean;
  job_size: number;
  batch_size: number;
  batches_executed: number;
  categories_processed: number;
  categories_resolved: number;
  materials_updated: number;
  duplicates_merged: number;
  parent_links_repaired: number;
  skipped_ambiguous: number;
  remaining_safe: number;
  has_more: boolean;
  next_cursor: string | null;
  preview_items: PreviewItem[];
  errors: Array<Record<string, unknown>>;
};

type BulkApproveResponse = {
  dry_run: boolean;
  job_size: number;
  batch_size: number;
  batches_executed: number;
  total_eligible: number;
  retained_empty: number;
  approved: number;
  skipped_already_approved: number;
  remaining_valid: number;
  excluded_count: number;
  excluded_by_issue_type: Record<string, number>;
  has_more: boolean;
  next_cursor: string | null;
  preview_items: PreviewItem[];
  excluded_preview_items: PreviewItem[];
  errors: Array<Record<string, unknown>>;
};

const ISSUE_LABELS: Record<string, string> = {
  missing_parent: 'Missing parent',
  archived_parent: 'Archived parent',
  hierarchy_depth_exceeded: 'Exceeds two-level hierarchy',
  missing_discipline: 'Missing discipline',
  unsupported_discipline: 'Unsupported discipline',
  empty_category: 'Empty category',
  inactive_materials_only: 'Inactive materials only',
  duplicate_category: 'Duplicate category',
  parent_mismatch: 'Parent mismatch',
  ambiguous_classification: 'Ambiguous classification',
  invalid_import_data: 'Invalid import data',
};

const DISCIPLINES = ['Civil', 'Electrical', 'Mechanical'] as const;

export function CategoryReviewQueue({ onChanged }: { onChanged: () => void }) {
  const { t } = useLocale();
  const toast = useToast();

  const [offset, setOffset] = useState(0);
  const [issueFilter, setIssueFilter] = useState('');
  const [conflictsOnly, setConflictsOnly] = useState(false);
  const [search, setSearch] = useState('');
  const [analyzed, setAnalyzed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progressNote, setProgressNote] = useState('');
  const [error, setError] = useState('');

  // Confirmation preview modal state
  const [previewKind, setPreviewKind] = useState<'auto-fix' | 'approve-valid' | null>(null);
  const [fixPreview, setFixPreview] = useState<BulkFixResponse | null>(null);
  const [approvePreview, setApprovePreview] = useState<BulkApproveResponse | null>(null);
  const previewDialog = useRef<HTMLDialogElement>(null);

  // Reclassify modal state
  const [reclassifyItem, setReclassifyItem] = useState<CategoryReviewItem | null>(null);
  const [reclassifyDiscipline, setReclassifyDiscipline] = useState<string>('');
  const [materialDisciplines, setMaterialDisciplines] = useState<Record<string, string>>({});
  const [decisionNotes, setDecisionNotes] = useState('');
  const reclassifyDialog = useRef<HTMLDialogElement>(null);

  const queryParams = new URLSearchParams({
    limit: '100',
    offset: String(offset),
    ...(issueFilter ? { issue_type: issueFilter } : {}),
    ...(conflictsOnly ? { conflicts_only: 'true' } : {}),
    ...(search.trim() ? { q: search.trim() } : {}),
  });

  const analysis = useApi<CategoryReviewAnalysis>(`taxonomy/review/analyze?${queryParams.toString()}`);

  useEffect(() => {
    if (previewKind && !previewDialog.current?.open) previewDialog.current?.showModal();
  }, [previewKind]);

  useEffect(() => {
    if (reclassifyItem && !reclassifyDialog.current?.open) reclassifyDialog.current?.showModal();
  }, [reclassifyItem]);

  function refreshAll() {
    setError('');
    analysis.reload();
    onChanged();
  }

  async function handleAnalyzeAll() {
    setBusy(true);
    setError('');
    setIssueFilter('');
    setConflictsOnly(false);
    setOffset(0);
    try {
      analysis.reload();
      setAnalyzed(true);
      toast.success(t('Analysis updated'), t('Pending categories classified by issue type and safe actions.'));
    } finally {
      setBusy(false);
    }
  }

  async function openAutoFixPreview() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const preview = await api<BulkFixResponse>('taxonomy/review/auto-fix', {
        method: 'POST',
        body: json({ dry_run: true, job_limit: 300, batch_size: 30 }),
      });
      setFixPreview(preview);
      setApprovePreview(null);
      setPreviewKind('auto-fix');
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : t('Could not preview safe fixes');
      setError(msg);
      toast.error(t('Preview failed'), msg);
    } finally {
      setBusy(false);
    }
  }

  async function openApproveValidPreview() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const preview = await api<BulkApproveResponse>('taxonomy/review/approve-valid', {
        method: 'POST',
        body: json({ dry_run: true, include_empty: true, job_limit: 300, batch_size: 30 }),
      });
      setApprovePreview(preview);
      setFixPreview(null);
      setPreviewKind('approve-valid');
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : t('Could not preview valid categories');
      setError(msg);
      toast.error(t('Preview failed'), msg);
    } finally {
      setBusy(false);
    }
  }

  async function confirmBulkAction() {
    if (busy || !previewKind) return;
    setBusy(true);
    setError('');
    try {
      if (previewKind === 'auto-fix') {
        let cursor: string | null = null;
        let totalResolved = 0;
        let totalMaterials = 0;
        let totalMerged = 0;
        let totalParentRepaired = 0;
        let totalBatches = 0;
        let jobsRun = 0;
        while (jobsRun < 20) {
          jobsRun += 1;
          setProgressNote(t(`Applying safe fixes (job ${jobsRun}, 30 records per batch)...`));
          const res: BulkFixResponse = await api<BulkFixResponse>('taxonomy/review/auto-fix', {
            method: 'POST',
            body: json({ dry_run: false, cursor_after_id: cursor, job_limit: 300, batch_size: 30 }),
          });
          totalResolved += res.categories_resolved;
          totalMaterials += res.materials_updated;
          totalMerged += res.duplicates_merged;
          totalParentRepaired += res.parent_links_repaired;
          totalBatches += res.batches_executed;
          if (!res.has_more || !res.next_cursor || res.categories_processed === 0) break;
          cursor = res.next_cursor;
        }
        setPreviewKind(null);
        setFixPreview(null);
        toast.success(
          t('Safe fixes applied'),
          t(`${totalResolved} categories resolved, ${totalMaterials} materials updated, ${totalMerged} duplicates merged, ${totalParentRepaired} parent links repaired (${totalBatches} batches).`),
        );
      } else {
        let cursor: string | null = null;
        let totalRetained = 0;
        let totalSkipped = 0;
        let totalBatches = 0;
        let jobsRun = 0;
        while (jobsRun < 20) {
          jobsRun += 1;
          setProgressNote(t(`Approving valid categories (job ${jobsRun}, 30 per batch)...`));
          const res: BulkApproveResponse = await api<BulkApproveResponse>('taxonomy/review/approve-valid', {
            method: 'POST',
            body: json({ dry_run: false, include_empty: true, cursor_after_id: cursor, job_limit: 300, batch_size: 30 }),
          });
          totalRetained += res.retained_empty;
          totalSkipped += res.skipped_already_approved || 0;
          totalBatches += res.batches_executed || 0;
          if (!res.has_more || !res.next_cursor || (res.retained_empty === 0 && (res.skipped_already_approved || 0) === 0)) break;
          cursor = res.next_cursor;
        }
        setPreviewKind(null);
        setApprovePreview(null);
        toast.success(
          t('Valid categories updated'),
          totalSkipped > 0
            ? t(`${totalRetained} valid empty categories retained (${totalSkipped} already approved skipped, ${totalBatches} batch(es)).`)
            : t(`${totalRetained} valid empty categories retained in ${totalBatches} batch(es) (hidden from buyer selection until active materials exist).`),
        );
      }
      refreshAll();
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : t('Bulk action could not finish');
      setError(msg);
      toast.error(t('Bulk action failed'), msg);
    } finally {
      setProgressNote('');
      setBusy(false);
    }
  }

  async function submitDecision(
    item: CategoryReviewItem,
    decision: 'approve' | 'retain_empty' | 'merge_duplicate' | 'archive',
  ) {
    if (busy) return;
    if (decision === 'archive') {
      const ok = await adminConfirm(t('Archive this unused category? It can be restored later without changing IDs.'));
      if (!ok) return;
    }
    if (decision === 'merge_duplicate') {
      const ok = await adminConfirm(t('Merge this duplicate group into the canonical category and archive aliases? All Material IDs, capabilities, and RFQ references are preserved.'));
      if (!ok) return;
    }
    setBusy(true);
    setError('');
    try {
      await api(`taxonomy/review/categories/${item.id}/decision`, {
        method: 'POST',
        body: json({ decision }),
      });
      toast.success(t('Decision saved'), `${item.name_en}: ${decision.replace('_', ' ')}`);
      refreshAll();
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : t('Could not record decision');
      setError(msg);
      toast.error(t('Action failed'), msg);
    } finally {
      setBusy(false);
    }
  }

  function openReclassify(item: CategoryReviewItem) {
    setReclassifyItem(item);
    setReclassifyDiscipline(item.proposed_discipline || '');
    const initial: Record<string, string> = {};
    for (const mat of item.materials_preview) {
      if (mat.inferred_discipline) initial[mat.id] = mat.inferred_discipline;
    }
    setMaterialDisciplines(initial);
    setDecisionNotes('');
  }

  async function submitReclassify(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !reclassifyItem) return;
    const perMaterial = Object.fromEntries(
      Object.entries(materialDisciplines).filter(([, value]) => Boolean(value)),
    );
    if (!reclassifyDiscipline && Object.keys(perMaterial).length === 0) {
      setError(t('Select a supported discipline (Civil, Electrical, or Mechanical) for the category or individual materials.'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      await api(`taxonomy/review/categories/${reclassifyItem.id}/decision`, {
        method: 'POST',
        body: json({
          decision: 'reclassify',
          discipline: reclassifyDiscipline || null,
          material_disciplines: Object.keys(perMaterial).length ? perMaterial : null,
          notes: decisionNotes.trim() || null,
        }),
      });
      toast.success(t('Category reclassified'), reclassifyItem.name_en);
      setReclassifyItem(null);
      refreshAll();
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : t('Could not reclassify category');
      setError(msg);
      toast.error(t('Reclassify failed'), msg);
    } finally {
      setBusy(false);
    }
  }

  const data = analysis.data;
  if (analysis.loading && !data) return null;
  if (analysis.error && !data) return null;
  if (!data || (data.total_pending === 0 && !issueFilter && !conflictsOnly && !search)) return null;

  return (
    <details className="panel industry-taxonomy-review" open={analyzed || conflictsOnly || Boolean(issueFilter)}>
      <summary>
        {t('Category review needed')} <span>{data.total_pending}</span>
      </summary>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '0.75rem' }}>
        <p style={{ margin: 0 }}>
          {t('Categories are grouped by specific review reason. Safe discipline fixes, parent link repairs, and duplicate merges run in short batches of 30 (up to 300 per job) while preserving Material IDs. Ambiguous records remain pending for manual review.')}
        </p>

        {/* Action Toolbar */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center' }}>
          <Button type="button" variant="outline" disabled={busy} onClick={() => void handleAnalyzeAll()}>
            <IconSparkles size={16} /> {t('Analyze All')}
          </Button>
          <Button
            type="button"
            disabled={busy || data.total_safe_auto_fix === 0}
            onClick={() => void openAutoFixPreview()}
          >
            <IconWand size={16} /> {t('Auto-Fix Safe Records')} ({data.total_safe_auto_fix})
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={busy || data.total_valid_approvable === 0}
            onClick={() => void openApproveValidPreview()}
          >
            <IconCheck size={16} /> {t('Approve All Valid')} ({data.total_valid_approvable})
          </Button>
          <Button
            type="button"
            variant={conflictsOnly ? 'default' : 'outline'}
            disabled={busy}
            onClick={() => {
              setConflictsOnly(v => !v);
              setIssueFilter('');
              setOffset(0);
            }}
          >
            <IconFilter size={16} /> {t('Review Conflicts')} ({data.total_conflicts})
          </Button>
          <Button type="button" variant="ghost" disabled={busy} onClick={refreshAll}>
            <IconRefresh size={16} /> {t('Refresh')}
          </Button>
        </div>

        {/* Issue Type Breakdown Pills */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', alignItems: 'center' }}>
          <Button
            type="button"
            size="sm"
            variant={!issueFilter && !conflictsOnly ? 'default' : 'outline'}
            onClick={() => {
              setIssueFilter('');
              setConflictsOnly(false);
              setOffset(0);
            }}
          >
            {t('All')} ({data.total_pending})
          </Button>
          {Object.entries(data.counts_by_issue_type).map(([key, count]) => (
            <Button
              key={key}
              type="button"
              size="sm"
              variant={issueFilter === key ? 'default' : 'outline'}
              onClick={() => {
                setIssueFilter(current => (current === key ? '' : key));
                setConflictsOnly(false);
                setOffset(0);
              }}
            >
              {t(ISSUE_LABELS[key] || key)} ({count})
            </Button>
          ))}
          <input
            type="search"
            value={search}
            onChange={e => {
              setSearch(e.target.value);
              setOffset(0);
            }}
            placeholder={t('Filter review queue...')}
            style={{ marginInlineStart: 'auto', minWidth: '180px' }}
          />
        </div>

        {progressNote && <p role="status" className="notice">{progressNote}</p>}
        {error && <p role="alert" className="category-editor-error">{error}</p>}

        {/* Review Table */}
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('Category')}</th>
                <th>{t('Parent')}</th>
                <th>{t('Issue')}</th>
                <th>{t('Materials')}</th>
                <th>{t('Review reason')}</th>
                <th>{t('Actions')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map(item => (
                <tr key={item.id}>
                  <td>
                    <strong>{item.name_en}</strong>
                    <br />
                    <small>{item.code}</small>
                  </td>
                  <td>
                    {item.parent_name_en || t('Top level')}
                    {item.hierarchy_path && item.hierarchy_path.length > 2 && (
                      <div>
                        <small>{item.hierarchy_path.join(' → ')}</small>
                      </div>
                    )}
                  </td>
                  <td>
                    <span className={`status ${item.safe_to_auto_fix ? 'verified' : item.requires_manual_review ? 'rejected' : 'pending'}`}>
                      {t(item.issue_label)}
                    </span>
                    {item.proposed_discipline && (
                      <div><small>{t('Proposed')}: {item.proposed_discipline}</small></div>
                    )}
                  </td>
                  <td>
                    {item.active_material_count} {t('active')}
                    {item.inactive_material_count > 0 && (
                      <div><small>{item.inactive_material_count} {t('archived')}</small></div>
                    )}
                  </td>
                  <td>
                    <div>{item.reason}</div>
                    {item.materials_preview.length > 0 && (
                      <details style={{ marginTop: '0.25rem' }}>
                        <summary style={{ cursor: 'pointer', fontSize: '0.85em' }}>
                          {t('Inspect materials')} ({item.materials_preview.length})
                        </summary>
                        <ul style={{ margin: '0.25rem 0 0', paddingInlineStart: '1.1rem', fontSize: '0.85em' }}>
                          {item.materials_preview.map(mat => (
                            <li key={mat.id}>
                              <strong>{mat.name_en}</strong> ({mat.code}) —{' '}
                              {mat.inferred_discipline
                                ? `${t('Safe')}: ${mat.inferred_discipline}`
                                : t('Manual review')}{' '}
                              <small>({mat.evidence})</small>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </td>
                  <td>
                    <div className="category-row-actions">
                      {item.can_approve && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => void submitDecision(item, 'approve')}
                        >
                          {t('Approve')}
                        </Button>
                      )}
                      {item.can_retain_empty && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => void submitDecision(item, 'retain_empty')}
                        >
                          {t('Retain empty')}
                        </Button>
                      )}
                      {item.can_merge && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => void submitDecision(item, 'merge_duplicate')}
                        >
                          {t('Merge duplicate')}
                        </Button>
                      )}
                      {(item.missing_discipline_count > 0 || item.unsupported_discipline_count > 0) && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => openReclassify(item)}
                        >
                          {t('Reclassify')}
                        </Button>
                      )}
                      {item.can_archive && (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() => void submitDecision(item, 'archive')}
                        >
                          {t('Archive')}
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {data.items.length === 0 && (
                <tr>
                  <td colSpan={6}>{t('No categories match this review filter.')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="table-pagination">
          <span>
            {t('Showing')} {data.items.length} {t('of')} {data.total_filtered}
          </span>
          {offset > 0 && (
            <Button type="button" variant="outline" onClick={() => setOffset(v => Math.max(0, v - 100))}>
              {t('Previous')}
            </Button>
          )}
          {offset + data.items.length < data.total_filtered && (
            <Button type="button" variant="outline" onClick={() => setOffset(v => v + 100)}>
              {t('Next')}
            </Button>
          )}
        </div>
      </div>

      {/* Confirmation Preview Modal for Bulk Actions */}
      {previewKind && (
        <AdminModal
          ref={previewDialog}
          className="live-dialog category-editor-dialog"
          aria-labelledby="bulk-review-preview-title"
          open={Boolean(previewKind)}
          busy={busy}
          onCancel={e => {
            e.preventDefault();
            if (!busy) setPreviewKind(null);
          }}
          onClose={() => setPreviewKind(null)}
          onRequestClose={() => {
            if (!busy) setPreviewKind(null);
          }}
        >
          <div className="category-editor-form">
            <header className="category-editor-header">
              <h2 id="bulk-review-preview-title">
                {previewKind === 'auto-fix' ? t('Confirm Auto-Fix Safe Records') : t('Confirm Approve All Valid')}
              </h2>
              <Button type="button" variant="ghost" aria-label={t('Close')} disabled={busy} onClick={() => setPreviewKind(null)}>
                <IconX size={20} />
              </Button>
            </header>

            {previewKind === 'auto-fix' && fixPreview && (
              <div>
                <p>
                  {t('Only safe, verified fixes will be applied in batches of 30 (up to 300 per job with automatic continuation). Material IDs and RFQ references are preserved. Ambiguous records remain pending.')}
                </p>
                <ul>
                  <li><strong>{t('Safe categories in job')}:</strong> {fixPreview.categories_processed}{fixPreview.has_more ? '+' : ''}</li>
                  <li><strong>{t('Materials to assign discipline')}:</strong> {fixPreview.materials_updated}</li>
                  <li><strong>{t('Safe duplicate aliases to merge')}:</strong> {fixPreview.duplicates_merged}</li>
                  <li><strong>{t('Parent links to repair')}:</strong> {fixPreview.parent_links_repaired}</li>
                  <li><strong>{t('Ambiguous records left for manual review')}:</strong> {fixPreview.skipped_ambiguous}</li>
                </ul>
                {fixPreview.preview_items.length > 0 && (
                  <div className="table-wrap" style={{ maxHeight: '240px', overflowY: 'auto' }}>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>{t('Category')}</th>
                          <th>{t('Action')}</th>
                          <th>{t('Details')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {fixPreview.preview_items.slice(0, 25).map(p => (
                          <tr key={p.category_id}>
                            <td>{p.parent_name_en ? `${p.parent_name_en} → ` : ''}{p.name_en}</td>
                            <td>{p.proposed_action}{p.proposed_discipline ? ` (${p.proposed_discipline})` : ''}</td>
                            <td>{p.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {previewKind === 'approve-valid' && approvePreview && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                <p style={{ margin: 0 }}>
                  {t('Eligible valid empty categories will be recorded with an explicit retain decision in batches of 30. Categories are not deleted, no fake materials or disciplines are created, and retained empty categories remain hidden from buyer RFQ selection until active materials exist.')}
                </p>
                <ul style={{ margin: 0 }}>
                  <li>
                    <strong>{t('Eligible valid records to approve')}:</strong>{' '}
                    {approvePreview.total_eligible} ({Math.max(1, Math.ceil(approvePreview.total_eligible / Math.max(1, approvePreview.batch_size)))}{' '}
                    {t('batch(es) of')} {approvePreview.batch_size})
                  </li>
                  <li>
                    <strong>{t('Excluded invalid or conflicting records')}:</strong>{' '}
                    {approvePreview.excluded_count} ({t('will remain pending for manual review')})
                  </li>
                </ul>

                {approvePreview.preview_items.length > 0 && (
                  <div>
                    <p style={{ margin: '0 0 0.35rem', fontWeight: 600 }}>
                      {t('Records that will change')} ({approvePreview.preview_items.length}
                      {approvePreview.total_eligible > approvePreview.preview_items.length ? ` / ${approvePreview.total_eligible}` : ''})
                    </p>
                    <div className="table-wrap" style={{ maxHeight: '200px', overflowY: 'auto' }}>
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>{t('Category')}</th>
                            <th>{t('Current status')}</th>
                            <th>{t('Change')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {approvePreview.preview_items.slice(0, 30).map(p => (
                            <tr key={p.category_id}>
                              <td>{p.parent_name_en ? `${p.parent_name_en} → ` : ''}{p.name_en}</td>
                              <td>{t(ISSUE_LABELS[p.issue_type] || p.issue_type)}</td>
                              <td>{t('Retain as valid empty category')}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {approvePreview.excluded_count > 0 && (
                  <details>
                    <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
                      {t('Excluded invalid / conflicting records')} ({approvePreview.excluded_count})
                    </summary>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem', margin: '0.5rem 0' }}>
                      {Object.entries(approvePreview.excluded_by_issue_type).map(([key, count]) => (
                        <span key={key} className="status rejected">
                          {t(ISSUE_LABELS[key] || key)}: {count}
                        </span>
                      ))}
                    </div>
                    {approvePreview.excluded_preview_items.length > 0 && (
                      <div className="table-wrap" style={{ maxHeight: '180px', overflowY: 'auto' }}>
                        <table className="data-table">
                          <thead>
                            <tr>
                              <th>{t('Category')}</th>
                              <th>{t('Issue')}</th>
                              <th>{t('Reason excluded')}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {approvePreview.excluded_preview_items.slice(0, 20).map(p => (
                              <tr key={p.category_id}>
                                <td>{p.parent_name_en ? `${p.parent_name_en} → ` : ''}{p.name_en}</td>
                                <td>{t(ISSUE_LABELS[p.issue_type] || p.issue_type)}</td>
                                <td>{p.reason}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </details>
                )}
              </div>
            )}

            {progressNote && <p role="status">{progressNote}</p>}

            <footer className="category-editor-footer">
              <Button type="button" variant="outline" disabled={busy} onClick={() => setPreviewKind(null)}>
                {t('Cancel')}
              </Button>
              <Button
                type="button"
                disabled={busy || (previewKind === 'approve-valid' && (approvePreview?.total_eligible ?? 0) === 0)}
                onClick={() => void confirmBulkAction()}
              >
                {busy
                  ? t('Processing...')
                  : previewKind === 'approve-valid'
                    ? t(`Confirm & Approve (${approvePreview?.total_eligible ?? 0})`)
                    : t('Apply Changes')}
              </Button>
            </footer>
          </div>
        </AdminModal>
      )}

      {/* Explicit Reclassify Modal (supports category-level or per-material discipline for shared categories) */}
      {reclassifyItem && (
        <AdminModal
          ref={reclassifyDialog}
          className="live-dialog category-editor-dialog"
          aria-labelledby="reclassify-category-title"
          open={Boolean(reclassifyItem)}
          busy={busy}
          onCancel={e => {
            e.preventDefault();
            if (!busy) setReclassifyItem(null);
          }}
          onClose={() => setReclassifyItem(null)}
          onRequestClose={() => {
            if (!busy) setReclassifyItem(null);
          }}
        >
          <form className="category-editor-form" onSubmit={submitReclassify}>
            <header className="category-editor-header">
              <h2 id="reclassify-category-title">{t('Reclassify')}: {reclassifyItem.name_en}</h2>
              <Button type="button" variant="ghost" aria-label={t('Close')} disabled={busy} onClick={() => setReclassifyItem(null)}>
                <IconX size={20} />
              </Button>
            </header>

            <label className="field">
              {t('Assign discipline to all unclassified materials in this category')}
              <select
                value={reclassifyDiscipline}
                disabled={busy}
                onChange={e => setReclassifyDiscipline(e.target.value)}
              >
                <option value="">{t('— Assign per material below —')}</option>
                {DISCIPLINES.map(d => (
                  <option key={d} value={d}>{t(d)}</option>
                ))}
              </select>
            </label>

            {reclassifyItem.materials_preview.length > 0 && (
              <div style={{ maxHeight: '240px', overflowY: 'auto' }}>
                <p style={{ margin: '0.25rem 0', fontSize: '0.9em' }}>
                  {t('Or assign each material individually (recommended for shared multi-discipline categories):')}
                </p>
                {reclassifyItem.materials_preview.map(mat => (
                  <label key={mat.id} className="field" style={{ marginBottom: '0.5rem' }}>
                    <span>{mat.name_en} <small>({mat.code})</small></span>
                    <select
                      value={materialDisciplines[mat.id] || ''}
                      disabled={busy}
                      onChange={e => setMaterialDisciplines(prev => ({ ...prev, [mat.id]: e.target.value }))}
                    >
                      <option value="">{t('Leave pending')}</option>
                      {DISCIPLINES.map(d => (
                        <option key={d} value={d}>{t(d)}</option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            )}

            <label className="field">
              {t('Audit notes (optional)')}
              <input
                value={decisionNotes}
                maxLength={500}
                disabled={busy}
                onChange={e => setDecisionNotes(e.target.value)}
                placeholder={t('Reason for classification decision')}
              />
            </label>

            <footer className="category-editor-footer">
              <Button type="button" variant="outline" disabled={busy} onClick={() => setReclassifyItem(null)}>
                {t('Cancel')}
              </Button>
              <Button type="submit" disabled={busy}>
                {busy ? t('Saving...') : t('Save Decision')}
              </Button>
            </footer>
          </form>
        </AdminModal>
      )}
    </details>
  );
}
