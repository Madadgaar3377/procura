"use client";
import { useEffect, useRef, useState } from 'react';
import {
  IconCheck,
  IconFileSpreadsheet,
  IconRefresh,
  IconUpload,
} from '@tabler/icons-react';
import { Button } from '@/components/ui/button';
import {
  AdminModal,
  ModalBody,
  ModalFooter,
  ModalHeader,
} from '@/features/shared/admin-modal';
import {
  DownloadAction,
  ProgressPanel,
} from '@/features/shared/admin-upload';
import { api, json, upload } from '@/lib/api/client';
import { useApi } from '@/lib/api/use-api';
import type { Schemas } from '@/lib/api/generated/schema';
import { useLocale } from '@/lib/locale';
import { useToast } from '@/features/shared/toast';

type RateImportOut = Schemas['RateImportOut'];
type RateImportRow = Schemas['RateImportRow'];
type Material = Schemas['MaterialOut'];
type RowFilter = 'all' | 'valid' | 'issues';

const isRunning = (status?: string) =>
  ['uploaded', 'queued', 'applying'].includes(status || '');
const isFinished = (status?: string) =>
  ['completed', 'completed_with_errors'].includes(status || '');

function useDebounce(value: string) {
  const [result, setResult] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setResult(value), 300);
    return () => clearTimeout(timer);
  }, [value]);
  return result;
}

export function PriceBookImportModal({
  orgId,
  open,
  onClose,
  onDone,
}: {
  orgId: string;
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const { arabic, t } = useLocale();
  const toast = useToast();
  const w = (en: string, ar: string) => (arabic ? ar : en);

  const [file, setFile] = useState<File>();
  const [dragging, setDragging] = useState(false);
  const [batchId, setBatchId] = useState<string>();
  const [result, setResult] = useState<RateImportOut>();
  const [filter, setFilter] = useState<RowFilter>('all');
  const [offset, setOffset] = useState(0);
  const [posting, setPosting] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [uploadPercent, setUploadPercent] = useState<number>();
  const [error, setError] = useState('');
  const [pollVersion, setPollVersion] = useState(0);
  const [mappingRow, setMappingRow] = useState<number | null>(null);
  const [materialQuery, setMaterialQuery] = useState('');

  const debouncedMaterialQuery = useDebounce(materialQuery);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const submitting = useRef(false);
  const completedRef = useRef<string | undefined>(undefined);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  const materialSearch = useApi<Material[]>(
    mappingRow !== null
      ? `taxonomy/materials?autocomplete=true&limit=20&q=${encodeURIComponent(debouncedMaterialQuery)}`
      : null,
  );

  const busy = posting || recovering || isRunning(result?.status);
  const numFmt = new Intl.NumberFormat(arabic ? 'ar-SA' : 'en-SA');
  const priceFmt = new Intl.NumberFormat(arabic ? 'ar-SA' : 'en-SA', {
    style: 'currency',
    currency: 'SAR',
  });

  // Automatically recover active or recent import on modal open / page refresh
  useEffect(() => {
    if (!open || batchId) return;
    let active = true;
    const controller = new AbortController();
    setRecovering(true);
    api<RateImportOut[]>(`pricebooks/${orgId}/imports/recent?limit=3`, {
      signal: controller.signal,
    })
      .then((rows) => {
        if (!active || !rows?.length) return;
        const activeOrPreview = rows.find(
          (r) => isRunning(r.status) || r.status === 'preview',
        );
        const latest = activeOrPreview || rows[0];
        if (latest && (isRunning(latest.status) || latest.status === 'preview')) {
          setResult(latest);
          setBatchId(latest.batch_id);
        }
      })
      .catch(() => {
        // Non-blocking recovery failure
      })
      .finally(() => {
        if (active) setRecovering(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [open, orgId, batchId]);

  // Poll server-side durable progress
  useEffect(() => {
    if (!open || !batchId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();

    async function poll() {
      try {
        const next = await api<RateImportOut>(
          `pricebooks/${orgId}/imports/${batchId}?limit=25&offset=${offset}&filter_status=${filter}`,
          { signal: controller.signal },
        );
        if (stopped) return;
        setResult(next);
        setError('');
        if (isFinished(next.status) && completedRef.current !== batchId) {
          completedRef.current = batchId;
          onDoneRef.current();
          window.dispatchEvent(new Event('procura:data-changed'));
          toast.success(
            w(
              `Import complete: ${next.saved} price(s) saved.`,
              `اكتمل الاستيراد: تم حفظ ${next.saved} سعر.`,
            ),
          );
        }
        if (!isRunning(next.status)) return;
      } catch (cause) {
        if (stopped) return;
        const msg = cause instanceof Error ? cause.message : '';
        setError(
          `${msg} ${w(
            'Saved progress is preserved. Reconnecting automatically.',
            'تم حفظ التقدم. جارٍ إعادة الاتصال تلقائياً.',
          )}`.trim(),
        );
      }
      if (!stopped) {
        timer = setTimeout(() => void poll(), 1500);
      }
    }

    void poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      controller.abort();
    };
  }, [open, orgId, batchId, offset, filter, pollVersion]);

  function selectFile(selected?: File) {
    if (!selected || busy) return;
    if (!/\.(xlsx|csv)$/i.test(selected.name)) {
      setError(
        w(
          'Choose an Excel (.xlsx) or CSV (.csv) file.',
          'اختر ملف Excel (.xlsx) أو CSV (.csv).',
        ),
      );
      return;
    }
    if (selected.size > 8_000_000) {
      setError(
        w(
          'Maximum file size is 8 MB.',
          'الحد الأقصى لحجم الملف هو 8 ميجابايت.',
        ),
      );
      return;
    }
    setFile(selected);
    setResult(undefined);
    setBatchId(undefined);
    setOffset(0);
    setFilter('all');
    setMappingRow(null);
    setError('');
    completedRef.current = undefined;
  }

  function resetForNewUpload() {
    if (busy) return;
    setFile(undefined);
    setResult(undefined);
    setBatchId(undefined);
    setOffset(0);
    setFilter('all');
    setMappingRow(null);
    setError('');
    completedRef.current = undefined;
  }

  async function previewRecords() {
    if (!file || busy || submitting.current) return;
    submitting.current = true;
    setPosting(true);
    setError('');
    setUploadPercent(0);
    try {
      const body = new FormData();
      body.set('file', file);
      const next = await upload<RateImportOut>(
        `pricebooks/${orgId}/bulk-import`,
        body,
        setUploadPercent,
      );
      setResult(next);
      setBatchId(next.batch_id);
      setOffset(0);
      setFilter('all');
      setPollVersion((v) => v + 1);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : w('Could not upload file for preview.', 'تعذر رفع الملف للمعاينة.'),
      );
    } finally {
      submitting.current = false;
      setPosting(false);
      setUploadPercent(undefined);
    }
  }

  async function triggerAction(kind: 'confirm' | 'retry') {
    if (!batchId || posting || submitting.current) return;
    submitting.current = true;
    setPosting(true);
    setError('');
    try {
      const next = await api<RateImportOut>(
        `pricebooks/${orgId}/imports/${batchId}/${kind}`,
        { method: 'POST' },
      );
      setResult(next);
      setMappingRow(null);
      setPollVersion((v) => v + 1);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : w('Could not start background import.', 'تعذر بدء الاستيراد في الخلفية.'),
      );
    } finally {
      submitting.current = false;
      setPosting(false);
    }
  }

  async function mapUnmatchedRow(rowNumber: number, materialId: string) {
    if (!batchId || !materialId || posting) return;
    setPosting(true);
    setError('');
    try {
      const next = await api<RateImportOut>(
        `pricebooks/${orgId}/imports/${batchId}/rows/${rowNumber}`,
        { method: 'PUT', body: json({ material_id: materialId }) },
      );
      setResult(next);
      setMappingRow(null);
      setMaterialQuery('');
      setPollVersion((v) => v + 1);
      toast.success(
        w('Material matched and validated.', 'تم ربط المادة والتحقق منها.'),
      );
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : w('Could not map material.', 'تعذر ربط المادة.'),
      );
    } finally {
      setPosting(false);
    }
  }

  const stageLabel = () => {
    if (!result) return '';
    if (result.status === 'uploaded') {
      return w(
        'Reading workbook and matching Procumaterial Material Master…',
        'جارٍ قراءة الملف ومطابقة دليل المواد الرئيسي…',
      );
    }
    if (result.status === 'preview') {
      return w(
        'Preview ready. Review summary and confirm to start automatic import.',
        'المعاينة جاهزة. راجع الملخص ثم أكّد لبدء الاستيراد التلقائي.',
      );
    }
    if (result.status === 'queued' || result.status === 'applying') {
      return w(
        'Importing valid private prices in background batches…',
        'جارٍ استيراد الأسعار الخاصة الصالحة في دفعات بالخلفية…',
      );
    }
    if (result.status === 'failed') {
      return w(
        'Import paused due to an error. Previously saved batches are preserved.',
        'توقف الاستيراد بسبب خطأ. تم حفظ الدفعات المكتملة مسبقاً.',
      );
    }
    if (result.status === 'completed_with_errors') {
      return w(
        'Import completed. Valid prices were saved; download failed rows to review skipped items.',
        'اكتمل الاستيراد. تم حفظ الأسعار الصالحة؛ يمكنك تنزيل الصفوف الفاشلة للمراجعة.',
      );
    }
    return w(
      'Import completed successfully. All valid prices are now in your private price book.',
      'اكتمل الاستيراد بنجاح. أصبحت جميع الأسعار الصالحة في دفتر أسعارك الخاص.',
    );
  };

  const previewStats: [string, number][] = result
    ? [
        [w('Total rows', 'إجمالي الصفوف'), result.rows || 0],
        [w('Valid rows', 'صفوف صالحة'), result.valid || 0],
        [w('New prices', 'أسعار جديدة'), result.new_count || 0],
        [w('Existing to update', 'تحديث أسعار حالية'), result.update_count || 0],
        [w('Unmatched materials', 'مواد غير مطابقة'), result.unmatched_count || 0],
        [w('Invalid rows', 'صفوف غير صالحة'), result.invalid_count || 0],
        [w('Duplicates', 'تكرارات'), result.duplicate_count || 0],
        [w('Conflicts', 'تعارضات'), result.conflict_count || 0],
      ]
    : [];

  const resultStats: [string, number][] = result
    ? [
        [w('Processed rows', 'الصفوف المعالجة'), result.completed_rows || 0],
        [w('New saved', 'تم إنشاء'), result.created || 0],
        [w('Updated', 'تم تحديث'), result.updated_count || 0],
        [w('Skipped', 'تم تخطي'), result.skipped_count || 0],
        [w('Failed / issues', 'فشل / مشكلات'), result.errors || 0],
      ]
    : [];

  const rowStatusLabel = (row: RateImportRow) => {
    const labels: Record<string, [string, string]> = {
      ready: ['Valid', 'صالح'],
      saved: ['Saved', 'تم الحفظ'],
      unmatched: ['Unmatched', 'غير مطابق'],
      invalid: ['Invalid', 'غير صالح'],
      duplicate: ['Duplicate', 'مكرر'],
      conflict: ['Conflict', 'تعارض'],
      failed: ['Failed', 'فشل'],
    };
    const pair = labels[row.status] || [row.status, row.status];
    return arabic ? pair[1] : pair[0];
  };

  const formatUnitPrice = (val: string) => {
    const num = Number(val);
    return Number.isFinite(num) && val !== '' ? priceFmt.format(num) : val;
  };

  const filteredTotal = result
    ? filter === 'valid'
      ? result.valid
      : filter === 'issues'
        ? result.errors
        : result.rows
    : 0;

  return (
    <AdminModal
      open={open}
      size="lg"
      normalize={false}
      busy={posting}
      onRequestClose={onClose}
      aria-labelledby="price-book-import-title"
      className="price-book-import-dialog"
    >
      <ModalHeader onClose={onClose} disabled={posting}>
        <h2 id="price-book-import-title">
          {w('Import Private Price Book (Excel / CSV)', 'استيراد دفتر الأسعار الخاص (Excel / CSV)')}
        </h2>
        <p>
          {w(
            'Upload once, preview matched Material Master items, and import valid prices automatically. Private rates are never exposed to buyers.',
            'ارفع الملف مرة واحدة، وعاين المواد المطابقة لدليل المواد الرئيسي، واستورد الأسعار الصالحة تلقائياً. لا تُكشف أسعارك الخاصة للمشترين أبداً.',
          )}
        </p>
      </ModalHeader>

      <ModalBody>
        {/* Step 1: Template & Existing Price Book downloads */}
        <div className="price-import-downloads">
          <DownloadAction
            path={`pricebooks/${orgId}/import-template`}
            filename="procumaterial-price-book-template.xlsx"
            onError={setError}
          >
            {w('Download Excel Template', 'تنزيل قالب Excel')}
          </DownloadAction>
          <DownloadAction
            path={`pricebooks/${orgId}/export`}
            filename="supplier-price-book.xlsx"
            onError={setError}
          >
            {w('Download Existing Price Book', 'تنزيل دفتر الأسعار الحالي')}
          </DownloadAction>
          {result && !isRunning(result.status) && (
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={resetForNewUpload}
            >
              <IconRefresh size={16} />
              <span>{w('Upload New File', 'رفع ملف جديد')}</span>
            </Button>
          )}
        </div>

        {/* Step 2: File picker / drag-and-drop */}
        {!batchId && (
          <div
            className={`admin-upload-zone${dragging ? ' is-dragging' : ''}${busy ? ' is-disabled' : ''}`}
            aria-disabled={busy || undefined}
            onDragOver={(e) => {
              e.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                setDragging(false);
              }
            }}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (e.dataTransfer.files.length > 1) {
                setError(
                  w('Choose one file at a time.', 'اختر ملفاً واحداً في كل مرة.'),
                );
                return;
              }
              selectFile(e.dataTransfer.files[0]);
            }}
          >
            <span className="admin-upload-icon">
              <IconUpload size={24} />
            </span>
            <strong>
              {w(
                'Drag & drop your Excel or CSV file here',
                'اسحب ملف Excel أو CSV وأفلته هنا',
              )}
            </strong>
            <p>
              {w(
                'Supports .xlsx and .csv files up to 8 MB (up to 10,000 rows).',
                'يدعم ملفات .xlsx و .csv حتى 8 ميجابايت (حتى 10,000 صف).',
              )}
            </p>
            <input
              ref={fileInputRef}
              className="sr-only"
              type="file"
              accept=".xlsx,.csv"
              disabled={busy}
              aria-label={w('Choose Excel / CSV', 'اختيار ملف Excel / CSV')}
              tabIndex={-1}
              onChange={(e) => {
                selectFile(e.target.files?.[0]);
                e.target.value = '';
              }}
            />
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => fileInputRef.current?.click()}
            >
              {file
                ? w('Replace File', 'استبدال الملف')
                : w('Choose Excel / CSV', 'اختيار ملف Excel / CSV')}
            </Button>
            {file && (
              <div className="admin-selected-file" aria-live="polite">
                <IconFileSpreadsheet size={20} />
                <div>
                  <strong>{file.name}</strong>
                  <span>
                    {new Intl.NumberFormat(arabic ? 'ar-SA' : 'en-US', {
                      maximumFractionDigits: 1,
                    }).format(file.size / 1024)}{' '}
                    KB
                  </span>
                </div>
                <IconCheck size={18} />
              </div>
            )}
          </div>
        )}

        {recovering && (
          <p role="status">
            {w('Checking for active import…', 'جارٍ التحقق من وجود استيراد نشط…')}
          </p>
        )}

        {posting && uploadPercent !== undefined && (
          <ProgressPanel
            status="uploading"
            stage={w('Uploading file…', 'جارٍ رفع الملف…')}
            percent={uploadPercent}
          />
        )}

        {error && (
          <p role="alert" className="form-error">
            {t(error)}
          </p>
        )}

        {/* Step 3: Progress Panel + Preview / Results */}
        {result && (
          <div className="price-import-results">
            <ProgressPanel
              status={result.status === 'applying' ? 'processing' : result.status}
              stage={stageLabel()}
              percent={result.progress_percent}
            >
              {result.filename && (
                <p className="price-import-meta">
                  <strong>{w('File:', 'الملف:')}</strong> <bdi>{result.filename}</bdi>
                </p>
              )}
              {(result.jobs_total || 0) > 0 && (
                <p role="status" aria-live="polite" className="price-import-meta">
                  {w('Current job', 'المهمة الحالية')}:{' '}
                  <strong>
                    {numFmt.format(result.current_job || 0)} /{' '}
                    {numFmt.format(result.jobs_total || 0)}
                  </strong>{' '}
                  · {w('Current batch', 'الدفعة الحالية')}:{' '}
                  <strong>
                    {numFmt.format(result.current_batch || 0)} /{' '}
                    {numFmt.format(result.batches_in_job || 0)}
                  </strong>{' '}
                  · {w('Processed rows', 'الصفوف المعالجة')}:{' '}
                  <strong>
                    {numFmt.format(result.completed_rows || 0)} /{' '}
                    {numFmt.format(result.valid || 0)}
                  </strong>
                </p>
              )}
            </ProgressPanel>

            {result.failure_reason && (
              <p role="alert" className="form-error">
                {result.failure_reason}
              </p>
            )}

            {/* Preview summary KPI grid */}
            <dl className="admin-stat-grid price-import-stats">
              {(result.stage === 'preview' ? previewStats : resultStats).map(
                ([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{numFmt.format(value)}</dd>
                  </div>
                ),
              )}
            </dl>

            {/* Download failed / unmatched rows */}
            {batchId && (result.errors || 0) > 0 && (
              <div className="price-import-actions-bar">
                <DownloadAction
                  path={`pricebooks/${orgId}/imports/${batchId}/errors.xlsx`}
                  filename={`price-book-failed-rows-${batchId.slice(0, 8)}.xlsx`}
                  onError={setError}
                >
                  {w(
                    `Download Failed Rows (${result.errors})`,
                    `تنزيل الصفوف الفاشلة (${result.errors})`,
                  )}
                </DownloadAction>
              </div>
            )}

            {/* Filter tabs + Sample Preview Table */}
            {result.rows > 0 && (
              <>
                <div className="price-import-filters" role="tablist">
                  {(
                    [
                      ['all', w('All Rows', 'جميع الصفوف'), result.rows],
                      ['valid', w('Valid', 'صالحة'), result.valid],
                      ['issues', w('Issues / Review', 'مشكلات / مراجعة'), result.errors],
                    ] as const
                  ).map(([key, label, count]) => (
                    <Button
                      key={key}
                      type="button"
                      variant={filter === key ? 'default' : 'outline'}
                      onClick={() => {
                        setFilter(key);
                        setOffset(0);
                      }}
                    >
                      {label} ({numFmt.format(count || 0)})
                    </Button>
                  ))}
                </div>

                <div className="table-wrap price-import-table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>{w('Row', 'صف')}</th>
                        <th>{w('Category', 'الفئة')}</th>
                        <th>{w('Material Description', 'وصف المادة')}</th>
                        <th>{w('Unit', 'الوحدة')}</th>
                        <th>{w('Unit Price (SAR)', 'السعر (ر.س)')}</th>
                        <th>{w('Action', 'الإجراء')}</th>
                        <th>{w('Status', 'الحالة')}</th>
                        <th>{w('Issue / Match', 'الملاحظة / المطابقة')}</th>
                        {result.status === 'preview' && (
                          <th>{w('Resolve', 'معالجة')}</th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {(result.items || []).map((row) => (
                        <tr key={row.row}>
                          <td>{row.row}</td>
                          <td>
                            {[row.main_category, row.subcategory]
                              .filter(Boolean)
                              .join(' / ')}
                          </td>
                          <td>
                            <strong>{row.description}</strong>
                            {(row.brand || row.model) && (
                              <small className="price-import-subtext">
                                {[row.brand, row.model].filter(Boolean).join(' · ')}
                              </small>
                            )}
                          </td>
                          <td>{row.unit}</td>
                          <td>
                            {formatUnitPrice(row.unit_price)}
                            {row.existing_price && (
                              <small className="price-import-subtext">
                                {w('Was:', 'السابق:')}{' '}
                                {formatUnitPrice(row.existing_price)}
                              </small>
                            )}
                          </td>
                          <td>
                            {row.status === 'ready' || row.status === 'saved'
                              ? row.action === 'update'
                                ? w('Update', 'تحديث')
                                : w('New', 'جديد')
                              : '—'}
                          </td>
                          <td>
                            <span
                              className={`price-import-pill status-${row.status}`}
                            >
                              {rowStatusLabel(row)}
                            </span>
                          </td>
                          <td>
                            {row.issue ? (
                              <span className="price-import-issue">
                                {row.issue}
                              </span>
                            ) : row.matched_material_code ? (
                              <small>
                                {row.matched_material_code} ·{' '}
                                {row.matched_material_name}
                              </small>
                            ) : (
                              '—'
                            )}
                          </td>
                          {result.status === 'preview' && (
                            <td>
                              {(row.status === 'unmatched' ||
                                row.status === 'conflict') && (
                                <Button
                                  type="button"
                                  variant="outline"
                                  onClick={() => {
                                    setMappingRow(row.row);
                                    setMaterialQuery(row.description || '');
                                  }}
                                >
                                  {w('Map material', 'ربط المادة')}
                                </Button>
                              )}
                            </td>
                          )}
                        </tr>
                      ))}
                      {!result.items?.length && (
                        <tr>
                          <td colSpan={result.status === 'preview' ? 9 : 8}>
                            {w('No rows match this filter.', 'لا توجد صفوف مطابقة للتصفية.')}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

                {filteredTotal > 25 && (
                  <div className="table-footer">
                    <span>
                      {numFmt.format(offset + 1)}–
                      {numFmt.format(Math.min(offset + 25, filteredTotal))} /{' '}
                      {numFmt.format(filteredTotal)}
                    </span>
                    <div className="live-actions">
                      <Button
                        type="button"
                        variant="outline"
                        disabled={offset === 0}
                        onClick={() => setOffset(Math.max(0, offset - 25))}
                      >
                        {t('Previous')}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        disabled={offset + 25 >= filteredTotal}
                        onClick={() => setOffset(offset + 25)}
                      >
                        {t('Next')}
                      </Button>
                    </div>
                  </div>
                )}
              </>
            )}

            {/* Inline manual material mapping for unmatched/ambiguous rows */}
            {mappingRow !== null && result.status === 'preview' && (
              <section className="panel price-import-mapper">
                <div className="price-dialog-heading">
                  <h3>
                    {w('Map Row to Material Master', 'ربط الصف بدليل المواد الرئيسي')}{' '}
                    #{mappingRow}
                  </h3>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setMappingRow(null)}
                  >
                    {w('Cancel', 'إلغاء')}
                  </Button>
                </div>
                <label className="field">
                  {w('Search existing Material Master', 'البحث في دليل المواد الرئيسي')}
                  <input
                    type="search"
                    value={materialQuery}
                    maxLength={120}
                    onChange={(e) => setMaterialQuery(e.target.value)}
                    placeholder={w(
                      'Type material name or code…',
                      'اكتب اسم المادة أو رمزها…',
                    )}
                  />
                </label>
                <select
                  aria-label={w('Select Material Master item', 'اختر المادة من الدليل')}
                  value=""
                  disabled={posting}
                  onChange={(e) => {
                    if (e.target.value) {
                      void mapUnmatchedRow(mappingRow, e.target.value);
                    }
                  }}
                >
                  <option value="">
                    {w('Select an approved material…', 'اختر مادة معتمدة…')}
                  </option>
                  {(materialSearch.data || []).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.code} · {arabic && m.name_ar ? m.name_ar : m.name_en} ({m.unit})
                    </option>
                  ))}
                </select>
              </section>
            )}

            <p className="workflow-help">
              {w(
                'Jobs process up to 300 rows in committed batches of 30. Closing this modal or refreshing the page does not cancel the import. Automatic quotation submission is never enabled automatically by importing prices.',
                'تعالج المهام حتى 300 صف في دفعات محفوظة من 30 صفاً. إغلاق هذه النافذة أو تحديث الصفحة لا يلغي الاستيراد. لا يتم تفعيل التقديم التلقائي للعروض بمجرد استيراد الأسعار.',
              )}
            </p>
          </div>
        )}
      </ModalBody>

      <ModalFooter>
        <Button type="button" variant="outline" disabled={posting} onClick={onClose}>
          {w('Close', 'إغلاق')}
        </Button>

        {result?.status === 'failed' ? (
          <Button
            type="button"
            disabled={posting}
            onClick={() => void triggerAction('retry')}
          >
            {w('Retry Import', 'إعادة المحاولة')}
          </Button>
        ) : result?.status === 'preview' ? (
          <Button
            type="button"
            disabled={posting || !(result.valid > 0)}
            onClick={() => void triggerAction('confirm')}
          >
            {w(
              `Import Now (${numFmt.format(result.valid)} valid)`,
              `استيراد الآن (${numFmt.format(result.valid)} صالح)`,
            )}
          </Button>
        ) : !batchId ? (
          <Button
            type="button"
            disabled={!file || busy}
            onClick={() => void previewRecords()}
          >
            <IconUpload size={17} />
            <span>{w('Preview Records', 'معاينة السجلات')}</span>
          </Button>
        ) : null}
      </ModalFooter>
    </AdminModal>
  );
}
