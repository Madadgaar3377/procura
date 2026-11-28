import type { Schemas } from '@/lib/api/generated/schema';

/** Buyer quotation review: pure view-model helpers (no React, unit-tested). */

export type QuoteDetail = Schemas['QuoteDetail'];
export type QuoteLine = Schemas['QuoteLineOut'];
export type RfqItem = Schemas['RFQItemOut'];

/** One line of the backend quotation document (the same data the PDF renders). */
export type DocLine = {
  rfq_item_id: string; description?: string; offered_description?: string; brand?: string; model?: string;
  specification?: string; quantity?: string; unit?: string; unit_price?: string; line_total?: string;
  lead_days?: number | null; vat_rate?: string; compliance?: string;
  discount_percent?: string; discount_amount?: string; net_total?: string;
};
export type DocTotals = {
  subtotal?: string; discount?: string; delivery_charge?: string; net_amount?: string; vat?: string; total?: string; currency?: string;
};
export type DocParty = { name?: string | null; city?: string | null; address?: string | null; cr_number?: string | null; vat_number?: string | null };
export type QuoteDocument = {
  quotation_reference?: string; rfq_reference?: string; rfq_title?: string; quotation_date?: string; validity_date?: string | null;
  currency?: string; price_basis?: string; submission_source?: string; status?: string;
  supplier?: DocParty; buyer?: DocParty; lines?: DocLine[]; totals?: DocTotals;
  commercial_terms?: Record<string, string | undefined>;
};

export const TABS = ['materials', 'technical', 'commercial', 'attachments', 'activity'] as const;
export type Tab = typeof TABS[number];
export const isTab = (value: unknown): value is Tab => TABS.includes(value as Tab);
export const PAGE_SIZES = [10, 20, 50] as const;

export const COMPLIANCE = ['compliant', 'partial', 'noncompliant', 'unconfirmed'] as const;
export type Compliance = typeof COMPLIANCE[number];
/** Supplier-declared compliance. Anything unrecognised is treated as unconfirmed, never as compliant. */
export const normalizeCompliance = (value?: string | null): Compliance =>
  COMPLIANCE.includes(value as Compliance) ? value as Compliance : 'unconfirmed';

const decimal = /^(-?)(\d+)(?:\.(\d+))?$/;
/** Exact display of a backend decimal string with digit grouping: no float re-pricing. */
export function formatAmount(value?: string | number | null): string | null {
  if (value == null || value === '') return null;
  const match = decimal.exec(String(value).trim());
  if (!match) return String(value);
  const whole = match[2].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${match[1]}${whole}.${(match[3] || '').padEnd(2, '0').slice(0, 2)}`;
}
export const isPositive = (value?: string | null) => !!value && decimal.test(value.trim()) && /[1-9]/.test(value) && !value.trim().startsWith('-');

/** Quantities keep their meaningful decimals only: "12.000" -> "12", "2.500" -> "2.5". */
export function formatQuantity(value?: string | number | null): string {
  if (value == null || value === '') return '—';
  const text = String(value);
  if (!decimal.test(text)) return text;
  const [whole, fraction = ''] = text.replace(/^-/, '').split('.');
  const trimmed = fraction.replace(/0+$/, '');
  return `${text.startsWith('-') ? '-' : ''}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${trimmed ? `.${trimmed}` : ''}`;
}

/** "0.1500" -> "15%"; returns null when the rate is missing. */
export function percent(rate?: string | null): string | null {
  if (rate == null || rate === '' || !decimal.test(String(rate))) return null;
  const [whole, fraction = ''] = String(rate).split('.');
  const scaled = `${whole}${fraction.padEnd(2, '0').slice(0, 2)}`.replace(/^(-?)0+(?=\d)/, '$1');
  const rest = fraction.slice(2).replace(/0+$/, '');
  return `${scaled}${rest ? `.${rest}` : ''}%`;
}

export type ReviewLine = {
  id: string; index: number; doc: DocLine; line?: QuoteLine;
  mainCategoryId: string | null; subcategoryId: string | null;
  description: string; requestedSpecs: string; requestedQuantity: string | null; requiredBrand: string;
  compliance: Compliance;
};

const text = (value: unknown) => typeof value === 'string' ? value : value == null ? '' : String(value);

/**
 * Joins document lines (what the PDF prints) with the stored quote lines and the
 * requested RFQ snapshot. Requested data prefers the snapshot taken when the
 * supplier quoted, so a later RFQ edit never rewrites what was offered against.
 */
export function reviewLines(detail?: QuoteDetail, rfqItems: RfqItem[] = []): ReviewLine[] {
  if (!detail) return [];
  const doc = (detail.document || {}) as QuoteDocument;
  const stored = new Map(detail.lines.map(line => [line.rfq_item_id, line]));
  const requested = new Map(rfqItems.map((item, index) => [item.id, { item, index }]));
  const docLines: DocLine[] = Array.isArray(doc.lines) && doc.lines.length ? doc.lines
    : detail.lines.map(line => ({ rfq_item_id: line.rfq_item_id, offered_description: line.offered_description, brand: line.brand, model: line.model,
      specification: line.offered_specs, quantity: line.quantity, unit_price: line.unit_price, lead_days: line.lead_days, compliance: line.compliance }));
  return docLines.map((docLine, index) => {
    const line = stored.get(docLine.rfq_item_id);
    const snapshot = (line?.requested_snapshot || {}) as Record<string, unknown>;
    const item = requested.get(docLine.rfq_item_id)?.item;
    return {
      id: docLine.rfq_item_id, index, doc: docLine, line,
      mainCategoryId: text(snapshot.main_category_id) || item?.main_category_id || null,
      subcategoryId: text(snapshot.subcategory_id) || item?.subcategory_id || null,
      description: docLine.description || text(snapshot.description) || item?.description || docLine.offered_description || '',
      requestedSpecs: text(snapshot.specifications) || item?.specifications || '',
      requestedQuantity: text(snapshot.quantity) || item?.quantity || null,
      requiredBrand: text(snapshot.required_brand) || item?.required_brand || '',
      compliance: normalizeCompliance(docLine.compliance ?? line?.compliance),
    };
  }).sort((a, b) => (requested.get(a.id)?.index ?? 1e9) - (requested.get(b.id)?.index ?? 1e9) || a.index - b.index);
}

/** Active requested RFQ lines that this quotation does not price. */
export const unquotedItems = (lines: ReviewLine[], rfqItems: RfqItem[] = []) => {
  const quoted = new Set(lines.map(line => line.id));
  return rfqItems.filter(item => item.is_active !== false && !quoted.has(item.id));
};

export function leadTime(lines: ReviewLine[]): { min: number; max: number } | null {
  const days = lines.map(line => line.doc.lead_days ?? line.line?.lead_days).filter((value): value is number => typeof value === 'number' && value >= 0);
  return days.length ? { min: Math.min(...days), max: Math.max(...days) } : null;
}

export function complianceCounts(lines: ReviewLine[]): Record<Compliance, number> {
  const counts: Record<Compliance, number> = { compliant: 0, partial: 0, noncompliant: 0, unconfirmed: 0 };
  for (const line of lines) counts[line.compliance] += 1;
  return counts;
}

/** Validity from the supplier's stated date only. Null when the supplier gave none. */
export function validity(date?: string | null, now = Date.now()): { date: string; daysLeft: number; expired: boolean } | null {
  if (!date) return null;
  const end = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(date) ? `${date}T23:59:59` : date);
  if (Number.isNaN(end)) return null;
  const daysLeft = Math.ceil((end - now) / 86_400_000);
  return { date, daysLeft, expired: end < now };
}

/** Distinct VAT rates actually applied (line overrides fall back to the header rate). */
export function vatRates(detail?: QuoteDetail): string[] {
  if (!detail) return [];
  const header = detail.quotation.vat_rate;
  const rates = detail.lines.length ? detail.lines.map(line => line.vat_rate ?? header) : [header];
  return [...new Set(rates.map(rate => percent(rate)).filter((value): value is string => !!value))];
}

export type Decision = 'shortlisted' | 'awarded' | 'rejected';
/**
 * Buyer decisions mirror the backend transition rules (quotations/router.py decide):
 * only submitted or shortlisted quotations accept a decision, a shortlisted quotation
 * cannot be shortlisted again, and award requires the quotation to match the current
 * RFQ revision. Duplicate awards on the same RFQ lines are still rejected server-side.
 */
export function allowedDecisions(detail?: QuoteDetail, rfqRevision?: number | null): { decisions: Decision[]; awardBlocked: string | null } {
  if (!detail) return { decisions: [], awardBlocked: null };
  const { status, review_required: reviewRequired, rfq_revision: quoteRevision } = detail.quotation;
  if (status !== 'submitted' && status !== 'shortlisted') return { decisions: [], awardBlocked: null };
  const decisions: Decision[] = status === 'submitted' ? ['shortlisted', 'awarded', 'rejected'] : ['awarded', 'rejected'];
  const stale = reviewRequired || (rfqRevision != null && quoteRevision !== rfqRevision);
  return { decisions, awardBlocked: stale ? 'revision' : null };
}

export const sourceKind = (source?: string | null): 'automatic' | 'manual' =>
  source === 'automatic' || source === 'automatic_draft' ? 'automatic' : 'manual';

export type HistoryEvent = { action: string; created_at: string; details?: Record<string, unknown> | null };

/** Paged slice for large quotations; small quotations are shown whole. */
export function pageOf<T>(rows: T[], offset: number, limit: number) {
  const safeOffset = offset >= rows.length ? Math.max(0, Math.floor((rows.length - 1) / limit) * limit) : offset;
  return { rows: rows.slice(safeOffset, safeOffset + limit), offset: safeOffset, from: rows.length ? safeOffset + 1 : 0, to: Math.min(safeOffset + limit, rows.length) };
}
