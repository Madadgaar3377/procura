import type { Schemas } from '@/lib/api/generated/schema';

export type Match = Schemas['MatchSummary'];
export const PAGE_SIZE = 25;
export const isRecommended = (match: Match) =>
  (match.status === 'eligible' || match.status === 'partial') && match.service_coverage_status !== 'not covered';
export const isSubmitted = (status?: string | null) =>
  ['submitted', 'shortlisted', 'awarded', 'rejected'].includes(status || '');

// Unknown backend reasons are deliberately not echoed: internal item markers,
// identifiers and job diagnostics do not belong in a buyer-facing explanation.
export function eligibilityReason(match: Match): string {
  const reasons = match.reasons || [];
  if (match.status === 'location_mismatch' || match.service_coverage_status === 'not covered') return 'location';
  if (match.status === 'unverified' || match.verification_status === 'unverified') return 'verification';
  if (match.status === 'pending_approval') return 'approval';
  if (match.status === 'inactive') return 'inactive';
  if (match.status === 'coverage_unknown') return 'coverage';
  if (reasons.includes('Missing required project qualifications.')) return 'qualifications';
  if (reasons.includes('Capability was rejected by administration.')) return 'rejected';
  if (match.status === 'no_capability' || reasons.some(reason => /no .*capability|material.*not found|hierarchy does not match/i.test(reason))) return 'capability';
  return 'requirements';
}

export function matchEvidence(match: Match): 'material' | 'subcategory' | 'category' | null {
  const levels = match.match_levels || [];
  const reasons = match.reasons || [];
  if (levels.includes('exact material') || reasons.some(r => r.startsWith('Approved exact material capability in '))) return 'material';
  if (levels.includes('exact subcategory') || reasons.some(r => r.startsWith('Approved exact subcategory capability in '))) return 'subcategory';
  if (reasons.some(r => r.startsWith('Approved main category capability in '))) return 'category';
  return null;
}

export function matchingState(data?: Schemas['MatchResults'], stalled = false) {
  if (!data) return { label: 'loading', busy: false, inconsistent: false };
  const busy = ['matching_queued', 'matching_in_progress'].includes(data.progress);
  const eligible = data.matches.filter(isRecommended).length;
  const inconsistent = (data.progress === 'no_eligible_suppliers' && (eligible > 0 || (data.matched_suppliers || 0) > 0))
    || (data.matched_suppliers === 0 && eligible > 0)
    || (data.progress === 'invitations_available' && data.invited_suppliers === 0);
  const label = inconsistent || (busy && stalled) ? 'review'
    : data.progress === 'matching_failed' ? 'failed'
    : data.progress === 'matching_queued' || data.progress === 'published' ? 'queued'
    : data.progress === 'matching_in_progress' ? 'processing'
    : data.progress === 'draft' ? 'draft'
    : data.progress === 'closed' ? 'closed' : 'completed';
  return { label, busy: busy && !stalled && !inconsistent, inconsistent };
}
