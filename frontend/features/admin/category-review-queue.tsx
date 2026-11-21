"use client";
import { useEffect, useRef, useState } from 'react';
import {
  IconCheck,
  IconFilter,
  IconGitMerge,
  IconRefresh,
  IconSparkles,
  IconWand,
  IconX,
} from '@tabler/icons-react';
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
  proposed_path?: string[];
  hierarchy_repair_action?: string | null;
  hierarchy_repair_safe?: boolean;
  affected_capabilities_count?: number;
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
  total_safe_hierarchy_repairs?: number;
  total_hierarchy_conflicts?: number;
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
  approved_count?: number;
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

type HierarchyRepairItem = {
  category_id: string;
  code: string;
  name_en: string;
  parent_id: string | null;
  parent_name_en: string | null;
  grandparent_id: string | null;
  grandparent_name_en: string | null;
  current_path: string[];
  proposed_path: string[];
  repair_action: string;
  target_parent_id: string | null;
  target_category_id: string | null;
  affected_materials_count: number;
  affected_capabilities_count: number;
  safe: boolean;
  status: string;
  reason: string;
};

type HierarchyRepairPreview = {
  total_hierarchy_issues: number;
  safe_repairs: number;
  manual_review_count: number;
  affected_materials: number;
  affected_capabilities: number;
  counts_by_action: Record<string, number>;
  batch_size: number;
  job_size: number;
  items: HierarchyRepairItem[];
};

type HierarchyRepairJob = {
  job_id: string | null;
  status: string;
  total_records: number;
  completed_records: number;
  repaired_count: number;
  skipped_count: number;
  failed_count: number;
  batches_executed: number;
  materials_updated: number;
  references_moved: number;
  remaining_safe: number;
  has_more: boolean;
  errors: Array<Record<string, unknown>>;
  failure_reason: string | null;
};

const ISSUE_LABELS: Record<string, string> = {
  missing_parent: 'Missing parent',
  archived_parent: 'Archived parent',
  hierarchy_depth_exceeded: 'Exceeds 2 levels',
  missing_discipline: 'Missing discipline',
  unsupported_discipline: 'Unsupported discipline',
  empty_category: 'Empty category',
  inactive_materials_only: 'Inactive only',
  duplicate_category: 'Duplicate',
  parent_mismatch: 'Parent mismatch',
  ambiguous_classification: 'Ambiguous',
  invalid_import_data: 'Invalid import',
};

const DISCIPLINES = ['Civil', 'Electrical', 'Mechanical'] as const;
const PAGE_SIZE = 50;

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
  const [previewKind, setPreviewKind] = useState<'auto-fix' | 'approve-valid' | 'hierarchy-repair' | null>(null);
  const [fixPreview, setFixPreview] = useState<BulkFixResponse | null>(null);
  const [approvePreview, setApprovePreview] = useState<BulkApproveResponse | null>(null);
  const [hierarchyPreview, setHierarchyPreview] = useState<HierarchyRepairPreview | null>(null);
  const [activeRepairJob, setActiveRepairJob] = useState<HierarchyRepairJob | null>(null);
  const previewDialog = useRef<HTMLDialogElement>(null);

  // Reclassify modal state
  const [reclassifyItem, setReclassifyItem] = useState<CategoryReviewItem | null>(null);
  const [reclassifyDiscipline, setReclassifyDiscipline] = useState<string>('');
  const [materialDisciplines, setMaterialDisciplines] = useState<Record<string, string>>({});
  const [decisionNotes, setDecisionNotes] = useState('');
  const reclassifyDialog = useRef<HTMLDialogElement>(null);

  const queryParams = new URLSearchParams({
    limit: String(PAGE_SIZE),
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

  // Poll durable hierarchy repair job until completion
  useEffect(() => {
    if (!activeRepairJob?.job_id) return;
    if (!['queued', 'processing'].includes(activeRepairJob.status)) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const status = await api<HierarchyRepairJob>(
          `taxonomy/review/hierarchy-repairs/jobs/${activeRepairJob.job_id}`,
        );
        if (cancelled) return;
        setActiveRepairJob(status);
        if (!['queued', 'processing'].includes(status.status)) {
          setProgressNote('');
          toast.success(
            t('Hierarchy repairs completed'),
            t(`${status.repaired_count} repaired, ${status.skipped_count} skipped, ${status.failed_count} failed (${status.batches_executed} batches).`),
          );
          analysis.reload();
          onChanged();
        }
      } catch (cause) {
        if (cancelled) return;
        const msg = cause instanceof Error ? cause.message : t('Could not check repair progress');
        setError(msg);
      }
    }, 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [activeRepairJob, analysis, onChanged, t, toast]);

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

  async function openHierarchyRepairPreview() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const preview = await api<HierarchyRepairPreview>('taxonomy/review/hierarchy-repairs/preview?limit=200&offset=0');
      setHierarchyPreview(preview);
      setFixPreview(null);
      setApprovePreview(null);
      setPreviewKind('hierarchy-repair');
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : t('Could not preview hierarchy repairs');
      setError(msg);
      toast.error(t('Preview failed'), msg);
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
      setHierarchyPreview(null);
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
      setHierarchyPreview(null);
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
      if (previewKind === 'hierarchy-repair') {
        setProgressNote(t('Starting safe hierarchy repair job (batches of 30)...'));
        const job = await api<HierarchyRepairJob>('taxonomy/review/hierarchy-repairs', {
          method: 'POST',
          body: json({ job_limit: 300, batch_size: 30, async_mode: true }),
        });
        setPreviewKind(null);
        setHierarchyPreview(null);
        if (job.job_id && ['queued', 'processing'].includes(job.status)) {
          setActiveRepairJob(job);
          toast.success(
            t('Hierarchy repair started'),
            t(`Processing ${job.total_records} safe repairs in batches of 30.`),
          );
        } else {
          setActiveRepairJob(job);
          toast.success(
            t('Hierarchy repairs completed'),
            t(`${job.repaired_count} repaired, ${job.skipped_count} skipped, ${job.failed_count} failed.`),
          );
          refreshAll();
        }
      } else if (previewKind === 'auto-fix') {
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
        refreshAll();
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
            : t(`${totalRetained} valid empty categories retained in ${totalBatches} batch(es).`),
        );
        refreshAll();
      }
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : t('Bulk action could not finish');
      setError(msg);
      toast.error(t('Bulk action failed'), msg);
    } finally {
      if (previewKind !== 'hierarchy-repair') setProgressNote('');
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

  const hierarchyConflicts =
    data.total_hierarchy_conflicts ??
    (data.counts_by_issue_type.hierarchy_depth_exceeded || 0) +
      (data.counts_by_issue_type.missing_parent || 0) +
      (data.counts_by_issue_type.archived_parent || 0);
  const invalidImports = data.counts_by_issue_type.invalid_import_data || 0;
  const safeHierarchyRepairs = data.total_safe_hierarchy_repairs || 0;
  const totalSafeRepairs = safeHierarchyRepairs + data.total_safe_auto_fix;
  const jobRunning = Boolean(activeRepairJob?.job_id && ['queued', 'processing'].includes(activeRepairJob.status));

  function rowStatusBadge(item: CategoryReviewItem) {
    if (item.hierarchy_repair_safe) {
      return <span className="status verified">{t('Safe repair')}</span>;
    }
    if (item.safe_to_auto_fix) {
      return <span className="status verified">{t('Auto-fix')}</span>;
    }
    if (item.can_retain_empty) {
      return <span className="status pending">{t('Valid empty')}</span>;
    }
    return <span className="status rejected">{t('Manual review')}</span>;
  }

  return (
    <details className="panel industry-taxonomy-review" open={analyzed || conflictsOnly || Boolean(issueFilter) || jobRunning}>
      <summary>
        {t('Category review needed')} <span>{data.total_pending}</span>
      </summary>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem', marginTop: '0.85rem' }}>
        {/* Compact Summary Metrics */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: '0.5rem',
          }}
        >
          <div style={{ padding: '0.6rem 0.75rem', borderRadius: '8px', border: '1px solid var(--border, #e2e8f0)' }}>
            <div style={{ fontSize: '0.78rem', opacity: 0.75 }}>{t('Total review issues')}</div>
            <strong style={{ fontSize: '1.25rem' }}>{data.total_pending}</strong>
          </div>
          <div style={{ padding: '0.6rem 0.75rem', borderRadius: '8px', border: '1px solid var(--border, #e2e8f0)' }}>
            <div style={{ fontSize: '0.78rem', opacity: 0.75 }}>{t('Hierarchy conflicts')}</div>
            <strong style={{ fontSize: '1.25rem' }}>{hierarchyConflicts}</strong>
          </div>
          <div style={{ padding: '0.6rem 0.75rem', borderRadius: '8px', border: '1px solid var(--border, #e2e8f0)' }}>
            <div style={{ fontSize: '0.78rem', opacity: 0.75 }}>{t('Invalid imports')}</div>
            <strong style={{ fontSize: '1.25rem' }}>{invalidImports}</strong>
          </div>
          <div style={{ padding: '0.6rem 0.75rem', borderRadius: '8px', border: '1px solid var(--border, #e2e8f0)' }}>
            <div style={{ fontSize: '0.78rem', opacity: 0.75 }}>{t('Safe repairs')}</div>
            <strong style={{ fontSize: '1.25rem' }}>{totalSafeRepairs}</strong>
          </div>
          <div style={{ padding: '0.6rem 0.75rem', borderRadius: '8px', border: '1px solid var(--border, #e2e8f0)' }}>
            <div style={{ fontSize: '0.78rem', opacity: 0.75 }}>{t('Manual review')}</div>
            <strong style={{ fontSize: '1.25rem' }}>{data.total_conflicts}</strong>
          </div>
        </div>

        {/* Action Toolbar */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center' }}>
          <Button type="button" variant="outline" disabled={busy || jobRunning} onClick={() => void handleAnalyzeAll()}>
            <IconSparkles size={16} /> {t('Analyze')}
          </Button>
          <Button
            type="button"
            variant={hierarchyConflicts > 0 ? 'default' : 'outline'}
            disabled={busy || jobRunning || hierarchyConflicts === 0}
            onClick={() => void openHierarchyRepairPreview()}
          >
            <IconGitMerge size={16} /> {t('Repair Hierarchy')} ({safeHierarchyRepairs}/{hierarchyConflicts})
          </Button>
          {data.total_safe_auto_fix > 0 && (
            <Button
              type="button"
              variant="outline"
              disabled={busy || jobRunning}
              onClick={() => void openAutoFixPreview()}
            >
              <IconWand size={16} /> {t('Auto-Fix Safe Records')} ({data.total_safe_auto_fix})
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            disabled={busy || jobRunning || data.total_valid_approvable === 0}
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

        {/* Active Background Job Progress Banner */}
        {activeRepairJob && activeRepairJob.job_id && (
          <div
            role="status"
            className="notice"
            style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem', alignItems: 'center', justifyContent: 'space-between' }}
          >
            <div>
              <strong>
                {jobRunning ? t('Hierarchy repair in progress') : t('Hierarchy repair finished')}:
              </strong>{' '}
              {activeRepairJob.completed_records} / {activeRepairJob.total_records}{' '}
              ({t('repaired')}: {activeRepairJob.repaired_count}, {t('skipped')}: {activeRepairJob.skipped_count}, {t('failed')}: {activeRepairJob.failed_count})
            </div>
            {!jobRunning && (
              <Button type="button" size="sm" variant="ghost" onClick={() => setActiveRepairJob(null)}>
                {t('Dismiss')}
              </Button>
            )}
          </div>
        )}

        {/* Filter Pills + Server-side Search */}
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
            placeholder={t('Search categories or paths...')}
            style={{ marginInlineStart: 'auto', minWidth: '200px' }}
          />
        </div>

        {progressNote && <p role="status" className="notice">{progressNote}</p>}
        {error && <p role="alert" className="category-editor-error">{error}</p>}

        {/* Clean Compact Review Table */}
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('Category')}</th>
                <th>{t('Current path')}</th>
                <th>{t('Proposed path')}</th>
                <th>{t('Materials affected')}</th>
                <th>{t('Issue')}</th>
                <th>{t('Status')}</th>
                <th>{t('Action')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map(item => {
                const currentPath =
                  item.hierarchy_path && item.hierarchy_path.length > 0
                    ? item.hierarchy_path.join(' → ')
                    : item.parent_name_en
                      ? `${item.parent_name_en} → ${item.name_en}`
                      : item.name_en;
                const proposedPath =
                  item.proposed_path && item.proposed_path.length > 0
                    ? item.proposed_path.join(' → ')
                    : '—';
                return (
                  <tr key={item.id}>
                    <td>
                      <strong>{item.name_en}</strong>
                      <div><small>{item.code}</small></div>
                    </td>
                    <td title={item.reason}>
                      <span dir="auto">{currentPath}</span>
                    </td>
                    <td>
                      <span dir="auto">{proposedPath}</span>
                    </td>
                    <td>
                      <div>{item.active_material_count} {t('materials')}</div>
                      {(item.affected_capabilities_count || 0) > 0 && (
                        <small>{item.affected_capabilities_count} {t('capabilities')}</small>
                      )}
                    </td>
                    <td>
                      <span className="status pending" title={item.reason}>
                        {t(ISSUE_LABELS[item.issue_type] || item.issue_label)}
                      </span>
                    </td>
                    <td>{rowStatusBadge(item)}</td>
                    <td>
                      <div className="category-row-actions">
                        {item.hierarchy_repair_safe && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy || jobRunning}
                            onClick={() => void openHierarchyRepairPreview()}
                          >
                            {t('Preview repair')}
                          </Button>
                        )}
                        {item.can_approve && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy || jobRunning}
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
                            disabled={busy || jobRunning}
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
                            disabled={busy || jobRunning}
                            onClick={() => void submitDecision(item, 'merge_duplicate')}
                          >
                            {t('Merge')}
                          </Button>
                        )}
                        {(item.missing_discipline_count > 0 || item.unsupported_discipline_count > 0) && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy || jobRunning}
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
                            disabled={busy || jobRunning}
                            onClick={() => void submitDecision(item, 'archive')}
                          >
                            {t('Archive')}
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {data.items.length === 0 && (
                <tr>
                  <td colSpan={7}>{t('No categories match this review filter.')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Server-side Pagination */}
        <div className="table-pagination">
          <span>
            {t('Showing')} {data.items.length === 0 ? 0 : offset + 1}–{offset + data.items.length} {t('of')} {data.total_filtered}
          </span>
          {offset > 0 && (
            <Button type="button" variant="outline" onClick={() => setOffset(v => Math.max(0, v - PAGE_SIZE))}>
              {t('Previous')}
            </Button>
          )}
          {offset + data.items.length < data.total_filtered && (
            <Button type="button" variant="outline" onClick={() => setOffset(v => v + PAGE_SIZE)}>
              {t('Next')}
            </Button>
          )}
        </div>
      </div>

      {/* Confirmation Preview Modal for Bulk Actions & Hierarchy Repair */}
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
                {previewKind === 'hierarchy-repair'
                  ? t('Preview Hierarchy Repairs')
                  : previewKind === 'auto-fix'
                    ? t('Confirm Auto-Fix Safe Records')
                    : t('Confirm Approve All Valid')}
              </h2>
              <Button type="button" variant="ghost" aria-label={t('Close')} disabled={busy} onClick={() => setPreviewKind(null)}>
                <IconX size={20} />
              </Button>
            </header>

            {previewKind === 'hierarchy-repair' && hierarchyPreview && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                <p style={{ margin: 0 }}>
                  {t('Only verified safe repairs will be applied in committed batches of 30 (up to 300 per job). Existing Material IDs, supplier capabilities, and RFQ references are preserved. Ambiguous records remain untouched for manual review.')}
                </p>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
                    gap: '0.45rem',
                  }}
                >
                  <div><small>{t('Total issues')}</small><div><strong>{hierarchyPreview.total_hierarchy_issues}</strong></div></div>
                  <div><small>{t('Safe repairs')}</small><div><strong>{hierarchyPreview.safe_repairs}</strong></div></div>
                  <div><small>{t('Manual review')}</small><div><strong>{hierarchyPreview.manual_review_count}</strong></div></div>
                  <div><small>{t('Materials affected')}</small><div><strong>{hierarchyPreview.affected_materials}</strong></div></div>
                  <div><small>{t('Capabilities affected')}</small><div><strong>{hierarchyPreview.affected_capabilities}</strong></div></div>
                </div>

                {hierarchyPreview.items.length > 0 && (
                  <div className="table-wrap" style={{ maxHeight: '300px', overflowY: 'auto' }}>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>{t('Current hierarchy')}</th>
                          <th>{t('Proposed hierarchy')}</th>
                          <th>{t('Materials')}</th>
                          <th>{t('Capabilities')}</th>
                          <th>{t('Status')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {hierarchyPreview.items.map(item => (
                          <tr key={item.category_id}>
                            <td dir="auto" title={item.code}>{item.current_path.join(' → ')}</td>
                            <td dir="auto">{item.proposed_path.length ? item.proposed_path.join(' → ') : '—'}</td>
                            <td>{item.affected_materials_count}</td>
                            <td>{item.affected_capabilities_count}</td>
                            <td>
                              <span
                                className={`status ${item.safe ? 'verified' : 'rejected'}`}
                                title={item.reason}
                              >
                                {item.safe ? t('Safe') : t('Manual review')}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {previewKind === 'auto-fix' && fixPreview && (
              <div>
                <p>
                  {t('Only safe, verified fixes will be applied in batches of 30 (up to 300 per job). Material IDs and RFQ references are preserved. Ambiguous records remain pending.')}
                </p>
                <ul>
                  <li><strong>{t('Safe categories in job')}:</strong> {fixPreview.categories_processed}{fixPreview.has_more ? '+' : ''}</li>
                  <li><strong>{t('Materials to assign discipline')}:</strong> {fixPreview.materials_updated}</li>
                  <li><strong>{t('Safe duplicate aliases to merge')}:</strong> {fixPreview.duplicates_merged}</li>
                  <li><strong>{t('Parent links to repair')}:</strong> {fixPreview.parent_links_repaired}</li>
                  <li><strong>{t('Ambiguous records left for manual review')}:</strong> {fixPreview.skipped_ambiguous}</li>
                </ul>
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
                    {approvePreview.total_eligible}
                  </li>
                  <li>
                    <strong>{t('Excluded invalid or conflicting records')}:</strong>{' '}
                    {approvePreview.excluded_count} ({t('will remain pending for manual review')})
                  </li>
                </ul>

                {approvePreview.preview_items.length > 0 && (
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
                disabled={
                  busy ||
                  (previewKind === 'approve-valid' && (approvePreview?.total_eligible ?? 0) === 0) ||
                  (previewKind === 'hierarchy-repair' && (hierarchyPreview?.safe_repairs ?? 0) === 0)
                }
                onClick={() => void confirmBulkAction()}
              >
                {busy
                  ? t('Processing...')
                  : previewKind === 'hierarchy-repair'
                    ? t(`Apply Safe Repairs (${hierarchyPreview?.safe_repairs ?? 0})`)
                    : previewKind === 'approve-valid'
                      ? t(`Confirm & Approve (${approvePreview?.total_eligible ?? 0})`)
                      : t('Apply Changes')}
              </Button>
            </footer>
          </div>
        </AdminModal>
      )}

      {/* Explicit Reclassify Modal */}
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
                  {t('Or assign each material individually:')}
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
