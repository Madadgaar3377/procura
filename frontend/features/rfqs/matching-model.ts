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
  if (match.status === 'pending_approval') return 'approval';
  if (match.status === 'inactive') return 'inactive';
  if (match.status === 'coverage_unknown') return 'coverage';
  if (reasons.includes('Missing required project qualifications.')) return 'qualifications';
  if (reasons.includes('Capability was rejected by administration.')) return 'rejected';
  if (match.status === 'no_capability' || reasons.some(reason => /no .*capability|material.*not found|hierarchy does not match/i.test(reason))) return 'capability';
  if (match.status === 'unverified' || match.verification_status === 'unverified') return 'verification';
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
