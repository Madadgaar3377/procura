import type { Schemas } from '@/lib/api/generated/schema';

export type Match = Schemas['MatchSummary'];
// Display page size only: the matching engine evaluates every supplier regardless.
export const PAGE_SIZE = 10;
export const PAGE_SIZES = [10, 20, 50] as const;
export const isRecommended = (match: Match) =>
  (match.status === 'eligible' || match.status === 'partial') && match.service_coverage_status !== 'not covered';
export const isSubmitted = (status?: string | null) =>
  ['submitted', 'shortlisted', 'awarded', 'rejected'].includes(status || '');

// Unknown backend reasons are deliberately not echoed: internal item markers,
// identifiers and job diagnostics do not belong in a buyer-facing explanation.
export function eligibilityReason(match: Match): string {
  const reasons = match.reasons || [];
  if (match.status === 'location_mismatch' || match.service_coverage_status === 'not covered') return 'location';
  if (match.status === 'location_unconfirmed' || match.status === 'coverage_unknown') return 'coverage';
  if (match.status === 'brand_mismatch') return 'brand';
  if (match.status === 'insufficient_evidence') return 'broad_category';
  if (match.status === 'pending_approval') return 'approval';
  if (match.status === 'inactive') return 'inactive';
  if (reasons.includes('Missing required project qualifications.')) return 'qualifications';
  if (reasons.includes('Capability was rejected by administration.')) return 'rejected';
  if (match.status === 'no_capability' || reasons.some(reason => /no .*capability|material.*not found|hierarchy does not match/i.test(reason))) return 'capability';
  if (match.status === 'unverified') return 'verification';
  return 'requirements';
}

export type MatchEvidence = 'material' | 'description' | 'subcategory' | 'category' | null;

export function matchEvidence(match: Match): MatchEvidence {
  // match_strength is computed by the backend from persisted evidence markers.
  if (match.match_strength === 'exact') return 'material';
  if (match.match_strength === 'product') return 'description';
  if (match.match_strength === 'capability') return 'subcategory';
  if (match.match_strength === 'broad') return 'category';
  const levels = match.match_levels || [];
  const reasons = match.reasons || [];
  if (levels.includes('exact material') || reasons.some(r => r.startsWith('Approved exact material capability in '))) return 'material';
  if (levels.includes('fuzzy description') || levels.includes('product family') || levels.includes('product alias')) return 'description';
  if (levels.includes('exact subcategory') || levels.includes('subcategory') || reasons.some(r => r.startsWith('Approved exact subcategory capability in '))) return 'subcategory';
  if (
    levels.includes('main category')
    || levels.includes('keyword/catalog match')
    || levels.includes('AI-assisted semantic match')
    || reasons.some(r => r.startsWith('Approved main category capability in ') || r.startsWith('Approved keyword/catalog match:') || r.startsWith('Approved AI-assisted semantic match:'))
  ) return 'category';
  return null;
}

export function matchingState(data?: Schemas['MatchResults'], stalled = false) {
  if (!data) return { label: 'loading', busy: false, inconsistent: false };
  const busy = ['published', 'matching_queued', 'matching_in_progress'].includes(data.progress);
  const eligible = data.matches.filter(isRecommended).length;
  const inconsistent = (data.progress === 'no_eligible_suppliers' && (eligible > 0 || (data.matched_suppliers || 0) > 0))
    || (data.matched_suppliers === 0 && eligible > 0)
    || (data.progress === 'invitations_available' && data.invited_suppliers === 0);
  const label = inconsistent || (busy && stalled) ? 'review'
    : data.progress === 'matching_failed' ? 'failed'
    : data.progress === 'matching_queued' || data.progress === 'published' ? 'queued'
    : data.progress === 'matching_in_progress' ? 'processing'
    : data.progress === 'no_eligible_suppliers' ? 'no_eligible'
    : data.progress === 'draft' ? 'draft'
    : data.progress === 'closed' ? 'closed' : 'completed';
  return { label, busy: busy && !stalled && !inconsistent, inconsistent };
}

// ---- Display state: tabs, server-side supplier queries and pagination ----

export const TABS = ['filtered', 'total', 'invitations', 'quotations', 'notifications'] as const;
export type Tab = typeof TABS[number];
export const isTab = (value: unknown): value is Tab => TABS.includes(value as Tab);

export type SupplierQuery = {
  q: string; discipline: string; main_category_id: string; subcategory_id: string;
  match_type: string; location: string; brand: string; quotation: string;
  sort: string; limit: number; offset: number;
};
export const FILTER_KEYS = ['discipline', 'main_category_id', 'subcategory_id', 'match_type', 'location', 'brand', 'quotation'] as const;
export const emptySupplierQuery = (): SupplierQuery => ({
  q: '', discipline: '', main_category_id: '', subcategory_id: '', match_type: '', location: '', brand: '', quotation: '',
  sort: 'relevance', limit: PAGE_SIZE, offset: 0,
});
export const activeFilterCount = (query: SupplierQuery) => FILTER_KEYS.filter(key => query[key]).length;

export type PageQuery = { limit: number; offset: number };
export type NotificationQuery = PageQuery & { channel: string; group: string };
export type ViewState = {
  rfq: string;
  filtered: SupplierQuery;
  total: SupplierQuery;
  invitations: PageQuery;
  quotations: PageQuery & { kind: 'automatic' | 'manual' };
  notifications: NotificationQuery;
};
export const initialViews = (rfq: string): ViewState => ({
  rfq, filtered: emptySupplierQuery(), total: emptySupplierQuery(),
  invitations: { limit: PAGE_SIZE, offset: 0 },
  quotations: { limit: PAGE_SIZE, offset: 0, kind: 'automatic' },
  notifications: { limit: PAGE_SIZE, offset: 0, channel: '', group: '' },
});

/** Server-side paged, filtered and sorted supplier results. Only non-default values are sent. */
export function supplierResultsPath(rfqId: string, view: 'filtered' | 'total', query: SupplierQuery) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (view === 'total') params.set('view', 'total');
  const q = query.q.trim();
  if (q) params.set('q', q);
  for (const key of FILTER_KEYS) {
    const value = String(query[key] || '').trim();
    if (value) params.set(key, value);
  }
  if (query.sort && query.sort !== 'relevance') params.set('sort', query.sort);
  return `matching/rfqs/${rfqId}/results?${params.toString()}`;
}

/** Invitation pages reuse the results endpoint with a single supplier row. */
export const invitationResultsPath = (rfqId: string, page: PageQuery) =>
  `matching/rfqs/${rfqId}/results?limit=1&offset=0&invitation_limit=${page.limit}&invitation_offset=${page.offset}`;

export function notificationsPath(rfqId: string, query: NotificationQuery) {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.channel) params.set('channel', query.channel);
  if (query.group) params.set('group', query.group);
  return `matching/rfqs/${rfqId}/notifications?${params.toString()}`;
}

/** 1-based inclusive display range, e.g. {from: 1, to: 10, total: 94}. */
export function pageRange(offset: number, limit: number, total: number) {
  if (!total) return { from: 0, to: 0, total: 0, page: 1, pages: 1 };
  const from = Math.min(offset + 1, total);
  return { from, to: Math.min(offset + limit, total), total, page: Math.floor(offset / limit) + 1, pages: Math.max(1, Math.ceil(total / limit)) };
}

// Buyer-facing delivery groups returned by matching/rfqs/{id}/notifications.
export const NOTIFICATION_GROUPS = ['queued', 'accepted', 'delivered', 'failed', 'bounced', 'skipped'] as const;
export const NOTIFICATION_GROUP_LABELS: Record<string, string> = {
  queued: 'Queued', accepted: 'Provider accepted', delivered: 'Delivered', failed: 'Failed', bounced: 'Bounced', skipped: 'Skipped',
};
export const DELIVERY_LABELS: Record<string, string> = {
  not_queued: 'Not sent', queued: 'Queued', sending: 'Sending', retry_scheduled: 'Retry scheduled',
  accepted: 'Accepted by provider', sent: 'Sent', delivered: 'Delivered', failed: 'Failed', hard_bounced: 'Bounced',
  soft_bounced: 'Soft bounce, retrying', suppressed: 'Address suppressed', skipped: 'Not sent', uncertain: 'Outcome uncertain',
  simulated: 'Simulated (test mode)',
};
export const deliveryLabel = (status?: string | null) => DELIVERY_LABELS[status || 'not_queued'] || status || 'Not sent';
export const FAILURE_REASON_LABELS: Record<string, string> = {
  no_contact: 'No valid contact', invalid_contact: 'Invalid contact', opted_out: 'Recipient opted out',
  suppressed: 'Address suppressed', invalid_recipient: 'Invalid recipient', duplicate_suppressed: 'Duplicate suppressed',
  invitation_no_longer_eligible: 'Invitation no longer eligible', authorization_changed: 'Authorization changed',
  eligibility_review_required: 'Eligibility review required', rfq_closed_or_expired: 'RFQ closed or expired',
  superseded_not_eligible: 'Superseded', whatsapp_not_configured: 'WhatsApp not configured',
  provider_error: 'Provider error', hard_bounced: 'Hard bounce', soft_bounced: 'Soft bounce',
};
export const failureReasonLabel = (reason?: string | null) =>
  reason ? FAILURE_REASON_LABELS[reason] || reason.replaceAll('_', ' ') : '';
